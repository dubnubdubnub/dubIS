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
    monkeypatch.delenv("DUBIS_OLLAMA_URL", raising=False)
    # Pin the model so the Ollama-mock tests are hardware-independent — otherwise
    # _model() auto-selects from this machine's real GPU VRAM. The auto-selection
    # tests delenv this to exercise the real VRAM-based path.
    monkeypatch.setenv("DUBIS_VLM_MODEL", "qwen2.5vl:7b")


MODELS = {"models": [{"name": "qwen2.5vl:7b"}]}


def test_available_true_when_model_present(monkeypatch):
    _mock_urlopen(monkeypatch, tags=MODELS)
    assert vlm_extract.available() is True


def test_available_matches_model_by_base_name(monkeypatch):
    # A different tag of the same model still counts as available.
    _mock_urlopen(monkeypatch, tags={"models": [{"name": "qwen2.5vl:latest"}]})
    assert vlm_extract.available() is True


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


# ── Auto model-selection by GPU VRAM ─────────────────────────────────────
#   _model() picks the largest qwen2.5vl tag the detected VRAM can hold, so a
#   node "just works" without per-node DUBIS_VLM_MODEL tuning. Explicit env
#   still wins. VRAM is read via _detect_vram_mb() (nvidia-smi), mocked here.

def test_model_env_var_overrides_autoselect(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_MODEL", "custom:tag")
    monkeypatch.setattr(vlm_extract, "_detect_vram_mb", lambda: 6144)
    assert vlm_extract._model() == "custom:tag"


def test_autoselect_3b_for_6gb_gpu(monkeypatch):
    # RTX 2060 Mobile = 6 GB → 7b would OOM, so pick 3b.
    monkeypatch.delenv("DUBIS_VLM_MODEL", raising=False)
    monkeypatch.setattr(vlm_extract, "_detect_vram_mb", lambda: 6144)
    assert vlm_extract._model() == "qwen2.5vl:3b"


def test_autoselect_7b_for_12gb_gpu(monkeypatch):
    monkeypatch.delenv("DUBIS_VLM_MODEL", raising=False)
    monkeypatch.setattr(vlm_extract, "_detect_vram_mb", lambda: 12288)
    assert vlm_extract._model() == "qwen2.5vl:7b"


def test_autoselect_32b_for_24gb_gpu(monkeypatch):
    monkeypatch.delenv("DUBIS_VLM_MODEL", raising=False)
    monkeypatch.setattr(vlm_extract, "_detect_vram_mb", lambda: 24576)
    assert vlm_extract._model() == "qwen2.5vl:32b"


def test_autoselect_falls_back_to_default_without_gpu(monkeypatch):
    # No GPU / nvidia-smi absent → keep the conservative default; Ollama
    # availability gating handles the GPU-less node anyway.
    monkeypatch.delenv("DUBIS_VLM_MODEL", raising=False)
    monkeypatch.setattr(vlm_extract, "_detect_vram_mb", lambda: None)
    assert vlm_extract._model() == vlm_extract._DEFAULT_MODEL


def test_detect_vram_returns_none_when_nvidia_smi_absent(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("nvidia-smi not found")
    monkeypatch.setattr(vlm_extract.subprocess, "run", boom)
    assert vlm_extract._detect_vram_mb() is None


def test_detect_vram_parses_nvidia_smi_output(monkeypatch):
    class _Result:
        returncode = 0
        stdout = "6144\n"
    monkeypatch.setattr(vlm_extract.subprocess, "run", lambda *a, **k: _Result())
    assert vlm_extract._detect_vram_mb() == 6144
