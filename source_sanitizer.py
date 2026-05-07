"""Source-file sanitization for direct-from-mfg imports.

Validates magic bytes vs declared extension, strips EXIF from images,
rejects macros-enabled office formats, returns (bytes, sha256_hex, ext).
"""

from __future__ import annotations

import hashlib
import io

REJECTED_EXTENSIONS = {".xlsm", ".docm", ".pptm", ".dotm", ".xlsb"}

# Minimal magic-byte signatures keyed by canonical extension
_MAGIC = {
    ".pdf":  [b"%PDF-"],
    ".png":  [b"\x89PNG\r\n\x1a\n"],
    ".jpg":  [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif":  [b"GIF87a", b"GIF89a"],
    ".xls":  [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],  # OLE2
    ".xlsx": [b"PK\x03\x04"],  # ZIP
}


def validate_magic(data: bytes, ext: str) -> bool:
    """Return True if `data` has a magic prefix matching `ext`.

    Text-only formats (.csv, .tsv, .txt) are not magic-checked — return True.
    Unknown extensions return True (caller is expected to reject earlier).
    """
    ext = ext.lower()
    if ext in (".csv", ".tsv", ".txt"):
        return True
    sigs = _MAGIC.get(ext)
    if not sigs:
        return True
    return any(data.startswith(s) for s in sigs)


def strip_exif(data: bytes, ext: str) -> bytes:
    """Return image bytes with EXIF metadata removed (JPEG only)."""
    ext = ext.lower()
    if ext not in (".jpg", ".jpeg"):
        return data
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    out = io.BytesIO()
    # Re-save without EXIF
    img.save(out, format="JPEG", quality=92, exif=b"")
    return out.getvalue()


def sanitize(data: bytes, ext: str) -> tuple[bytes, str, str]:
    """Validate + strip EXIF + hash. Returns (sanitized_bytes, sha256_hex, ext).

    Raises ValueError on invalid magic bytes or rejected extension.
    """
    ext = ext.lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext in REJECTED_EXTENSIONS:
        raise ValueError(f"macro-enabled file extension rejected: {ext}")
    if not validate_magic(data, ext):
        raise ValueError(f"magic bytes do not match declared extension {ext}")

    sanitized = strip_exif(data, ext)
    sha = hashlib.sha256(sanitized).hexdigest()
    return sanitized, sha, ext
