"""Rasterize source documents to per-page PNG bytes for the OCR-overlay modal.

PDFs are rendered with PyMuPDF (no system Poppler dependency). Image files are
returned as a single page unchanged (re-encoded to PNG for a uniform contract).
"""

from __future__ import annotations

import io

_PDF_DPI = 180  # render scale; 72 = native. Higher = crisper OCR, bigger payload.


def rasterize(data: bytes, ext: str) -> list[tuple[bytes, int, int]]:
    """Return [(png_bytes, width, height), ...], one entry per page.

    ext: lowercased file extension including dot (".pdf", ".png", ".jpg", ...).
    """
    ext = (ext or "").lower()
    if ext == ".pdf":
        return _rasterize_pdf(data)
    return [_image_to_png_page(data)]


def _rasterize_pdf(data: bytes) -> list[tuple[bytes, int, int]]:
    import fitz

    zoom = _PDF_DPI / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[tuple[bytes, int, int]] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append((pix.tobytes("png"), pix.width, pix.height))
    if not pages:
        raise ValueError("PDF contained no pages")
    return pages


def _image_to_png_page(data: bytes) -> tuple[bytes, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), im.width, im.height
