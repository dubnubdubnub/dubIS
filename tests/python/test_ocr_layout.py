import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

import ocr_layout

requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed"
)


def _text_png(lines: list[str]) -> bytes:
    # Keep width at 640 (test_dimensions_returned / the mocked test assert it).
    img = Image.new("RGB", (640, 70 * len(lines) + 40), "white")
    d = ImageDraw.Draw(img)
    font = None
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(name, 36)
            break
        except OSError:
            continue
    if font is None:
        # Sized default font — large enough for tesseract (unsized default is a
        # tiny bitmap that OCR cannot read). Mirrors test_mfg_direct_import.
        font = ImageFont.load_default(size=36)
    for i, ln in enumerate(lines):
        d.text((20, 20 + i * 70), ln, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@requires_tesseract
def test_words_have_text_bbox_and_conf():
    page = ocr_layout.extract_page(_text_png(["C12624 4000"]))
    assert page["words"], "expected OCR to detect words"
    for w in page["words"]:
        for k in ("text", "x", "y", "w", "h", "conf", "line_id"):
            assert k in w
        assert w["w"] > 0 and w["h"] > 0
    # OCR is fuzzy across fonts/versions; assert it read real content (the
    # rendered string has digits) rather than pinning exact glyphs.
    joined = " ".join(w["text"] for w in page["words"])
    assert any(ch.isdigit() for ch in joined)


@requires_tesseract
def test_lines_group_words_on_same_row():
    page = ocr_layout.extract_page(_text_png(["Emerald Green LED"]))
    assert page["lines"], "expected at least one line"
    # The row's words collapse into one multi-word line token (grouping works).
    assert any(" " in ln["text"] for ln in page["lines"])
    joined = " ".join(ln["text"] for ln in page["lines"])
    assert "Emerald" in joined or "Green" in joined


@requires_tesseract
def test_dimensions_returned():
    png = _text_png(["X"])
    page = ocr_layout.extract_page(png)
    assert page["width"] == 640 and page["height"] > 0


def test_extract_page_mocked(monkeypatch):
    import sys, types, base64
    fake = {
        "text":      ["",  "C12624", "4000", "Emerald", "Green"],
        "conf":      ["-1", "95",    "88",   "30",      "72"],
        "left":      [0,    20,      130,    20,        160],
        "top":       [0,    20,      22,     80,        80],
        "width":     [640,  100,     60,     120,       90],
        "height":    [40,   30,      28,     30,        30],
        "block_num": [1,    1,       1,      1,         1],
        "par_num":   [1,    1,       1,      1,         1],
        "line_num":  [1,    1,       1,      2,         2],
    }
    fake_pt = types.SimpleNamespace(
        image_to_data=lambda im, output_type=None: fake,
        Output=types.SimpleNamespace(DICT="dict"),
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pt)
    png = _text_png(["x"])
    page = ocr_layout.extract_page(png)
    assert [w["text"] for w in page["words"]] == ["C12624", "4000", "Emerald", "Green"]
    assert page["words"][0]["conf"] == 95.0
    assert {w["line_id"] for w in page["words"]} == {0, 1}
    assert page["lines"][0]["text"] == "C12624 4000"
    assert page["lines"][0]["w"] == 170 and page["lines"][0]["h"] == 30
    assert page["lines"][0]["conf"] == pytest.approx((95 + 88) / 2)
    assert base64.b64decode(page["image_b64"]) == png
    assert page["width"] == 640
