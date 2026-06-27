"""Scan-image helpers: constants, validation, normalisation, and persistence.

These pure-Python utilities are consumed by the scan-upload handler in
pnp_server.py.  Kept separate so they can be unit-tested without pulling in
the HTTP server machinery.
"""

import base64
import binascii
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)

# Max accepted decoded image size for an upload (bytes). Phone photos are a few
# MB; 15 MB gives generous headroom while rejecting abuse. Enforced PER IMAGE.
SCAN_MAX_IMAGE_BYTES = 15 * 1024 * 1024

# Max number of images in a single multi-photo upload (one PO can span several
# printed pages). Bounds memory + OCR work per request.
SCAN_MAX_IMAGES = 12

# Filename extensions accepted for scan uploads.
SCAN_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _save_scan_image(base_dir, image_bytes, ext):
    """Persist an uploaded scan image to ``<base_dir>/scans`` and return its path.

    Called the moment a phone upload arrives (before OCR) so the original photo
    is always kept on the desktop, even if OCR fails or the user never finishes
    the import. Filenames are timestamped with a short random suffix so two
    uploads in the same second can't collide.
    """
    scans_dir = os.path.join(base_dir, "scans")
    os.makedirs(scans_dir, exist_ok=True)
    name = f"scan_{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(3)}{ext}"
    path = os.path.join(scans_dir, name)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


class _ScanUploadError(Exception):
    """Client-error during scan-upload validation, carrying the HTTP status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _validate_scan_image(entry):
    """Validate one upload image dict; return {image_b64, filename, ext, decoded}.

    Raises _ScanUploadError(status, message) on any client-side problem so the
    handler can surface the same 400/413 responses it always has.
    """
    image_b64 = (entry.get("image_b64") if isinstance(entry, dict) else "") or ""
    filename = ((entry.get("filename") if isinstance(entry, dict) else "") or "").strip()
    if not image_b64:
        raise _ScanUploadError(400, "image_b64 is required")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SCAN_IMAGE_EXTS:
        raise _ScanUploadError(400, f"Unsupported file type: {ext or '(none)'}")
    # base64 inflates by ~4/3; check the encoded length before decoding a blob.
    if len(image_b64) * 3 // 4 > SCAN_MAX_IMAGE_BYTES:
        raise _ScanUploadError(413, "Image too large")
    try:
        decoded = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _ScanUploadError(400, f"Invalid base64 image data: {exc}") from exc
    if len(decoded) > SCAN_MAX_IMAGE_BYTES:
        raise _ScanUploadError(413, "Image too large")
    return {"image_b64": image_b64, "filename": filename, "ext": ext, "decoded": decoded}


def _normalize_groups(raw, n):
    """Coerce a client-supplied photo grouping into a clean partition of range(n).

    Each inner list is one PO (photo indices). Out-of-range, duplicate, and
    non-integer indices are dropped; any photo not covered becomes its own group;
    empty groups are removed. Falls back to one-group-per-photo when *raw* isn't a
    usable list. Groups are ordered by their first photo index for stable output.
    """
    default = [[i] for i in range(n)]
    if not isinstance(raw, list):
        return default
    seen = set()
    groups = []
    for grp in raw:
        if not isinstance(grp, list):
            continue
        members = []
        for idx in grp:
            if (isinstance(idx, int) and not isinstance(idx, bool)
                    and 0 <= idx < n and idx not in seen):
                seen.add(idx)
                members.append(idx)
        if members:
            groups.append(sorted(members))
    for i in range(n):  # uncovered photos each become their own PO
        if i not in seen:
            groups.append([i])
    groups.sort(key=lambda g: g[0])
    return groups or default
