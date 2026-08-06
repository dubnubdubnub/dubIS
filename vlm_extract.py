"""Optional local vision-language-model backend for packing-list extraction.

A strong VLM reads photographed packing lists holistically — including faint
print, folds, and perspective that defeat classical OCR + table detection. We run
one entirely locally (llama.cpp's ``llama-server``, or anything else speaking the
same API) so nothing leaves the machine — the documents carry PII. This is the
PREFERRED extractor when a capable server is reachable; otherwise
``extract_line_items`` returns ``None`` and the caller falls back to the Tesseract
grid/flat pipeline — so GPU-less nodes (and CI) are unaffected.

Backend contract: the **OpenAI-compatible** surface — ``GET /v1/models`` to see
what is loaded, ``POST /v1/chat/completions`` with an ``image_url`` data URI for
inference. That is deliberately the lowest common denominator: any server
speaking ``/v1`` works (llama.cpp, vLLM, LM Studio, …), so the same code runs
against the cluster's llama.cpp server (the CI ``gpu`` runner) and against a
developer's own local server with nothing but a URL change.

Configuration (per-node, via environment):
    DUBIS_VLM_URL      base URL (default http://127.0.0.1:8080, llama-server's).
                       Point it at whatever host/port your ``/v1`` server listens
                       on, including another node's GPU.
    DUBIS_VLM_MODEL    model id to request, overriding the auto-pick. llama.cpp
                       serves exactly one model, named by its --alias.
    DUBIS_VLM_DISABLE  set to any non-empty value to force the backend off.

Public API:
    available() -> bool
        Fast check: server reachable AND serving a usable vision model.
    extract_line_items(image_bytes, template="generic") -> list[dict] | None
        Line-item dicts share the distributor_profiles shape (mpn, manufacturer,
        package, description, quantity, unit_price, distributor, distributor_pn).
        Returns None on any unavailability/error so the caller can fall back.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# llama-server's default port. (Any other /v1 server is reachable by setting
# DUBIS_VLM_URL explicitly.)
_DEFAULT_URL = "http://127.0.0.1:8080"
# Model ids to try, best first. The 7B reads faint/folded LCSC C-numbers reliably
# but needs ~9 GB VRAM at Q4 with its vision projector; the 3B fits smaller GPUs
# (e.g. the cluster's 6 GB RTX 2060, which serves exactly this) — it still gets
# MPNs + quantities but may miss faint C-numbers. We pick the best one the server
# actually reports, so a low-VRAM node just serves the 3B with no config.
# DUBIS_VLM_MODEL overrides this list entirely.
# Both spellings are listed because the id is backend-chosen: llama.cpp reports
# its --alias (`qwen2.5-vl-3b`), while tag-style servers report `qwen2.5vl:3b`.
_PREFERRED_MODELS = ["qwen2.5-vl-7b", "qwen2.5vl:7b", "qwen2.5-vl-3b", "qwen2.5vl:3b"]
# Substrings that mark a served id as a Qwen2.5-VL variant, whatever the backend
# called it (a llama.cpp deploy with no --alias reports the .gguf *path*).
_QWEN_MARKERS = ("qwen2.5-vl", "qwen2.5vl", "qwen2_5_vl")
_selected_model = None  # cache of the model chosen on the last successful probe
# Short timeout for the reachability probe so a node without a VLM server falls
# back almost instantly; generous timeout for the actual (GPU) inference call.
_PROBE_TIMEOUT = 1.5
_INFER_TIMEOUT = 600
# Bound the reply. Left unset, llama.cpp will happily generate to the end of the
# context window on a degenerate loop and burn the CI leg's 15-minute budget.
_MAX_TOKENS = 4096

_PROMPT = (
    "This image is a distributor packing list / shipping manifest. Extract EVERY "
    "row of the goods table as JSON under the key \"items\". For each row output: "
    "{\"distributor_pn\": the distributor catalogue part number (LCSC numbers start "
    "with 'C' then digits; DigiKey numbers usually end in '-ND'), "
    "\"mfr_pn\": the manufacturer part number (the 'Mfr. Part#'/'MFG#' value), "
    "\"description\": the goods description, "
    "\"qty\": the ordered quantity as an integer, "
    "\"bbox\": the bounding box of the row in the image as [x0, y0, x1, y1] "
    "integers on a 0-1000 normalized grid (x right, y down). "
    "} "
    "Read carefully even where the print is faint or the page is folded. Use empty "
    "string for a field you cannot read. Output only the JSON object."
)

_TEMPLATE_HINTS = {
    "lcsc": "This is an LCSC packing list; its catalogue part numbers look like "
            "C followed by digits (e.g. C12345). Put them in distributor_pn.",
    "digikey": "This is a DigiKey packing list; its catalogue part numbers "
               "usually end in -ND/-CT/-DKR. Put them in distributor_pn.",
    "mouser": "This is a Mouser packing list; its catalogue part numbers look "
              "like <digits>-<mfr part>. Put them in distributor_pn.",
    "pololu": "This is a Pololu packing list; its catalogue part numbers are "
              "bare numbers. Put them in distributor_pn.",
}


def _prompt_for(template: str) -> str:
    hint = _TEMPLATE_HINTS.get((template or "").strip().lower())
    return f"{_PROMPT}\n{hint}" if hint else _PROMPT


def _base_url() -> str:
    return (os.environ.get("DUBIS_VLM_URL") or _DEFAULT_URL).rstrip("/")


def _preferred_ids() -> list[str]:
    """Model ids to try, best first. An explicit DUBIS_VLM_MODEL overrides the list."""
    env = (os.environ.get("DUBIS_VLM_MODEL") or "").strip()
    return [env] if env else list(_PREFERRED_MODELS)


def _disabled() -> bool:
    return bool(os.environ.get("DUBIS_VLM_DISABLE"))


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _served_models():
    """Model ids the server reports at /v1/models, or None if it's unreachable."""
    try:
        body = _get_json(f"{_base_url()}/v1/models", _PROBE_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("VLM backend unavailable (probe failed): %s", exc)
        return None
    return {str(m.get("id") or "") for m in (body.get("data") or [])}


def _is_qwen_vl(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(marker in lowered for marker in _QWEN_MARKERS)


def _select_model():
    """Best served model: the first preferred id (7B over 3B), then any served
    Qwen2.5-VL variant. None if none are served / the server is down."""
    served = _served_models()
    if not served:
        return None
    for wanted in _preferred_ids():
        if wanted in served:
            return wanted
        # An id given without its tag/quant suffix still matches, and so does a
        # bare alias against a served .gguf path.
        match = next((s for s in sorted(served) if wanted.lower() in s.lower()), None)
        if match:
            return match
    return next((s for s in sorted(served) if _is_qwen_vl(s)), None)


def model_name() -> str:
    """The VLM model last selected (for logging); a best guess before any run."""
    return _selected_model or _preferred_ids()[0]


def available() -> bool:
    """True if enabled and a usable VLM model is served by a reachable server."""
    if _disabled():
        return False
    return _select_model() is not None


def extract_line_items(image_bytes: bytes, template: str = "generic",
                       page_w: int = 0, page_h: int = 0):
    """Extract line items via the local VLM, or None if unavailable/failed."""
    if _disabled() or not image_bytes:
        return None
    try:
        return _extract(image_bytes, template, page_w, page_h)
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
        logger.warning("VLM extraction failed, falling back: %s", exc)
        return None


def _image_mime(image_bytes: bytes) -> str:
    """Sniff the container so the data URI is honest. pdf_raster hands us PNG for
    every page (PDFs and photos alike), but callers are not required to."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _extract(image_bytes: bytes, template: str, page_w: int = 0, page_h: int = 0):
    import base64
    global _selected_model
    if _disabled():
        return None
    model = _select_model()
    if not model:
        return None
    _selected_model = model
    data_uri = (f"data:{_image_mime(image_bytes)};base64,"
                + base64.b64encode(image_bytes).decode("ascii"))
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _prompt_for(template)},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }],
        # json_object is a grammar constraint in llama.cpp (and honoured by other
        # /v1 servers), so _parse_response can json.loads the content unconditionally.
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": _MAX_TOKENS,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{_base_url()}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_INFER_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = (body["choices"][0].get("message") or {}).get("content") or ""
    rows = _parse_response(content, template, page_w, page_h)
    return rows or None


def _parse_response(response_text: str, template: str, page_w: int = 0, page_h: int = 0):
    data = json.loads(response_text)
    if isinstance(data, dict):
        raw_items = data.get("items")
        if raw_items is None:
            # Some models return the list under another single key, or bare.
            lists = [v for v in data.values() if isinstance(v, list)]
            raw_items = lists[0] if lists else []
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []

    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = _to_line_item(raw, template, page_w, page_h)
        if item:
            items.append(item)
    return items


def _to_qty(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d[\d,]*", str(value or ""))
    return int(m.group(0).replace(",", "")) if m else 0


_PN_COLUMN_TEMPLATES = {"lcsc", "digikey", "mouser", "pololu"}


def _parse_bbox(raw_bbox, page_w: int, page_h: int):
    """Convert a model bbox (0..1000 grid, [x0,y0,x1,y1]) to pixel [x,y,w,h].

    Returns None for anything malformed/out-of-range or when the page size is
    unknown — the caller falls back to Tesseract-token matching for highlight.
    """
    if not page_w or not page_h or not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw_bbox)
    except (TypeError, ValueError):
        return None
    if x1 < x0 or y1 < y0:
        return None
    px = int(max(0.0, min(1000.0, x0)) / 1000.0 * page_w)
    py = int(max(0.0, min(1000.0, y0)) / 1000.0 * page_h)
    pw = int(max(0.0, min(1000.0, x1 - x0)) / 1000.0 * page_w)
    ph = int(max(0.0, min(1000.0, y1 - y0)) / 1000.0 * page_h)
    if pw <= 0 or ph <= 0:
        return None
    return [px, py, pw, ph]


def _to_line_item(raw: dict, template: str, page_w: int = 0, page_h: int = 0):
    distributor_pn = str(raw.get("distributor_pn") or "").strip()
    mpn = str(raw.get("mfr_pn") or raw.get("mpn") or "").strip()
    description = str(raw.get("description") or "").strip()
    quantity = _to_qty(raw.get("qty") or raw.get("quantity"))
    if not distributor_pn and not mpn:
        return None
    # The distributor PN only belongs in a distributor column for a distributor
    # template; for "generic" we keep it as the manufacturer part if no MPN.
    distributor = template if template in _PN_COLUMN_TEMPLATES else "generic"
    if distributor == "generic":
        distributor_pn = ""
    return {
        "mpn": mpn,
        "manufacturer": "",
        "package": "",
        "description": description,
        "quantity": quantity,
        "unit_price": 0.0,
        "distributor": distributor,
        "distributor_pn": distributor_pn,
        "_backend": "vlm",
        "bbox": _parse_bbox(raw.get("bbox"), page_w, page_h),
    }
