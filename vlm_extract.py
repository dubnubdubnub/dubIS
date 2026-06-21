"""Optional local vision-language-model backend for packing-list extraction.

A strong VLM reads photographed packing lists holistically — including faint
print, folds, and perspective that defeat classical OCR + table detection. We run
one entirely locally via Ollama (e.g. ``qwen2.5vl:7b``) so nothing leaves the
machine (the documents carry PII). This is the PREFERRED extractor when a capable
Ollama is reachable; otherwise ``extract_line_items`` returns ``None`` and the
caller falls back to the Tesseract grid/flat pipeline — so GPU-less nodes (and CI)
are unaffected.

Configuration (per-node, via environment):
    DUBIS_OLLAMA_URL   base URL (default http://127.0.0.1:11434)
    DUBIS_VLM_MODEL    model tag (default qwen2.5vl:7b) — set a smaller tag on
                       weak GPUs, or...
    DUBIS_VLM_DISABLE  set to any non-empty value to force the backend off.

Public API:
    available() -> bool
        Fast check: Ollama reachable AND the configured model is pulled.
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

_DEFAULT_URL = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "qwen2.5vl:7b"
# Short timeout for the reachability probe so a node without Ollama falls back
# almost instantly; generous timeout for the actual (GPU) inference call.
_PROBE_TIMEOUT = 1.5
_INFER_TIMEOUT = 600

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
    return (os.environ.get("DUBIS_OLLAMA_URL") or _DEFAULT_URL).rstrip("/")


def _model() -> str:
    return os.environ.get("DUBIS_VLM_MODEL") or _DEFAULT_MODEL


def _disabled() -> bool:
    return bool(os.environ.get("DUBIS_VLM_DISABLE"))


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def available() -> bool:
    """True if the backend is enabled, Ollama is reachable, and the model exists."""
    if _disabled():
        return False
    try:
        tags = _get_json(f"{_base_url()}/api/tags", _PROBE_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("VLM backend unavailable (probe failed): %s", exc)
        return False
    names = {m.get("name", "") for m in (tags.get("models") or [])}
    want = _model()
    # Accept an exact tag or the same model with any tag (e.g. "qwen2.5vl").
    base = want.split(":", 1)[0]
    return want in names or any(n.split(":", 1)[0] == base for n in names)


def extract_line_items(image_bytes: bytes, template: str = "generic",
                       page_w: int = 0, page_h: int = 0):
    """Extract line items via the local VLM, or None if unavailable/failed."""
    if _disabled() or not image_bytes:
        return None
    try:
        return _extract(image_bytes, template, page_w, page_h)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        logger.warning("VLM extraction failed, falling back: %s", exc)
        return None


def _extract(image_bytes: bytes, template: str, page_w: int = 0, page_h: int = 0):
    import base64
    if not available():
        return None
    payload = {
        "model": _model(),
        "prompt": _prompt_for(template),
        "images": [base64.b64encode(image_bytes).decode("ascii")],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{_base_url()}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_INFER_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    rows = _parse_response(body.get("response", ""), template, page_w, page_h)
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
