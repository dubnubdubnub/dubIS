import io
import logging
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
    # extract_page now gates on the real Tesseract binary via
    # ocr_engine.require_tesseract(); this test mocks pytesseract directly, so
    # stub the engine check to keep it binary-independent.
    import ocr_engine
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: True)
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


class TestExtractPagesPrefillSelection:
    """extract_pages keeps whichever extractor recovered MORE rows, so a partial
    grid result never suppresses a more complete flat parse (the bug that made a
    6-row packing list autofill a single row)."""

    def _patch(self, monkeypatch, grid_rows, flat_rows, vlm_rows=None):
        import ocr_engine, ocr_table, distributor_profiles, pdf_raster, vlm_extract
        monkeypatch.setattr(ocr_engine, "require_tesseract", lambda: None)
        monkeypatch.setattr(pdf_raster, "rasterize", lambda data, ext: [(b"png", 10, 10)])
        monkeypatch.setattr(
            ocr_layout, "extract_page",
            lambda png: {"image_b64": "", "width": 10, "height": 10,
                         "words": [], "lines": [{"text": "x"}]})
        # VLM backend off by default in these tests (None) so the grid/flat
        # selection logic is exercised; overridden where the VLM path is tested.
        monkeypatch.setattr(vlm_extract, "extract_line_items",
                            lambda png, template: vlm_rows)
        monkeypatch.setattr(ocr_table, "extract_line_items",
                            lambda png, template: grid_rows)
        monkeypatch.setattr(distributor_profiles, "parse_with_template",
                            lambda template, text: flat_rows)

    def test_flat_wins_when_it_has_more_rows(self, monkeypatch):
        grid = [{"distributor_pn": "C1"}]
        flat = [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}, {"distributor_pn": "C3"}]
        self._patch(monkeypatch, grid, flat)
        out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
        assert out["prefill_rows"] == flat

    def test_grid_wins_when_it_has_more_or_equal_rows(self, monkeypatch):
        grid = [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}]
        flat = [{"distributor_pn": "C9"}]
        self._patch(monkeypatch, grid, flat)
        out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
        assert out["prefill_rows"] == grid

    def test_grid_none_falls_back_to_flat(self, monkeypatch):
        flat = [{"distributor_pn": "C1"}]
        self._patch(monkeypatch, None, flat)
        out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
        assert out["prefill_rows"] == flat

    def test_vlm_preferred_when_available(self, monkeypatch):
        # When the local VLM backend returns rows, they win outright over the
        # grid/flat extractors (it reads faint/folded pages classical OCR can't).
        vlm = [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}, {"distributor_pn": "C3"}]
        self._patch(monkeypatch, grid_rows=[{"distributor_pn": "Cgrid"}],
                    flat_rows=[{"distributor_pn": "Cflat"}], vlm_rows=vlm)
        out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
        assert out["prefill_rows"] == vlm

    def test_vlm_none_falls_back_to_classical(self, monkeypatch):
        # No capable VLM (GPU-less node / CI) → unchanged grid/flat behaviour.
        grid = [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}]
        self._patch(monkeypatch, grid_rows=grid, flat_rows=[{"distributor_pn": "C9"}],
                    vlm_rows=None)
        out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
        assert out["prefill_rows"] == grid

    @pytest.mark.parametrize("vlm,grid,flat,marker", [
        ([{"distributor_pn": "C1"}], [], [], "local VLM"),
        (None, [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}],
         [{"distributor_pn": "C9"}], "Tesseract grid"),
        (None, [{"distributor_pn": "C1"}],
         [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}], "Tesseract flat-parse"),
    ])
    def test_logs_which_backend(self, monkeypatch, caplog, vlm, grid, flat, marker):
        # The log states which OCR backend produced the rows, for diagnosability.
        self._patch(monkeypatch, grid_rows=grid, flat_rows=flat, vlm_rows=vlm)
        with caplog.at_level(logging.INFO, logger="ocr_layout"):
            ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
        assert marker in caplog.text
        assert "OCR backend:" in caplog.text
