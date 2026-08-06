"""Tests for vlm_extract: the optional local VLM extraction backend.

The backend talks the OpenAI-compatible surface (``/v1/models`` +
``/v1/chat/completions``), which is mocked here — so these run with no GPU, no
model server, no model: exactly the situation on CI and GPU-less nodes, where the
backend must self-gate to None and let the caller fall back.

Model ids appear in two spellings on purpose. The cluster's llama.cpp server
reports its ``--alias`` (``qwen2.5-vl-3b``); other ``/v1`` servers report a
tag-style id (``qwen2.5vl:3b``). Either spelling must be matched, so both are
exercised.
"""
import base64
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


def _chat_reply(content):
    """A minimal OpenAI chat-completions envelope around a JSON string body."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _mock_urlopen(monkeypatch, *, models=None, generate=None, fail=False):
    """Patch urlopen to answer /v1/models and /v1/chat/completions.

    Captures each chat request body on the returned dict so tests can assert on
    the wire format (message shape, data URI, response_format).
    """
    captured = {}

    def fake(req, timeout=None):
        if fail:
            raise urllib.error.URLError("connection refused")
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/v1/models"):
            return _FakeResp(models if models is not None else {"data": []})
        if url.endswith("/v1/chat/completions"):
            captured["request"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(_chat_reply(json.dumps(
                generate if generate is not None else {"items": []})))
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(vlm_extract.urllib.request, "urlopen", fake)
    return captured


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Default to enabled + default model/url unless a test overrides.
    monkeypatch.delenv("DUBIS_VLM_DISABLE", raising=False)
    monkeypatch.delenv("DUBIS_VLM_MODEL", raising=False)
    monkeypatch.delenv("DUBIS_VLM_URL", raising=False)
    # Reset the selected-model cache so tests don't leak it between runs.
    monkeypatch.setattr(vlm_extract, "_selected_model", None)


def _models(*ids):
    return {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}


MODELS = _models("qwen2.5-vl-7b")
BOTH_MODELS = _models("qwen2.5-vl-3b", "qwen2.5-vl-7b")
ONLY_3B = _models("qwen2.5-vl-3b")
# What the cluster actually serves: llama-server's --alias.
CLUSTER = _models("qwen2.5-vl-3b")
# What a server that names its models by tag (rather than --alias) serves.
TAG_STYLE_BOTH = _models("qwen2.5vl:3b", "qwen2.5vl:7b")

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


# ── URL / env plumbing ──────────────────────────────────────────────────

def test_default_url_is_llama_server_port():
    assert vlm_extract._base_url() == "http://127.0.0.1:8080"


def test_vlm_url_env_wins(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_URL", "http://llamacpp.win-runners.svc.cluster.local:8080/")
    assert vlm_extract._base_url() == "http://llamacpp.win-runners.svc.cluster.local:8080"


def test_blank_vlm_url_falls_back_to_default(monkeypatch):
    # An empty value must not produce a bare "" base URL.
    monkeypatch.setenv("DUBIS_VLM_URL", "")
    assert vlm_extract._base_url() == "http://127.0.0.1:8080"


# ── model selection ─────────────────────────────────────────────────────

def test_available_true_when_model_present(monkeypatch):
    _mock_urlopen(monkeypatch, models=MODELS)
    assert vlm_extract.available() is True


def test_available_matches_model_by_base_name(monkeypatch):
    # A llama-server started without --alias reports the .gguf path; it still counts.
    _mock_urlopen(monkeypatch, models=_models("/models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"))
    assert vlm_extract.available() is True


def test_available_matches_tag_style_id(monkeypatch):
    # A server that names models by tag rather than --alias still matches.
    _mock_urlopen(monkeypatch, models=_models("qwen2.5vl:latest"))
    assert vlm_extract.available() is True


def test_prefers_7b_when_both_served(monkeypatch):
    _mock_urlopen(monkeypatch, models=BOTH_MODELS)
    assert vlm_extract._select_model() == "qwen2.5-vl-7b"


def test_prefers_7b_when_both_served_with_tag_style_ids(monkeypatch):
    _mock_urlopen(monkeypatch, models=TAG_STYLE_BOTH)
    assert vlm_extract._select_model() == "qwen2.5vl:7b"


def test_falls_back_to_3b_when_only_3b_served(monkeypatch):
    # A low-VRAM node (the cluster's 6 GB RTX 2060) serves only the 3B → it's
    # used with no config needed.
    _mock_urlopen(monkeypatch, models=CLUSTER)
    assert vlm_extract._select_model() == "qwen2.5-vl-3b"
    assert vlm_extract.available() is True


def test_env_override_forces_model_even_when_others_present(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_MODEL", "qwen2.5-vl-3b")
    _mock_urlopen(monkeypatch, models=BOTH_MODELS)
    assert vlm_extract._select_model() == "qwen2.5-vl-3b"


def test_model_name_reflects_selection_after_extract(monkeypatch):
    _mock_urlopen(monkeypatch, models=ONLY_3B,
                  generate={"items": [{"distributor_pn": "C1", "mfr_pn": "X", "qty": 1}]})
    vlm_extract.extract_line_items(PNG, "lcsc")
    assert vlm_extract.model_name() == "qwen2.5-vl-3b"


def test_available_false_when_unreachable(monkeypatch):
    _mock_urlopen(monkeypatch, fail=True)
    assert vlm_extract.available() is False


def test_available_false_when_model_absent(monkeypatch):
    # A text-only server must NOT be treated as a vision backend.
    _mock_urlopen(monkeypatch, models=_models("llama3:8b"))
    assert vlm_extract.available() is False


def test_available_false_when_no_models_served(monkeypatch):
    _mock_urlopen(monkeypatch, models={"object": "list", "data": []})
    assert vlm_extract.available() is False


def test_disabled_env_forces_off(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_DISABLE", "1")
    _mock_urlopen(monkeypatch, models=MODELS)
    assert vlm_extract.available() is False
    assert vlm_extract.extract_line_items(PNG, "lcsc") is None


# ── request wire format ─────────────────────────────────────────────────

def test_request_is_openai_chat_with_image_data_uri(monkeypatch):
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate={
        "items": [{"distributor_pn": "C1", "mfr_pn": "X", "qty": 1}]})
    vlm_extract.extract_line_items(PNG, "lcsc")
    req = captured["request"]
    assert req["model"] == "qwen2.5-vl-3b"
    assert req["response_format"] == {"type": "json_object"}
    assert req["temperature"] == 0
    assert req["stream"] is False
    assert req["max_tokens"] > 0
    parts = req["messages"][0]["content"]
    text = next(p for p in parts if p["type"] == "text")
    image = next(p for p in parts if p["type"] == "image_url")
    assert "LCSC" in text["text"]
    expected = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    assert image["image_url"]["url"] == expected


def test_jpeg_input_gets_a_jpeg_data_uri(monkeypatch):
    jpeg = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body"
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate={
        "items": [{"mfr_pn": "X", "qty": 1}]})
    vlm_extract.extract_line_items(jpeg, "generic")
    parts = captured["request"]["messages"][0]["content"]
    image = next(p for p in parts if p["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ── response parsing ────────────────────────────────────────────────────

def test_extract_parses_items_for_lcsc(monkeypatch):
    _mock_urlopen(monkeypatch, models=MODELS, generate={"items": [
        {"distributor_pn": "C12624", "mfr_pn": "KT-0603G",
         "description": "Emerald Green LED", "qty": 4000},
        {"distributor_pn": "C2874885", "mfr_pn": "WS2812B-V5/W",
         "description": "LED", "qty": "1,000"},
    ]})
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows is not None and len(rows) == 2
    assert rows[0] == {
        "mpn": "KT-0603G", "manufacturer": "", "package": "",
        "description": "Emerald Green LED", "quantity": 4000,
        "unit_price": 0.0, "distributor": "lcsc", "distributor_pn": "C12624",
        "_backend": "vlm", "bbox": None,
    }
    assert rows[1]["quantity"] == 1000  # "1,000" coerced


def test_extract_generic_template_drops_distributor_pn(monkeypatch):
    _mock_urlopen(monkeypatch, models=MODELS, generate={"items": [
        {"distributor_pn": "C12624", "mfr_pn": "KT-0603G", "qty": 10},
    ]})
    rows = vlm_extract.extract_line_items(PNG, "generic")
    assert rows[0]["distributor"] == "generic"
    assert rows[0]["distributor_pn"] == ""  # not a distributor template
    assert rows[0]["mpn"] == "KT-0603G"


def test_extract_returns_none_when_unavailable(monkeypatch):
    _mock_urlopen(monkeypatch, fail=True)
    assert vlm_extract.extract_line_items(PNG, "lcsc") is None


def test_extract_returns_none_on_empty_items(monkeypatch):
    _mock_urlopen(monkeypatch, models=MODELS, generate={"items": []})
    assert vlm_extract.extract_line_items(PNG, "lcsc") is None


def test_extract_handles_bare_list_response(monkeypatch):
    # Some models emit a bare JSON array rather than {"items": [...]}.
    _mock_urlopen(monkeypatch, models=MODELS,
                  generate=[{"distributor_pn": "C1", "mfr_pn": "X", "qty": 5}])
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows and rows[0]["distributor_pn"] == "C1"


def test_extract_returns_none_on_malformed_envelope(monkeypatch):
    # A server that answers 200 with no choices must fall back, not raise.
    def fake(req, timeout=None):
        url = req.full_url
        if url.endswith("/v1/models"):
            return _FakeResp(MODELS)
        return _FakeResp({"choices": []})
    monkeypatch.setattr(vlm_extract.urllib.request, "urlopen", fake)
    assert vlm_extract.extract_line_items(PNG, "lcsc") is None


def test_extract_returns_none_on_non_json_content(monkeypatch):
    def fake(req, timeout=None):
        url = req.full_url
        if url.endswith("/v1/models"):
            return _FakeResp(MODELS)
        return _FakeResp(_chat_reply("I'm sorry, I can't read that image."))
    monkeypatch.setattr(vlm_extract.urllib.request, "urlopen", fake)
    assert vlm_extract.extract_line_items(PNG, "lcsc") is None


def test_extract_drops_rows_without_pn_or_mpn(monkeypatch):
    _mock_urlopen(monkeypatch, models=MODELS, generate={"items": [
        {"distributor_pn": "", "mfr_pn": "", "qty": 5},
        {"distributor_pn": "C9", "mfr_pn": "", "qty": 5},
    ]})
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
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
