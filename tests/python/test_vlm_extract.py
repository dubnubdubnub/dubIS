"""Tests for vlm_extract: the optional local VLM (Ollama) extraction backend.

The Ollama HTTP API is mocked, so these run with no GPU, no Ollama, no model —
exactly the situation on CI and GPU-less nodes, where the backend must self-gate
to None and let the caller fall back.
"""
import json
import urllib.error

import pytest

import vlm_extract


class _FakeResp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_urlopen(monkeypatch, *, tags=None, generate=None, fail=False):
    """Patch urllib.request.urlopen to answer /api/tags and /api/generate."""
    def fake(req, timeout=None):
        if fail:
            raise urllib.error.URLError("connection refused")
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/api/tags"):
            return _FakeResp(tags if tags is not None else {"models": []})
        if url.endswith("/api/generate"):
            return _FakeResp({"response": json.dumps(generate or {"items": []})})
        raise AssertionError(f"unexpected url {url}")
    monkeypatch.setattr(vlm_extract.urllib.request, "urlopen", fake)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Default to enabled + default model/url unless a test overrides.
    monkeypatch.delenv("DUBIS_VLM_DISABLE", raising=False)
    monkeypatch.delenv("DUBIS_VLM_MODEL", raising=False)
    monkeypatch.delenv("DUBIS_OLLAMA_URL", raising=False)
    # Reset the selected-model cache so tests don't leak it between runs.
    monkeypatch.setattr(vlm_extract, "_selected_model", None)


MODELS = {"models": [{"name": "qwen2.5vl:7b"}]}
BOTH_MODELS = {"models": [{"name": "qwen2.5vl:3b"}, {"name": "qwen2.5vl:7b"}]}
ONLY_3B = {"models": [{"name": "qwen2.5vl:3b"}]}


def test_available_true_when_model_present(monkeypatch):
    _mock_urlopen(monkeypatch, tags=MODELS)
    assert vlm_extract.available() is True


def test_available_matches_model_by_base_name(monkeypatch):
    # A different tag of the same model still counts as available.
    _mock_urlopen(monkeypatch, tags={"models": [{"name": "qwen2.5vl:latest"}]})
    assert vlm_extract.available() is True


def test_prefers_7b_when_both_installed(monkeypatch):
    _mock_urlopen(monkeypatch, tags=BOTH_MODELS)
    assert vlm_extract._select_model() == "qwen2.5vl:7b"


def test_falls_back_to_3b_when_only_3b_installed(monkeypatch):
    # A low-VRAM node (e.g. 6 GB RTX 2060) pulls only the 3B → it's used with no
    # config needed.
    _mock_urlopen(monkeypatch, tags=ONLY_3B)
    assert vlm_extract._select_model() == "qwen2.5vl:3b"
    assert vlm_extract.available() is True


def test_env_override_forces_model_even_when_others_present(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_MODEL", "qwen2.5vl:3b")
    _mock_urlopen(monkeypatch, tags=BOTH_MODELS)
    assert vlm_extract._select_model() == "qwen2.5vl:3b"


def test_model_name_reflects_selection_after_extract(monkeypatch):
    _mock_urlopen(monkeypatch, tags=ONLY_3B,
                  generate={"items": [{"distributor_pn": "C1", "mfr_pn": "X", "qty": 1}]})
    vlm_extract.extract_line_items(b"img", "lcsc")
    assert vlm_extract.model_name() == "qwen2.5vl:3b"


def test_available_false_when_unreachable(monkeypatch):
    _mock_urlopen(monkeypatch, fail=True)
    assert vlm_extract.available() is False


def test_available_false_when_model_absent(monkeypatch):
    _mock_urlopen(monkeypatch, tags={"models": [{"name": "llama3:8b"}]})
    assert vlm_extract.available() is False


def test_disabled_env_forces_off(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_DISABLE", "1")
    _mock_urlopen(monkeypatch, tags=MODELS)
    assert vlm_extract.available() is False
    assert vlm_extract.extract_line_items(b"img", "lcsc") is None


def test_extract_parses_items_for_lcsc(monkeypatch):
    _mock_urlopen(monkeypatch, tags=MODELS, generate={"items": [
        {"distributor_pn": "C12624", "mfr_pn": "KT-0603G",
         "description": "Emerald Green LED", "qty": 4000},
        {"distributor_pn": "C2874885", "mfr_pn": "WS2812B-V5/W",
         "description": "LED", "qty": "1,000"},
    ]})
    rows = vlm_extract.extract_line_items(b"img", "lcsc")
    assert rows is not None and len(rows) == 2
    assert rows[0] == {
        "mpn": "KT-0603G", "manufacturer": "", "package": "",
        "description": "Emerald Green LED", "quantity": 4000,
        "unit_price": 0.0, "distributor": "lcsc", "distributor_pn": "C12624",
        "_backend": "vlm", "bbox": None,
    }
    assert rows[1]["quantity"] == 1000  # "1,000" coerced


def test_extract_generic_template_drops_distributor_pn(monkeypatch):
    _mock_urlopen(monkeypatch, tags=MODELS, generate={"items": [
        {"distributor_pn": "C12624", "mfr_pn": "KT-0603G", "qty": 10},
    ]})
    rows = vlm_extract.extract_line_items(b"img", "generic")
    assert rows[0]["distributor"] == "generic"
    assert rows[0]["distributor_pn"] == ""  # not a distributor template
    assert rows[0]["mpn"] == "KT-0603G"


def test_extract_returns_none_when_unavailable(monkeypatch):
    _mock_urlopen(monkeypatch, fail=True)
    assert vlm_extract.extract_line_items(b"img", "lcsc") is None


def test_extract_returns_none_on_empty_items(monkeypatch):
    _mock_urlopen(monkeypatch, tags=MODELS, generate={"items": []})
    assert vlm_extract.extract_line_items(b"img", "lcsc") is None


def test_extract_handles_bare_list_response(monkeypatch):
    # Some models emit a bare JSON array rather than {"items": [...]}.
    _mock_urlopen(monkeypatch, tags=MODELS,
                  generate=[{"distributor_pn": "C1", "mfr_pn": "X", "qty": 5}])
    rows = vlm_extract.extract_line_items(b"img", "lcsc")
    assert rows and rows[0]["distributor_pn"] == "C1"


def test_extract_drops_rows_without_pn_or_mpn(monkeypatch):
    _mock_urlopen(monkeypatch, tags=MODELS, generate={"items": [
        {"distributor_pn": "", "mfr_pn": "", "qty": 5},
        {"distributor_pn": "C9", "mfr_pn": "", "qty": 5},
    ]})
    rows = vlm_extract.extract_line_items(b"img", "lcsc")
    assert len(rows) == 1 and rows[0]["distributor_pn"] == "C9"


def test_to_line_item_tags_backend_and_parses_bbox():
    raw = {"distributor_pn": "C12345", "mfr_pn": "RC0402", "description": "10k",
           "qty": 100, "bbox": [100, 200, 300, 250]}  # 0..1000 normalized
    item = vlm_extract._to_line_item(raw, "lcsc", 1000, 2000)
    assert item["_backend"] == "vlm"
    assert item["distributor_pn"] == "C12345"
    # 0..1000 grid -> pixels: x=100/1000*1000=100, y=200/1000*2000=400,
    # w=(300-100)/1000*1000=200, h=(250-200)/1000*2000=100
    assert item["bbox"] == [100, 400, 200, 100]


def test_to_line_item_missing_bbox_is_none():
    raw = {"mfr_pn": "RC0402", "qty": 5}
    item = vlm_extract._to_line_item(raw, "generic", 1000, 1000)
    assert item["_backend"] == "vlm"
    assert item["bbox"] is None


def test_to_line_item_malformed_bbox_is_none_not_raise():
    raw = {"mfr_pn": "RC0402", "qty": 5, "bbox": ["x", 1, 2]}
    item = vlm_extract._to_line_item(raw, "generic", 1000, 1000)
    assert item["bbox"] is None


def test_prompt_for_includes_distributor_hint():
    p = vlm_extract._prompt_for("lcsc")
    assert "LCSC" in p
    assert "C" in p  # mentions the C<digits> PN format
    assert "bbox" in p  # still asks for the box


def test_prompt_for_generic_has_no_distributor_hint():
    p = vlm_extract._prompt_for("generic")
    assert "LCSC packing list" not in p
    assert "DigiKey packing list" not in p
