import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

import ocr_layout

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed"
)


def _text_png(lines: list[str]) -> bytes:
    img = Image.new("RGB", (640, 60 * len(lines) + 40), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 32)
    except OSError:
        font = ImageFont.load_default()
    for i, ln in enumerate(lines):
        d.text((20, 20 + i * 60), ln, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_words_have_text_bbox_and_conf():
    png = _text_png(["C12624 4000"])
    page = ocr_layout.extract_page(png)
    texts = [w["text"] for w in page["words"]]
    assert any("C12624" in t for t in texts)
    for w in page["words"]:
        for k in ("text", "x", "y", "w", "h", "conf", "line_id"):
            assert k in w
        assert w["w"] > 0 and w["h"] > 0


def test_lines_group_words_on_same_row():
    png = _text_png(["KT-0603G Emerald Green LED"])
    page = ocr_layout.extract_page(png)
    assert page["lines"], "expected at least one line"
    assert any("Emerald" in ln["text"] and "KT-0603G" in ln["text"]
               for ln in page["lines"])


def test_dimensions_returned():
    png = _text_png(["X"])
    page = ocr_layout.extract_page(png)
    assert page["width"] == 640 and page["height"] > 0
