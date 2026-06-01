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
