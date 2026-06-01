"""Tests for inventory_api.ocr_overlay_b64 — rasterize + OCR + heuristic prefill.

ocr_overlay_b64 orchestrates pdf_raster.rasterize -> ocr_layout.extract_page ->
distributor_profiles.parse_with_template into one call the OCR-overlay modal uses.
We monkeypatch ocr_layout.extract_page to a canned page so the return SHAPE is
exercised in CI without the tesseract binary.
"""

import base64
import io

from PIL import Image

from inventory_api import InventoryApi


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (200, 80), "white").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_ocr_overlay_returns_pages_and_prefill(monkeypatch):
    # Tesseract may be unavailable in CI; monkeypatch extract_page to a canned
    # page so the API shape is still exercised.
    import ocr_engine
    import ocr_layout
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: True)
    monkeypatch.setattr(ocr_layout, "extract_page", lambda png: {
        "image_b64": "AAAA", "width": 200, "height": 80, "words": [], "lines": []})

    api = InventoryApi(debug=True)
    out = api.ocr_overlay_b64(_png_b64(), "scan.png", "lcsc")

    assert out["template"] == "lcsc"
    assert isinstance(out["pages"], list) and len(out["pages"]) == 1
    page = out["pages"][0]
    for k in ("image_b64", "width", "height", "words", "lines"):
        assert k in page
    assert isinstance(out["prefill_rows"], list)


def test_ocr_overlay_prefill_uses_template_heuristic(monkeypatch):
    # A canned line whose text is an LCSC-shaped row should produce a prefill
    # row tagged with the lcsc distributor — proving we reuse the real heuristic.
    import ocr_engine
    import ocr_layout
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: True)
    monkeypatch.setattr(ocr_layout, "extract_page", lambda png: {
        "image_b64": "AAAA", "width": 200, "height": 80, "words": [],
        "lines": [{"text": "C429942 SN74LVC1G08 100 0.05", "x": 0, "y": 0,
                   "w": 200, "h": 20, "conf": 90.0}]})

    api = InventoryApi(debug=True)
    out = api.ocr_overlay_b64(_png_b64(), "scan.png", "lcsc")

    assert out["prefill_rows"], "expected the lcsc heuristic to find a row"
    row = out["prefill_rows"][0]
    assert row["distributor"] == "lcsc"
    assert row["distributor_pn"] == "C429942"
    assert row["quantity"] == 100
    assert row["unit_price"] == 0.05
