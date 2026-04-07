"""Inventory operations: merge, adjust, categorize, sort, rebuild."""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from typing import Any

from categorize import categorize, parse_capacitance, parse_inductance, parse_resistance
from csv_io import append_csv_rows, fix_double_utf8
from price_ops import derive_missing_price, parse_price, parse_qty

logger = logging.getLogger(__name__)


def get_part_key(row: dict[str, str]) -> str:
    """Return best unique identifier: LCSC (C-prefixed) > MPN > Digikey PN > Pololu PN > Mouser PN."""
    lcsc = (row.get("LCSC Part Number") or "").strip()
    if lcsc and lcsc.upper().startswith("C"):
        return lcsc
    mpn = (row.get("Manufacture Part Number") or "").strip()
    if mpn:
        return mpn
    dk = (row.get("Digikey Part Number") or "").strip()
    if dk:
        return dk
    pololu = (row.get("Pololu Part Number") or "").strip()
    if pololu:
        return pololu
    mouser = (row.get("Mouser Part Number") or "").strip()
    if mouser:
        return mouser
    return ""


def read_and_merge(purchase_csv: str,
                   fieldnames: list[str]) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Read purchase_ledger.csv, fix encoding, merge duplicates.
    Returns (fieldnames, merged_dict).
    """
    if not os.path.exists(purchase_csv):
        return list(fieldnames), {}

    with open(purchase_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        file_fieldnames = reader.fieldnames
        rows = list(reader)

    # Fix double-encoded descriptions
    for r in rows:
        for field in ("Description", "Package"):
            if r.get(field):
                r[field] = fix_double_utf8(r[field])

    # Merge duplicates by part key
    merged: dict[str, dict[str, str]] = {}
    for r in rows:
        pn = get_part_key(r)
        if not pn:
            continue
        qty = parse_qty(r.get("Quantity"))
        ext = parse_price(r.get("Ext.Price($)"))
        if pn in merged:
            prev_qty = parse_qty(merged[pn]["Quantity"])
            merged[pn]["Quantity"] = str(prev_qty + qty)
            new_ext = parse_price(merged[pn]["Ext.Price($)"]) + ext
            merged[pn]["Ext.Price($)"] = f"{new_ext:.2f}"
            old_up = parse_price(merged[pn]["Unit Price($)"])
            new_up = parse_price(r.get("Unit Price($)"))
            if new_up > 0 and new_up < old_up:
                merged[pn]["Unit Price($)"] = r["Unit Price($)"]
        else:
            r_copy = dict(r)
            r_copy["Quantity"] = str(qty)
            merged[pn] = r_copy

    # Derive missing price from the other price field + qty
    for part in merged.values():
        up = parse_price(part.get("Unit Price($)")) or None
        ext = parse_price(part.get("Ext.Price($)")) or None
        qty = parse_qty(part.get("Quantity"))
        up, ext = derive_missing_price(up, ext, qty)
        if up is not None:
            part["Unit Price($)"] = f"{up:.4f}"
        if ext is not None:
            part["Ext.Price($)"] = f"{ext:.2f}"

    return file_fieldnames, merged


def apply_adjustments(merged: dict[str, dict[str, str]],
                      adjustments_csv: str,
                      fieldnames: list[str]) -> None:
    """Apply adjustments.csv entries to merged dict."""
    if not os.path.exists(adjustments_csv):
        return
    with open(adjustments_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            adj_type = (row.get("type") or "").strip()
            pn = (row.get("lcsc_part") or "").strip()
            if not pn or not adj_type:
                continue
            try:
                qty = int(float(row.get("quantity", "0")))
            except ValueError:
                logger.warning("Skipping adjustment: malformed quantity %r for part %s", row.get("quantity"), pn)
                continue

            if pn not in merged:
                if adj_type == "set" and qty > 0:
                    merged[pn] = {fn: "" for fn in fieldnames}
                    if pn.upper().startswith("C") and pn[1:].isdigit():
                        merged[pn]["LCSC Part Number"] = pn
                    else:
                        merged[pn]["Manufacture Part Number"] = pn
                    merged[pn]["Quantity"] = "0"
                else:
                    continue

            current = parse_qty(merged[pn]["Quantity"])
            if adj_type == "set":
                new_qty = max(0, qty)
            elif adj_type in ("consume", "add", "remove"):
                new_qty = max(0, current + qty)
            else:
                continue
            merged[pn]["Quantity"] = str(new_qty)


def sort_key_for_section(section: str, description: str) -> float | None:
    """Return numeric sort key for a part within its section, or None."""
    if "Resistor" in section:
        return parse_resistance(description)
    elif "Capacitor" in section:
        return parse_capacitance(description)
    elif "Inductor" in section:
        return parse_inductance(description)
    return None


def categorize_and_sort(parts: list[dict[str, str]],
                        flat_section_order: list[str] | None = None) -> dict[str, list[dict[str, str]]]:
    """Categorize parts and sort within sections."""
    categorized: dict[str, list[dict[str, str]]] = {}
    for p in parts:
        cat = categorize(p)
        categorized.setdefault(cat, []).append(p)

    # Sort passives by value -- applies to both bare and compound section names
    for section, items in categorized.items():
        if "Resistor" in section:
            items.sort(key=lambda r: parse_resistance(r.get("Description", "")))
        elif "Capacitor" in section:
            items.sort(key=lambda r: parse_capacitance(r.get("Description", "")))
        elif "Inductor" in section:
            items.sort(key=lambda r: parse_inductance(r.get("Description", "")))
    return categorized


def write_organized(categorized: dict[str, list[dict[str, str]]],
                    output_csv: str, fieldnames: list[str],
                    flat_section_order: list[str]) -> None:
    """Write inventory.csv."""
    with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Section"] + list(fieldnames))
        for section in flat_section_order:
            items = categorized.get(section)
            if not items:
                continue
            writer.writerow([])
            writer.writerow([f"=== {section} ==="] + [""] * len(fieldnames))
            for item in items:
                writer.writerow([section] + [item.get(fn, "") for fn in fieldnames])


def rebuild(purchase_csv: str, adjustments_csv: str, output_csv: str,
            fieldnames: list[str], flat_section_order: list[str]) -> list[dict[str, Any]]:
    """Full rebuild pipeline: merge -> adjust -> categorize -> sort -> write.
    Returns fresh inventory list.
    """
    file_fieldnames, merged = read_and_merge(purchase_csv, fieldnames)
    apply_adjustments(merged, adjustments_csv, file_fieldnames)
    parts = list(merged.values())
    categorized = categorize_and_sort(parts)
    write_organized(categorized, output_csv, file_fieldnames, flat_section_order)
    return load_organized(output_csv)


# Map JS field names to CSV column names
_FIELD_TO_COL = {
    "lcsc": "LCSC Part Number",
    "digikey": "Digikey Part Number",
    "pololu": "Pololu Part Number",
    "mouser": "Mouser Part Number",
    "mpn": "Manufacture Part Number",
    "manufacturer": "Manufacturer",
    "package": "Package",
    "description": "Description",
}


def load_organized(output_csv: str) -> list[dict[str, Any]]:
    """Load organized inventory as list of dicts for JSON."""
    rows: list[dict[str, Any]] = []
    if not os.path.exists(output_csv):
        return rows
    with open(output_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            section = (row.get("Section") or "").strip()
            lcsc = (row.get("LCSC Part Number") or "").strip()
            mpn = (row.get("Manufacture Part Number") or "").strip()
            if section.startswith("=") or lcsc.startswith("="):
                continue
            pololu = (row.get("Pololu Part Number") or "").strip()
            digikey = (row.get("Digikey Part Number") or "").strip()
            mouser = (row.get("Mouser Part Number") or "").strip()
            if not lcsc and not mpn and not digikey and not pololu and not mouser:
                continue
            rows.append({
                "section": section,
                "lcsc": lcsc,
                "mpn": mpn,
                "digikey": digikey,
                "pololu": pololu,
                "mouser": mouser,
                "manufacturer": (row.get("Manufacturer") or "").strip(),
                "package": (row.get("Package") or "").strip(),
                "description": (row.get("Description") or "").strip(),
                "qty": parse_qty(row.get("Quantity")),
                "unit_price": parse_price(row.get("Unit Price($)")),
                "ext_price": parse_price(row.get("Ext.Price($)")),
            })
    return rows


def append_adjustment(adjustments_csv: str, adj_fieldnames: list[str],
                      adj_type: str, part_key: str, quantity: int,
                      note: str = "", bom_file: str = "",
                      board_qty: int | str = "", source: str = "") -> None:
    """Append one row to adjustments.csv."""
    append_csv_rows(adjustments_csv, adj_fieldnames, [{
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "type": adj_type,
        "lcsc_part": part_key,
        "quantity": quantity,
        "bom_file": bom_file,
        "board_qty": board_qty,
        "note": note,
        "source": source,
    }])


def rollback_source(adjustments_csv: str, source: str) -> list[dict]:
    """Remove all adjustments with the given source tag.

    Returns (kept_rows, removed_rows). Caller is responsible for
    writing kept_rows back and triggering a rebuild.
    """
    if not source:
        raise ValueError("source must not be empty")
    if not os.path.exists(adjustments_csv):
        return []

    with open(adjustments_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    kept = []
    removed = []
    for row in rows:
        if (row.get("source") or "") == source:
            removed.append(row)
        else:
            kept.append(row)

    if not removed:
        return []

    with open(adjustments_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)

    logger.info("Rolled back %d adjustment(s) with source=%r", len(removed), source)
    return removed


def truncate_csv(csv_path: str, count: int, label: str) -> tuple[list[str], list[dict]]:
    """Remove the last *count* rows from a CSV.

    Returns (fieldnames, remaining_rows) so the caller can write them
    under its own lock.
    """
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")

    if not os.path.exists(csv_path):
        raise ValueError(f"No {label} file found")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if count > len(rows):
        raise ValueError(
            f"Cannot remove {count} rows: {label} only has {len(rows)} rows"
        )

    rows = rows[:-count]
    return fieldnames, rows
