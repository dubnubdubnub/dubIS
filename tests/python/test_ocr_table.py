"""Tests for ocr_table: grid-aware table extraction for ruled packing lists.

The CV/OCR pipeline (perspective-warp -> orient via OSD -> per-cell OCR ->
content-based column typing) is exercised on synthetically rendered bordered
tables. OCR is fuzzy across fonts/versions, so we assert the machine-clean
signals (LCSC C-numbers, row counts) rather than pinning every glyph.

ocr_table self-gates: it returns None (never raises) when there's no detectable
grid or OpenCV is unavailable, so the caller falls back to flat-OCR parsing.
"""
import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

import ocr_table

requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed"
)

_COLS = [50, 180, 360, 860, 1060, 1260, 1400]
_HEADERS = ["No.", "LCSC Part #", "Full Description", "Qty Ordered",
            "Qty Shipped", "COO"]


def _font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _render_table(data_rows):
    """Render a clean bordered LCSC-style packing-list table to PNG bytes.

    data_rows: list of 6-tuples (No, LCSC#, Description, QtyOrdered, QtyShipped, COO).
    """
    ys = [40 + 100 * i for i in range(len(data_rows) + 2)]
    w, h = 1450, ys[-1] + 40
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f = _font(24)
    for x in _COLS:
        d.line([(x, ys[0]), (x, ys[-1])], fill="black", width=2)
    for y in ys:
        d.line([(_COLS[0], y), (_COLS[-1], y)], fill="black", width=2)

    def put(r, c, text):
        d.text((_COLS[c] + 8, ys[r] + 32), text, fill="black", font=f)

    for c, head in enumerate(_HEADERS):
        put(0, c, head)
    for i, row in enumerate(data_rows, 1):
        for c, val in enumerate(row):
            put(i, c, val)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@requires_tesseract
def test_extracts_multi_row_table_with_clean_pns():
    png = _render_table([
        ("1", "C12624", "Mfr. Part#: KT-0603G", "4000", "4000", "CN"),
        ("2", "C377861", "Mfr. Part#: WSD4066DN33", "200", "200", "CN"),
        ("3", "C424643", "Mfr. Part#: DF40C-40DP", "500", "500", "JP"),
    ])
    items = ocr_table.extract_line_items(png, "lcsc")
    assert items is not None, "expected the grid to be detected"
    assert len(items) >= 2
    pns = {it["distributor_pn"] for it in items}
    # Machine-clean C-numbers OCR reliably; tolerate one miss across tesseract
    # versions but require the PN column to be correctly identified and read.
    expected = {"C12624", "C377861", "C424643"}
    assert len(expected & pns) >= 2, f"expected LCSC C-numbers, got {pns}"
    for it in items:
        assert it["distributor"] == "lcsc"


@requires_tesseract
def test_extracts_single_item_packing_list():
    # A packing list can have exactly one line item — the extractor must not
    # require a multi-row table (no header-vs-data assumption).
    png = _render_table([
        ("1", "C2874885", "Mfr. Part#: WS2812B-V5", "1000", "1000", "CN"),
    ])
    items = ocr_table.extract_line_items(png, "lcsc")
    assert items is not None
    assert len(items) == 1
    assert items[0]["distributor_pn"] == "C2874885"


@requires_tesseract
def test_no_grid_image_returns_none():
    # A plain image with text but no ruled table → None, so the caller falls back
    # to the flat-OCR heuristic parser.
    img = Image.new("RGB", (800, 200), "white")
    ImageDraw.Draw(img).text((20, 80), "just some prose, no table here",
                             fill="black", font=_font(28))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert ocr_table.extract_line_items(buf.getvalue(), "lcsc") is None


def test_returns_none_when_opencv_unavailable(monkeypatch):
    # Graceful degradation: if cv2/numpy can't be imported, return None (the
    # caller falls back) rather than raising. No tesseract needed for this path.
    monkeypatch.setattr(ocr_table, "_cv", lambda: (None, None))
    assert ocr_table.extract_line_items(b"\x89PNG not-a-real-image", "lcsc") is None


def test_never_raises_on_garbage_bytes(monkeypatch):
    # Defensive contract: any decode/CV error is swallowed → None.
    assert ocr_table.extract_line_items(b"not an image at all", "lcsc") is None
