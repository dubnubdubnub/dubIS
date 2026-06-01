"""Locate the Tesseract OCR binary (it isn't always on PATH after a default
Windows install) and point pytesseract at it. Throw a clear, actionable error
when it's genuinely missing rather than pytesseract's cryptic message."""
from __future__ import annotations

import os
import shutil

_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
]
INSTALL_HINT = ("Tesseract OCR engine not found. Install it to use image/PDF "
                "import — on Windows: winget install UB-Mannheim.TesseractOCR")


class TesseractMissingError(RuntimeError):
    def __init__(self, msg: str = INSTALL_HINT):
        super().__init__(msg)


def ensure_tesseract() -> bool:
    """Return True and set pytesseract.tesseract_cmd if Tesseract is available."""
    if shutil.which("tesseract"):
        return True
    for path in _CANDIDATES:
        if path and os.path.isfile(path):
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = path
            return True
    return False


def require_tesseract() -> None:
    if not ensure_tesseract():
        raise TesseractMissingError()
