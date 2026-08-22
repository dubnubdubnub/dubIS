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


def _mock_urlopen(monkeypatch, *, models=None, generate=None, content=None, fail=False):
    """Patch urlopen to answer /v1/models and /v1/chat/completions.

    ``generate`` is JSON-encoded into the reply content (the tidy case);
    ``content`` is sent as the content string *verbatim*, which is how the real
    llama.cpp replies are reproduced (markdown fences and all).

    Captures each chat request body on the returned dict so tests can assert on
    the wire format (message shape, data URI, response_format), plus every URL
    and Request object seen — that is how the endpoint/token plumbing is checked
    (which server was dialled, and what Authorization header, if any, went out).
    """
    captured = {"urls": [], "requests": []}

    def fake(req, timeout=None):
        if fail:
            raise urllib.error.URLError("connection refused")
        url = req.full_url if hasattr(req, "full_url") else str(req)
        captured["urls"].append(url)
        captured["requests"].append(req)
        if url.endswith("/v1/models"):
            return _FakeResp(models if models is not None else {"data": []})
        if url.endswith("/v1/chat/completions"):
            captured["request"] = json.loads(req.data.decode("utf-8"))
            body = content if content is not None else json.dumps(
                generate if generate is not None else {"items": []})
            return _FakeResp(_chat_reply(body))
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

# The *verbatim* content string a live llama.cpp server (Qwen2.5-VL-3B, alias
# ``qwen2.5-vl-3b``) returned for a packing-list image: a ```json fence wrapping
# a top-level array, despite response_format={"type":"json_object"}. Kept exactly
# as captured — this is the reply that used to kill the whole VLM path.
CAPTURED_FENCED_ARRAY = (
    '```json\n'
    '[\n'
    '    {\n'
    '        "distributor_pn": "C12624",\n'
    '        "mfr_pn": "CC0402KRX7R7/BB104",\n'
    '        "description": "100nF 50V X7R 0402 Capacitor",\n'
    '        "qty": 500,\n'
    '        "bbox": [10, 100, 100, 110]\n'
    '    },\n'
    '    {\n'
    '        "distributor_pn": "C25804",\n'
    '        "mfr_pn": "RC0402FR-0710KL",\n'
    '        "description": "10K 1% 0402 Resistor",\n'
    '        "qty": 1000,\n'
    '        "bbox": [10, 120, 100, 130]\n'
    '    }\n'
    ']\n'
    '```'
)


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


# ── explicit endpoint + bearer token ────────────────────────────────────
# A caller may need to target one specific server rather than whatever the
# environment says: a locally-spawned llama-server on an ephemeral port, or a
# leased fleet node that wants an Authorization header. Both are per-call, so
# they are parameters, not env — while the env path must keep working untouched
# for every caller that passes neither.

ROWS = {"items": [{"distributor_pn": "C1", "mfr_pn": "X", "qty": 1}]}


def test_explicit_endpoint_overrides_env(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_URL", "http://from-env:8080")
    assert vlm_extract._base_url("http://127.0.0.1:53311/") == "http://127.0.0.1:53311"


def test_blank_endpoint_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_URL", "http://from-env:8080")
    assert vlm_extract._base_url("") == "http://from-env:8080"
    assert vlm_extract._base_url(None) == "http://from-env:8080"


def test_endpoint_targets_that_server_for_probe_and_inference(monkeypatch):
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate=ROWS)
    rows = vlm_extract.extract_line_items(PNG, "lcsc", endpoint="http://127.0.0.1:53311")
    assert rows and rows[0]["distributor_pn"] == "C1"
    assert captured["urls"] == ["http://127.0.0.1:53311/v1/models",
                                "http://127.0.0.1:53311/v1/chat/completions"]


def test_env_url_used_when_no_endpoint_passed(monkeypatch):
    # Regression: the env path is unchanged for every existing caller.
    monkeypatch.setenv("DUBIS_VLM_URL", "http://llamacpp.win-runners.svc.cluster.local:8080")
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate=ROWS)
    assert vlm_extract.extract_line_items(PNG, "lcsc")
    assert captured["urls"] == [
        "http://llamacpp.win-runners.svc.cluster.local:8080/v1/models",
        "http://llamacpp.win-runners.svc.cluster.local:8080/v1/chat/completions"]


def test_token_sends_bearer_header_on_every_request(monkeypatch):
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate=ROWS)
    assert vlm_extract.extract_line_items(PNG, "lcsc", endpoint="http://node:8080",
                                          token="sekrit")
    # Both the /v1/models probe and the inference call must authenticate — a
    # server that gates one gates the other.
    assert [r.get_header("Authorization") for r in captured["requests"]] == [
        "Bearer sekrit", "Bearer sekrit"]


def test_no_token_sends_no_auth_header(monkeypatch):
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate=ROWS)
    assert vlm_extract.extract_line_items(PNG, "lcsc", endpoint="http://node:8080")
    assert [r.get_header("Authorization") for r in captured["requests"]] == [None, None]
    # …and nothing auth-shaped sneaks in under another name.
    for req in captured["requests"]:
        assert not [k for k in req.headers if "auth" in k.lower()]


def test_blank_token_sends_no_auth_header(monkeypatch):
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate=ROWS)
    assert vlm_extract.extract_line_items(PNG, "lcsc", endpoint="http://node:8080", token="")
    assert [r.get_header("Authorization") for r in captured["requests"]] == [None, None]


def test_content_type_still_set_on_the_chat_request(monkeypatch):
    # Adding auth must not displace the headers the request already needed.
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate=ROWS)
    vlm_extract.extract_line_items(PNG, "lcsc", endpoint="http://node:8080", token="t")
    chat = captured["requests"][-1]
    assert chat.get_header("Content-type") == "application/json"


def test_available_accepts_endpoint_and_token(monkeypatch):
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B)
    assert vlm_extract.available(endpoint="http://node:8080", token="tok") is True
    assert captured["urls"] == ["http://node:8080/v1/models"]
    assert captured["requests"][0].get_header("Authorization") == "Bearer tok"


def test_available_false_when_explicit_endpoint_is_unreachable(monkeypatch):
    _mock_urlopen(monkeypatch, fail=True)
    assert vlm_extract.available(endpoint="http://nothing-here:8080") is False


def test_model_name_reports_the_model_of_the_last_endpoint_probed(monkeypatch):
    # One cache slot, holding whatever the most recent successful probe/extraction
    # selected — on whatever endpoint. The only consumer logs it immediately
    # after the call it describes ("OCR backend: local VLM (%s)"), so "the last
    # one that answered" is exactly the right answer, and no per-endpoint cache
    # is needed (model_name() takes no endpoint to key one by).
    _mock_urlopen(monkeypatch, models=ONLY_3B)
    assert vlm_extract.available(endpoint="http://small-node:8080") is True
    assert vlm_extract.model_name() == "qwen2.5-vl-3b"
    _mock_urlopen(monkeypatch, models=_models("qwen2.5-vl-7b"))
    assert vlm_extract.available(endpoint="http://big-node:8080") is True
    assert vlm_extract.model_name() == "qwen2.5-vl-7b"


def test_disable_env_still_wins_over_an_explicit_endpoint(monkeypatch):
    # The kill switch is absolute: an endpoint argument must not reopen the path.
    monkeypatch.setenv("DUBIS_VLM_DISABLE", "1")
    captured = _mock_urlopen(monkeypatch, models=ONLY_3B, generate=ROWS)
    assert vlm_extract.available(endpoint="http://node:8080", token="t") is False
    assert vlm_extract.extract_line_items(PNG, "lcsc", endpoint="http://node:8080") is None
    assert captured["urls"] == []


def test_model_env_override_still_applies_to_an_explicit_endpoint(monkeypatch):
    monkeypatch.setenv("DUBIS_VLM_MODEL", "qwen2.5-vl-3b")
    captured = _mock_urlopen(monkeypatch, models=BOTH_MODELS, generate=ROWS)
    vlm_extract.extract_line_items(PNG, "lcsc", endpoint="http://node:8080")
    assert captured["request"]["model"] == "qwen2.5-vl-3b"


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


def test_model_name_reflects_selection_after_available_probe(monkeypatch):
    # available() must record what the server actually serves, not leave
    # model_name() guessing the 7B off the top of the preference list — that
    # guess is what "OCR backend: local VLM (%s)" logs.
    _mock_urlopen(monkeypatch, models=ONLY_3B)
    assert vlm_extract.available() is True
    assert vlm_extract.model_name() == "qwen2.5-vl-3b"


def test_model_name_is_a_guess_before_any_probe(monkeypatch):
    # Nothing probed yet → the documented best guess (head of the preference list).
    assert vlm_extract.model_name() == "qwen2.5-vl-7b"


def test_failed_probe_does_not_overwrite_a_recorded_model(monkeypatch):
    _mock_urlopen(monkeypatch, models=ONLY_3B)
    assert vlm_extract.available() is True
    _mock_urlopen(monkeypatch, fail=True)
    assert vlm_extract.available() is False
    # A later failure must not erase what we know was loaded (nor re-guess the 7B).
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


# ── markdown-fenced replies ─────────────────────────────────────────────
# llama.cpp does NOT reliably honour response_format={"type":"json_object"}: it
# answers with a ```json fence around the JSON. Every fence shape below has to
# parse, or the VLM path silently degrades to Tesseract.

def test_extract_parses_captured_live_fenced_array(monkeypatch):
    # The exact content string captured from the live 3B server.
    _mock_urlopen(monkeypatch, models=ONLY_3B, content=CAPTURED_FENCED_ARRAY)
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows is not None and len(rows) == 2
    assert [r["distributor_pn"] for r in rows] == ["C12624", "C25804"]
    assert [r["mpn"] for r in rows] == ["CC0402KRX7R7/BB104", "RC0402FR-0710KL"]
    assert [r["quantity"] for r in rows] == [500, 1000]


def test_extract_parses_bare_fence(monkeypatch):
    # A ``` fence with no language tag.
    _mock_urlopen(monkeypatch, models=ONLY_3B, content=(
        '```\n{"items": [{"distributor_pn": "C9", "mfr_pn": "RC0402", "qty": 25}]}\n```'))
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows and rows[0]["distributor_pn"] == "C9" and rows[0]["quantity"] == 25


def test_extract_parses_fence_with_surrounding_whitespace(monkeypatch):
    _mock_urlopen(monkeypatch, models=ONLY_3B, content=(
        '\n\n  ```json\n{"items": [{"mfr_pn": "RC0402", "qty": 7}]}\n```  \n\n'))
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows and rows[0]["mpn"] == "RC0402" and rows[0]["quantity"] == 7


def test_extract_parses_inline_fence(monkeypatch):
    # Fence opened and closed on one line, so there is no newline to split the
    # language tag off. Splitting on the newline alone leaves "json" glued to the
    # JSON and the parse fails — the tag has to come off with the fence itself.
    _mock_urlopen(monkeypatch, models=ONLY_3B, content=(
        '```json [{"distributor_pn": "C5", "mfr_pn": "Y", "qty": 12}]```'))
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows and rows[0]["distributor_pn"] == "C5" and rows[0]["quantity"] == 12


def test_extract_parses_fence_with_no_closing_fence(monkeypatch):
    # Truncated at max_tokens right after the JSON: the opening fence is there,
    # the closing one never arrived.
    _mock_urlopen(monkeypatch, models=ONLY_3B, content=(
        '```json\n[{"distributor_pn": "C7", "mfr_pn": "X", "qty": 3}]\n'))
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows and rows[0]["distributor_pn"] == "C7"


def test_extract_parses_unfenced_object_with_items(monkeypatch):
    # The tidy case a well-behaved /v1 server gives — must not regress.
    _mock_urlopen(monkeypatch, models=ONLY_3B, content=(
        '{"items": [{"distributor_pn": "C5", "mfr_pn": "Y", "qty": 12}]}'))
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows and rows[0]["distributor_pn"] == "C5" and rows[0]["quantity"] == 12


def test_extract_parses_unfenced_bare_array(monkeypatch):
    _mock_urlopen(monkeypatch, models=ONLY_3B, content=(
        '[{"distributor_pn": "C6", "mfr_pn": "Z", "qty": 2}]'))
    rows = vlm_extract.extract_line_items(PNG, "lcsc")
    assert rows and rows[0]["distributor_pn"] == "C6"


def test_extract_returns_none_on_fenced_non_json(monkeypatch):
    # Stripping the fence must not turn prose into a parse: still fall back.
    _mock_urlopen(monkeypatch, models=ONLY_3B,
                  content="```\nI can't read that image.\n```")
    assert vlm_extract.extract_line_items(PNG, "lcsc") is None


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
