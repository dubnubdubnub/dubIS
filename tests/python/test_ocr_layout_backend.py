"""Backend selection in ocr_layout.extract_pages, across the tesseract x VLM matrix.

Tesseract is needed for two things only: the overlay's word/line tokens
(click-to-fill highlight) and the grid/flat fallback extractors. The VLM needs
neither, so a tesseract-less machine with a reachable model server must still get
its rows — that path used to raise TesseractMissingError before the VLM was ever
consulted, making the VLM unreachable without tesseract installed.
"""
import logging

import pytest

import ocr_layout
from ocr_engine import TesseractMissingError

PAGE = {"image_b64": "AAAA", "width": 10, "height": 10,
        "words": [{"text": "x", "x": 0, "y": 0, "w": 1, "h": 1,
                   "conf": 90.0, "line_id": 0}],
        "lines": [{"text": "x"}]}


def _patch(monkeypatch, *, tesseract, vlm_rows=None, grid_rows=None, flat_rows=None):
    """Wire extract_pages' collaborators for one quadrant of the matrix.

    ``tesseract=False`` mimics a machine with no binary: ensure_tesseract is
    False, require_tesseract raises, and extract_page (which needs pytesseract)
    blows up if it is called at all.
    """
    import distributor_profiles
    import ocr_engine
    import ocr_table
    import pdf_raster
    import vlm_extract

    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: tesseract)

    def _require():
        if not tesseract:
            raise TesseractMissingError()

    monkeypatch.setattr(ocr_engine, "require_tesseract", _require)
    monkeypatch.setattr(pdf_raster, "rasterize", lambda data, ext: [(b"png", 10, 10)])

    def _page(png):
        if not tesseract:
            raise TesseractMissingError()
        return dict(PAGE)

    monkeypatch.setattr(ocr_layout, "extract_page", _page)
    # A backend that answers with rows is by definition reachable, so the probe
    # and the extraction agree — as they do in the real module, both going
    # through _select_model().
    monkeypatch.setattr(vlm_extract, "available", lambda: vlm_rows is not None)
    monkeypatch.setattr(vlm_extract, "extract_line_items",
                        lambda png, template, page_w, page_h: vlm_rows)
    monkeypatch.setattr(ocr_table, "extract_line_items",
                        lambda png, template: grid_rows)
    monkeypatch.setattr(distributor_profiles, "parse_with_template",
                        lambda template, text: flat_rows)


# ── the four quadrants ──────────────────────────────────────────────────

def test_vlm_rows_returned_when_tesseract_is_absent(monkeypatch):
    # THE bug: with no tesseract binary, extract_pages raised before the VLM was
    # ever asked, so a perfectly good VLM backend was unusable.
    vlm = [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}]
    _patch(monkeypatch, tesseract=False, vlm_rows=vlm)
    out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
    assert out["prefill_rows"] == vlm
    assert all(r["_backend"] == "vlm" for r in out["prefill_rows"])
    assert out["template"] == "lcsc"


def test_tesseract_absent_pages_render_without_highlight_tokens(monkeypatch):
    # The page image still reaches the frontend (so the overlay renders and the
    # prefilled grid is usable); only the click-to-fill word/line tokens are gone.
    import base64
    _patch(monkeypatch, tesseract=False, vlm_rows=[{"distributor_pn": "C1"}])
    out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
    assert len(out["pages"]) == 1
    page = out["pages"][0]
    assert set(page) == {"image_b64", "width", "height", "words", "lines"}
    assert base64.b64decode(page["image_b64"]) == b"png"
    assert page["width"] == 10 and page["height"] == 10
    assert page["words"] == [] and page["lines"] == []


def test_tesseract_absent_and_no_vlm_still_raises(monkeypatch):
    # Genuinely no backend: the actionable install error is still the right answer.
    _patch(monkeypatch, tesseract=False, vlm_rows=None,
           grid_rows=[{"distributor_pn": "C1"}], flat_rows=[{"distributor_pn": "C2"}])
    with pytest.raises(TesseractMissingError):
        ocr_layout.extract_pages(b"img", ".jpg", "lcsc")


def test_tesseract_absent_and_a_reachable_vlm_that_reads_nothing_raises(monkeypatch):
    # The VLM answered, just with no rows (an unreadable page), and there is no
    # tesseract to fall back to — so again, no backend produced anything.
    import vlm_extract
    _patch(monkeypatch, tesseract=False, vlm_rows=None)
    monkeypatch.setattr(vlm_extract, "available", lambda: True)  # reachable, empty-handed
    with pytest.raises(TesseractMissingError):
        ocr_layout.extract_pages(b"img", ".jpg", "lcsc")


def test_tesseract_present_vlm_wins_and_keeps_word_tokens(monkeypatch):
    # Unchanged behaviour: VLM rows beat grid/flat, and the overlay still gets
    # tesseract's tokens for click-to-fill.
    vlm = [{"distributor_pn": "C1"}]
    _patch(monkeypatch, tesseract=True, vlm_rows=vlm,
           grid_rows=[{"distributor_pn": "Cgrid"}], flat_rows=[{"distributor_pn": "Cflat"}])
    out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
    assert out["prefill_rows"] == vlm
    assert out["pages"][0]["words"] == PAGE["words"]
    assert out["pages"][0]["lines"] == PAGE["lines"]


def test_tesseract_present_without_vlm_uses_grid_flat(monkeypatch):
    _patch(monkeypatch, tesseract=True, vlm_rows=None,
           grid_rows=[{"distributor_pn": "C1"}, {"distributor_pn": "C2"}],
           flat_rows=[{"distributor_pn": "C9"}])
    out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
    assert [r["distributor_pn"] for r in out["prefill_rows"]] == ["C1", "C2"]
    assert all(r["_backend"] == "grid" for r in out["prefill_rows"])


def test_tesseract_missing_error_is_raised_before_rasterizing(monkeypatch):
    # No backend at all: don't burn the (expensive) rasterization first — but the
    # VLM probe needs a page, so the raster only happens when a VLM may answer.
    import ocr_engine
    import pdf_raster
    import vlm_extract
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: False)
    monkeypatch.setattr(vlm_extract, "available", lambda: False)

    def _boom(data, ext):
        raise AssertionError("rasterized with no usable backend")

    monkeypatch.setattr(pdf_raster, "rasterize", _boom)
    with pytest.raises(TesseractMissingError):
        ocr_layout.extract_pages(b"img", ".jpg", "lcsc")


# ── backend precedence + logging (regression guards) ────────────────────

@pytest.mark.parametrize("tesseract,vlm,grid,flat,expected,tag,marker", [
    (True, [{"distributor_pn": "C1"}], [], [], ["C1"], "vlm", "local VLM"),
    (False, [{"distributor_pn": "C1"}], None, None, ["C1"], "vlm", "local VLM"),
    (True, None, [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}],
     [{"distributor_pn": "C9"}], ["C1", "C2"], "grid", "Tesseract grid"),
    (True, None, [{"distributor_pn": "C1"}],
     [{"distributor_pn": "C1"}, {"distributor_pn": "C2"}], ["C1", "C2"], "flat",
     "Tesseract flat-parse"),
    # Equal counts: grid wins the tie (its rows are cleaner).
    (True, None, [{"distributor_pn": "Cg"}], [{"distributor_pn": "Cf"}], ["Cg"],
     "grid", "Tesseract grid"),
])
def test_backend_precedence_and_log(monkeypatch, caplog, tesseract, vlm, grid, flat,
                                    expected, tag, marker):
    _patch(monkeypatch, tesseract=tesseract, vlm_rows=vlm, grid_rows=grid, flat_rows=flat)
    with caplog.at_level(logging.INFO, logger="ocr_layout"):
        out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
    assert [r["distributor_pn"] for r in out["prefill_rows"]] == expected
    assert all(r["_backend"] == tag for r in out["prefill_rows"])
    assert "OCR backend:" in caplog.text and marker in caplog.text


def test_grid_none_falls_back_to_flat(monkeypatch):
    # ocr_table found no usable grid at all (None, not an empty list).
    _patch(monkeypatch, tesseract=True, vlm_rows=None, grid_rows=None,
           flat_rows=[{"distributor_pn": "C1"}])
    out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
    assert [r["distributor_pn"] for r in out["prefill_rows"]] == ["C1"]
    assert out["prefill_rows"][0]["_backend"] == "flat"


def test_no_backend_recovers_rows_returns_empty_prefill(monkeypatch, caplog):
    # Nothing read anything: an empty grid the user fills by hand, not an error.
    _patch(monkeypatch, tesseract=True, vlm_rows=None, grid_rows=None, flat_rows=None)
    with caplog.at_level(logging.INFO, logger="ocr_layout"):
        out = ocr_layout.extract_pages(b"img", ".jpg", "lcsc")
    assert out["prefill_rows"] == []
    assert "user must fill manually" in caplog.text


def test_tag_rows_sets_backend_and_null_bbox():
    rows = [{"mpn": "A", "quantity": 1}, {"mpn": "B", "quantity": 2}]
    out = ocr_layout._tag_rows(rows, "grid")
    assert all(r["_backend"] == "grid" for r in out)
    assert all(r["bbox"] is None for r in out)


def test_tag_rows_preserves_existing_bbox_for_vlm():
    rows = [{"mpn": "A", "_backend": "vlm", "bbox": [1, 2, 3, 4]}]
    out = ocr_layout._tag_rows(rows, "vlm")
    assert out[0]["bbox"] == [1, 2, 3, 4]
