import io
import fitz  # PyMuPDF
import pdf_raster


def _one_page_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 50), "HELLO PO")
    data = doc.tobytes()
    doc.close()
    return data


def test_rasterize_pdf_returns_one_page_with_dims():
    pages = pdf_raster.rasterize(_one_page_pdf(), ".pdf")
    assert len(pages) == 1
    png, w, h = pages[0]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert w > 300 and h > 200


def test_rasterize_multipage_pdf():
    doc = fitz.open()
    doc.new_page(width=300, height=200)
    doc.new_page(width=300, height=200)
    data = doc.tobytes(); doc.close()
    pages = pdf_raster.rasterize(data, ".pdf")
    assert len(pages) == 2


def test_image_passthrough_returns_single_page():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    pages = pdf_raster.rasterize(buf.getvalue(), ".png")
    assert len(pages) == 1
    png, w, h = pages[0]
    assert (w, h) == (40, 30)


def test_image_exif_orientation_is_applied():
    # Phone photos store sensor-native landscape pixels + an EXIF orientation tag.
    # rasterize must rotate them upright (tag 6 = rotate 90° CW for display), or the
    # overlay shows a sideways preview and OCR reads sideways text as garbage.
    from PIL import Image
    im = Image.new("RGB", (400, 300), "white")  # landscape pixels
    exif = im.getexif()
    exif[274] = 6  # Orientation: rotate 90° CW on display
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    _png, w, h = pdf_raster.rasterize(buf.getvalue(), ".jpg")[0]
    # After honouring the tag the page is upright (portrait): dimensions swapped.
    assert (w, h) == (300, 400)


def test_image_without_exif_orientation_unchanged():
    # No orientation tag (e.g. an already-upright scan) → dimensions preserved.
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, format="JPEG")
    _png, w, h = pdf_raster.rasterize(buf.getvalue(), ".jpg")[0]
    assert (w, h) == (300, 400)


def test_oversized_image_is_downscaled():
    # A ~24 MP phone photo must be capped so the preview payload and the OCR/grid
    # heuristics stay sane; the long edge is bounded by _MAX_EDGE.
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (5712, 4284), "white").save(buf, format="PNG")
    _png, w, h = pdf_raster.rasterize(buf.getvalue(), ".png")[0]
    assert max(w, h) == pdf_raster._MAX_EDGE
    assert (w, h) == (2600, 1950)  # aspect preserved
