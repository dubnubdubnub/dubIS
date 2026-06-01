"""Word/line-level OCR layout extraction for the OCR-overlay modal.

Uses pytesseract.image_to_data to get per-word bounding boxes and confidence,
then groups words sharing (block, par, line) into line tokens.
"""

from __future__ import annotations

import base64
import io
from typing import Any

LOW_CONF = 60.0  # words below this are flagged for review in prefill


def extract_page(png_bytes: bytes) -> dict[str, Any]:
    """Return {image_b64, width, height, words, lines} for one page image."""
    import pytesseract
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.convert("RGB")
        width, height = im.width, im.height
        data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT)

    words: list[dict[str, Any]] = []
    groups: dict[tuple, int] = {}
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line_id = _line_index(groups, key)
        words.append({
            "text": text,
            "x": int(data["left"][i]), "y": int(data["top"][i]),
            "w": int(data["width"][i]), "h": int(data["height"][i]),
            "conf": conf, "line_id": line_id,
        })

    lines = _group_lines(words)
    return {
        "image_b64": base64.b64encode(png_bytes).decode("ascii"),
        "width": width, "height": height,
        "words": words, "lines": lines,
    }


def _line_index(groups: dict[tuple, int], key: tuple) -> int:
    if key not in groups:
        groups[key] = len(groups)
    return groups[key]


def _group_lines(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_line: dict[int, list[dict[str, Any]]] = {}
    for w in words:
        by_line.setdefault(w["line_id"], []).append(w)
    lines = []
    for line_id, ws in sorted(by_line.items()):
        ws = sorted(ws, key=lambda w: w["x"])
        x0 = min(w["x"] for w in ws)
        y0 = min(w["y"] for w in ws)
        x1 = max(w["x"] + w["w"] for w in ws)
        y1 = max(w["y"] + w["h"] for w in ws)
        confs = [w["conf"] for w in ws if w["conf"] >= 0]
        lines.append({
            "text": " ".join(w["text"] for w in ws),
            "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
            "conf": (sum(confs) / len(confs)) if confs else -1.0,
        })
    return lines
