"""Purchase Order CRUD + source-file storage.

POs are stored one-per-row in data/purchase_orders.csv.
Source files are content-addressed at data/sources/<sha256>.<ext>.
"""

from __future__ import annotations

import base64
import csv
import io
import os
import secrets
from datetime import datetime

import csv_io
import source_sanitizer

# imported_at: local timestamp (YYYY-MM-DDTHH:MM:SS) recorded when the PO is
# first created/imported — distinct from purchase_date, which is the order's
# own date. Older PO rows predating this column read back as "".
FIELDNAMES = ["po_id", "vendor_id", "source_file_hash", "source_file_ext",
              "purchase_date", "notes", "imported_at"]


def _make_po_id() -> str:
    return f"po_{secrets.token_hex(4)}"


def _ensure_csv(csv_path: str) -> None:
    if os.path.exists(csv_path):
        return
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()


def _read(csv_path: str) -> list[dict[str, str]]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write(csv_path: str, rows: list[dict[str, str]]) -> None:
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    normalized = [{k: row.get(k, "") for k in FIELDNAMES} for row in rows]
    csv_io.atomic_write_rows(csv_path, FIELDNAMES, normalized, encoding="utf-8")


def list_purchase_orders(csv_path: str) -> list[dict[str, str]]:
    return _read(csv_path)


def get_purchase_order(csv_path: str, po_id: str) -> dict[str, str] | None:
    return next((r for r in _read(csv_path) if r["po_id"] == po_id), None)


def create_purchase_order(
    csv_path: str,
    sources_dir: str,
    vendor_id: str,
    source_file_bytes: bytes | None,
    source_file_ext: str | None,
    purchase_date: str,
    notes: str = "",
    imported_at: str | None = None,
) -> dict[str, str]:
    """Create a PO; if source file is provided, sanitize+hash+store it.

    imported_at stamps when the PO entered the system; defaults to now.
    """
    if not vendor_id:
        raise ValueError("vendor_id required")

    file_hash = ""
    file_ext = ""
    if source_file_bytes is not None and source_file_ext:
        sanitized, sha, ext = source_sanitizer.sanitize(source_file_bytes, source_file_ext)
        os.makedirs(sources_dir, exist_ok=True)
        target = os.path.join(sources_dir, sha + ext)
        if not os.path.isfile(target):
            with open(target, "wb") as f:
                f.write(sanitized)
        file_hash = sha
        file_ext = ext

    new_po = {
        "po_id": _make_po_id(),
        "vendor_id": vendor_id,
        "source_file_hash": file_hash,
        "source_file_ext": file_ext,
        "purchase_date": purchase_date,
        "notes": notes,
        "imported_at": imported_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _ensure_csv(csv_path)
    rows = _read(csv_path)
    rows.append(new_po)
    _write(csv_path, rows)
    return new_po


def update_purchase_order(csv_path: str, po_id: str, **fields) -> dict[str, str]:
    """Update mutable fields on a PO. Allowed: vendor_id, purchase_date, notes,
    source_file_hash, source_file_ext."""
    allowed = {"vendor_id", "purchase_date", "notes",
               "source_file_hash", "source_file_ext"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unsupported fields: {bad}")
    rows = _read(csv_path)
    for r in rows:
        if r["po_id"] == po_id:
            r.update({k: v for k, v in fields.items() if v is not None})
            _write(csv_path, rows)
            return r
    raise KeyError(po_id)


def delete_purchase_order(csv_path: str, sources_dir: str, po_id: str) -> None:
    """Remove the PO row; orphan-collect the source file if no other PO refs it."""
    rows = _read(csv_path)
    target = next((r for r in rows if r["po_id"] == po_id), None)
    if not target:
        return
    rest = [r for r in rows if r["po_id"] != po_id]
    _write(csv_path, rest)
    h = target.get("source_file_hash") or ""
    ext = target.get("source_file_ext") or ""
    if h and not any(r.get("source_file_hash") == h for r in rest):
        candidate = os.path.join(sources_dir, h + ext)
        if os.path.isfile(candidate):
            os.remove(candidate)


def resolve_source_path(sources_dir: str, po_id: str, csv_path: str) -> str | None:
    po = get_purchase_order(csv_path, po_id)
    if not po or not po.get("source_file_hash"):
        return None
    candidate = os.path.join(sources_dir, po["source_file_hash"] + (po.get("source_file_ext") or ""))
    return candidate if os.path.isfile(candidate) else None


# Source extensions we can turn into an inline <img>. Images render directly;
# PDFs are rasterized (first page) via pdf_raster. Everything else (.csv/.xls/…)
# has no image representation, so the picker shows no thumbnail.
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}


def source_preview(sources_dir: str, po_id: str, csv_path: str) -> dict:
    """Return a renderable image preview of a PO's archived source file.

    {"kind": "image", "data_uri", "mime", "width", "height", "page_count"} for
    image and PDF sources (PDFs rasterized to PNG, first page); otherwise
    {"kind": "none", "reason"} for missing/spreadsheet/CSV/unknown-PO cases.

    A corrupt image/PDF raises from the decoder — that is a real failure, not a
    "no preview" state, so we let it propagate rather than swallow it.
    """
    path = resolve_source_path(sources_dir, po_id, csv_path)
    if not path:
        return {"kind": "none", "reason": "no source file"}
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        data = f.read()

    if ext == ".pdf":
        import pdf_raster
        pages = pdf_raster.rasterize(data, ext)
        png, width, height = pages[0]
        b64 = base64.b64encode(png).decode("ascii")
        return {"kind": "image", "mime": "image/png",
                "data_uri": f"data:image/png;base64,{b64}",
                "width": width, "height": height, "page_count": len(pages)}

    mime = _IMAGE_MIME.get(ext)
    if mime:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.width, im.height
        b64 = base64.b64encode(data).decode("ascii")
        return {"kind": "image", "mime": mime,
                "data_uri": f"data:{mime};base64,{b64}",
                "width": width, "height": height, "page_count": 1}

    return {"kind": "none", "reason": f"unsupported source type {ext or '(none)'}"}
