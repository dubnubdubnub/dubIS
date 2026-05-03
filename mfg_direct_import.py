"""Direct-from-manufacturer import orchestration.

Public entry points (this task only):
    parse_source_file(path) — return list of candidate line items
"""

from __future__ import annotations

import csv
import difflib
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Heuristic regex for "MPN  Mfg  Qty  Price" rows in OCR/PDF text
_LINE_RE = re.compile(
    r"^\s*([A-Z0-9][A-Z0-9._/+\-]{1,40})"   # MPN-like token
    r"\s+([A-Za-z][A-Za-z0-9 .&,'\-]{1,30})"  # Manufacturer-like token
    r"\s+(\d{1,6})"                          # Qty
    r"\s+([0-9]+\.?\d{0,4})"                 # Unit price
    r"\s*$",
    re.IGNORECASE,
)

# CSV header mapping (loose match against existing detect_columns logic)
_CSV_FIELD_MAP = {
    "manufacture part number": "mpn",
    "mpn": "mpn",
    "manufacturer": "manufacturer",
    "package": "package",
    "quantity": "quantity",
    "qty": "quantity",
    "unit price": "unit_price",
    "unit price($)": "unit_price",
    "price": "unit_price",
}


def _empty_row() -> dict[str, Any]:
    return {"mpn": "", "manufacturer": "", "package": "", "quantity": 0, "unit_price": 0.0}


def _extract_csv(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for src in reader:
            row = _empty_row()
            for header, val in src.items():
                if header is None:
                    continue
                key = _CSV_FIELD_MAP.get(header.strip().lower())
                if not key:
                    continue
                if key == "quantity":
                    try:
                        row["quantity"] = int(str(val).replace(",", "").strip() or "0")
                    except ValueError:
                        row["quantity"] = 0
                elif key == "unit_price":
                    try:
                        row["unit_price"] = float(str(val).replace("$", "").replace(",", "").strip() or "0")
                    except ValueError:
                        row["unit_price"] = 0.0
                else:
                    row[key] = (val or "").strip()
            if row["mpn"]:
                rows.append(row)
    return rows


def _heuristic_parse_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        rows.append({
            "mpn": m.group(1).strip(),
            "manufacturer": m.group(2).strip(),
            "package": "",
            "quantity": int(m.group(3)),
            "unit_price": float(m.group(4)),
        })
    return rows


def _extract_pdf(path: str) -> list[dict[str, Any]]:
    import pdfplumber
    rows: list[dict[str, Any]] = []
    text_chunks: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for table in tables:
                for tr in table:
                    if not tr:
                        continue
                    line = " ".join((c or "").strip() for c in tr)
                    text_chunks.append(line)
            page_text = page.extract_text() or ""
            text_chunks.append(page_text)
    rows = _heuristic_parse_lines("\n".join(text_chunks))
    return rows


def _extract_image(path: str) -> list[dict[str, Any]]:
    import pytesseract
    from PIL import Image
    text = pytesseract.image_to_string(Image.open(path))
    return _heuristic_parse_lines(text)


def parse_source_file(path: str) -> list[dict[str, Any]]:
    """Best-effort extraction of line items from a source file.

    Returns a list of dicts with keys: mpn, manufacturer, package, quantity, unit_price.
    Empty list if the format is unsupported or no rows could be extracted.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".csv", ".tsv"):
            return _extract_csv(path)
        if ext in (".xls", ".xlsx"):
            # Reuse existing csv_io conversion
            import csv_io
            xls = csv_io.convert_xls_to_csv(path)
            if not xls:
                return []
            tmp_text = xls["csv_text"]
            tmp_path = path + ".tmp.csv"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(tmp_text)
            try:
                return _extract_csv(tmp_path)
            finally:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
        if ext == ".pdf":
            return _extract_pdf(path)
        if ext in (".png", ".jpg", ".jpeg", ".gif"):
            return _extract_image(path)
    except Exception as exc:
        logger.warning("parse_source_file(%s) failed: %s", path, exc)
        return []
    return []


# ── Match-and-confirm ────────────────────────────────────────────────


def _normalize_mpn(s: str) -> str:
    """Lowercase, strip whitespace/hyphens/underscores."""
    return re.sub(r"[\s_\-]+", "", (s or "").lower())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def match_part(db, mpn: str, manufacturer: str = "") -> dict[str, Any]:
    """Match an MPN against existing parts in the cache.

    Returns:
        {"status": "definite", "part_id": str, "existing_qty": int} on exact match
        {"status": "possible", "candidates": [{"part_id": str, "mpn": str,
                                                "manufacturer": str, "score": float}]}
        {"status": "new"} otherwise
    """
    if not (mpn or "").strip():
        return {"status": "new"}

    norm = _normalize_mpn(mpn)

    # Exact match (normalized)
    rows = db.execute(
        "SELECT part_id, mpn, manufacturer FROM parts"
    ).fetchall()
    for r in rows:
        if _normalize_mpn(r["mpn"]) == norm and norm:
            stock = db.execute(
                "SELECT quantity FROM stock WHERE part_id=?", (r["part_id"],)
            ).fetchone()
            return {
                "status": "definite",
                "part_id": r["part_id"],
                "existing_qty": (stock["quantity"] if stock else 0),
            }

    # Fuzzy match: difflib pre-filter, then Levenshtein ≤2
    candidates = []
    for r in rows:
        other = _normalize_mpn(r["mpn"])
        if not other:
            continue
        ratio = difflib.SequenceMatcher(None, norm, other).ratio()
        if ratio < 0.7:
            continue
        dist = _levenshtein(norm, other)
        if dist <= 2 or (len(norm) >= 6 and (norm in other or other in norm)):
            candidates.append({
                "part_id": r["part_id"],
                "mpn": r["mpn"],
                "manufacturer": r["manufacturer"],
                "score": ratio,
            })

    if candidates:
        candidates.sort(key=lambda c: -c["score"])
        return {"status": "possible", "candidates": candidates[:5]}
    return {"status": "new"}


# ── PO orchestration ─────────────────────────────────────────────────


def _existing_part_identifiers(ledger_csv: str, part_id: str) -> dict[str, str]:
    """Look up an existing part's identifier columns in the ledger so a new
    row for the same part can carry the same LCSC/Digikey/etc. codes (which
    drives the cache merge logic in inventory_ops.read_and_merge).
    """
    if not os.path.isfile(ledger_csv):
        return {}
    with open(ledger_csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            for col in ("LCSC Part Number", "Digikey Part Number",
                        "Pololu Part Number", "Mouser Part Number",
                        "Manufacture Part Number"):
                if (row.get(col) or "").strip() == part_id:
                    return {
                        "LCSC Part Number": row.get("LCSC Part Number", ""),
                        "Digikey Part Number": row.get("Digikey Part Number", ""),
                        "Pololu Part Number": row.get("Pololu Part Number", ""),
                        "Mouser Part Number": row.get("Mouser Part Number", ""),
                        "Manufacture Part Number": row.get("Manufacture Part Number", ""),
                    }
    return {}


def import_po(
    ledger_csv: str,
    po_csv: str,
    sources_dir: str,
    vendor_id: str,
    source_file_bytes: bytes | None,
    source_file_ext: str | None,
    purchase_date: str,
    notes: str,
    line_items: list[dict[str, Any]],
) -> dict[str, str]:
    """Create a PO record + append ledger rows tagged with the new po_id."""
    if not line_items:
        raise ValueError("line_items must not be empty")

    import purchase_orders

    new_po = purchase_orders.create_purchase_order(
        csv_path=po_csv,
        sources_dir=sources_dir,
        vendor_id=vendor_id,
        source_file_bytes=source_file_bytes,
        source_file_ext=source_file_ext,
        purchase_date=purchase_date,
        notes=notes,
    )

    # Build ledger rows. Use the canonical FIELDNAMES from InventoryApi to avoid
    # drift if data/constants.json is updated.
    from inventory_api import InventoryApi
    fieldnames = list(InventoryApi.FIELDNAMES)

    new_rows: list[dict[str, str]] = []
    for li in line_items:
        ids = {}
        if li.get("match") == "definite" and li.get("match_part_id"):
            ids = _existing_part_identifiers(ledger_csv, li["match_part_id"])
        row = {fn: "" for fn in fieldnames}
        if ids:
            row.update(ids)
        if not row.get("Manufacture Part Number"):
            row["Manufacture Part Number"] = li.get("mpn", "")
        row["Manufacturer"] = li.get("manufacturer", "")
        row["Package"] = li.get("package", "")
        qty = int(li.get("quantity") or 0)
        unit = float(li.get("unit_price") or 0.0)
        row["Quantity"] = str(qty)
        if unit > 0:
            row["Unit Price($)"] = f"{unit:.4f}"
            row["Ext.Price($)"] = f"{qty * unit:.2f}"
        row["po_id"] = new_po["po_id"]
        new_rows.append(row)

    # Append (creates file with header if missing; migrates if header is older)
    import csv_io
    csv_io.append_csv_rows(ledger_csv, fieldnames, new_rows)
    return new_po
