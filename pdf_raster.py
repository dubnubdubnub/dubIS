"""Rasterize source documents to per-page PNG bytes for the OCR-overlay modal.

PDFs are rendered with PyMuPDF (no system Poppler dependency). Image files are
oriented upright (honouring EXIF) and downscaled, then re-encoded to PNG for a
uniform contract.
"""

from __future__ import annotations

import io

_PDF_DPI = 180  # render scale; 72 = native. Higher = crisper OCR, bigger payload.
# Cap the long edge of a rasterized page. Phone photos are ~24 MP (5712 px) which
# both bloats the overlay preview payload and breaks the OCR/grid heuristics
# (their kernels/thresholds are tuned for ~2000 px); 2600 keeps text crisp.
_MAX_EDGE = 2600


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
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as im:
        # Honour EXIF orientation FIRST — phone photos are stored in the sensor's
        # native landscape pixels with an orientation tag (e.g. tag 6 = rotate 90).
        # Skipping this left packing-list scans sideways: a rotated preview, and
        # OCR/grid reading sideways text as garbage. exif_transpose is a no-op for
        # images without the tag (e.g. already-upright scans), so it's safe.
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = _downscale(im)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), im.width, im.height


def _downscale(im):
    """Shrink so the long edge is at most _MAX_EDGE (preserves aspect)."""
    longest = max(im.width, im.height)
    if longest <= _MAX_EDGE:
        return im
    scale = _MAX_EDGE / longest
    return im.resize((round(im.width * scale), round(im.height * scale)))
