"""CSV I/O utilities: reading, writing, migration, encoding fixes."""

from __future__ import annotations

import csv
import io
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def append_csv_rows(path: str, fieldnames: list[str],
                    rows: list[dict[str, Any]]) -> None:
    """Append rows to a CSV file, writing header if the file is new.

    If the file exists with an older header (fewer columns), migrates it
    to the new schema before appending.
    """
    if os.path.exists(path):
        migrate_csv_header(path, fieldnames)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            for row in rows:
                writer.writerow(row)
    else:
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


def migrate_csv_header(path: str, expected_fieldnames: list[str]) -> None:
    """If a CSV file has an older header, rewrite it with the new schema."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        existing_fields = reader.fieldnames or []
        if set(expected_fieldnames) == set(existing_fields):
            return
        existing_rows = list(reader)

    # Rewrite with new header, filling missing fields with ""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=expected_fieldnames)
        writer.writeheader()
        for row in existing_rows:
            migrated = {fn: row.get(fn, "") for fn in expected_fieldnames}
            writer.writerow(migrated)


def fix_double_utf8(text: str) -> str:
    """Fix double-encoded UTF-8 text."""
    for enc in ("cp1252", "latin-1"):
        try:
            return text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def read_text(path: str) -> str:
    """Read a text file, auto-detecting UTF-16 vs UTF-8 encoding."""
    with open(path, "rb") as f:
        bom = f.read(2)
    encoding = "utf-16" if bom in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    with open(path, encoding=encoding) as f:
        return f.read()


def convert_xls_to_csv(path: str) -> dict[str, Any] | None:
    """Convert a binary XLS file to CSV text for the import panel.

    Finds the header row automatically, extracts data rows, and
    returns {csv_text, headers, row_count}.
    """
    import xlrd

    wb = xlrd.open_workbook(path)
    sh = wb.sheet_by_index(0)

    # Find the header row: look for a row containing a recognisable header keyword
    header_row_idx = None
    for i in range(min(20, sh.nrows)):
        row_vals = [str(sh.cell_value(i, j)).strip() for j in range(sh.ncols)]
        lower_vals = [v.lower() for v in row_vals]
        if any("mouser" in v or "mfr" in v or "digikey" in v or "lcsc" in v
               for v in lower_vals):
            header_row_idx = i
            break

    if header_row_idx is None:
        # Fallback: first row with >3 non-empty cells
        for i in range(sh.nrows):
            row_vals = [str(sh.cell_value(i, j)).strip() for j in range(sh.ncols)]
            if sum(1 for v in row_vals if v) > 3:
                header_row_idx = i
                break

    if header_row_idx is None:
        return None

    headers = [str(sh.cell_value(header_row_idx, j)).strip() for j in range(sh.ncols)]

    # Extract data rows (skip blanks and footer rows)
    rows = []
    for i in range(header_row_idx + 1, sh.nrows):
        row_vals = [str(sh.cell_value(i, j)).strip() for j in range(sh.ncols)]
        # Clean up float formatting from xlrd (e.g. "10.0" -> "10")
        for k, v in enumerate(row_vals):
            if v.endswith(".0") and v[:-2].isdigit():
                row_vals[k] = v[:-2]
        joined = "".join(row_vals)
        if not joined:
            continue
        # Skip footer/summary rows
        if any(kw in joined.lower() for kw in
               ("submitting", "prices are", "merchandise", "shipping charge")):
            continue
        # Skip rows where the likely part-number column is empty
        if len(row_vals) > 1 and not row_vals[1]:
            continue
        rows.append(row_vals)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)

    return {"csv_text": output.getvalue(), "headers": headers, "row_count": len(rows)}
