"""File dialog operations: open, save, load, and column detection."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from csv_io import convert_xls_to_csv, read_text
from price_ops import ensure_parsed

logger = logging.getLogger(__name__)


def detect_columns(headers_json: str | list[str]) -> dict[str, str]:
    """Auto-detect column mapping for purchase CSV import.
    Returns dict of {source_column_index: target_inventory_field}.
    """
    headers = ensure_parsed(headers_json)
    lower_headers = [h.lower().strip() for h in headers]

    # Collect candidates for each target field
    candidates: dict[str, list[int]] = {}
    for i, h in enumerate(lower_headers):
        if "lcsc" in h:
            candidates.setdefault("LCSC Part Number", []).append(i)
        if "digikey" in h or "digi-key" in h:
            candidates.setdefault("Digikey Part Number", []).append(i)
        if "pololu" in h:
            candidates.setdefault("Pololu Part Number", []).append(i)
        if "mouser" in h:
            candidates.setdefault("Mouser Part Number", []).append(i)
        if h == "mpn" or ("manufactur" in h and "part" in h) or ("mfr" in h and ("part" in h or "#" in h)):
            candidates.setdefault("Manufacture Part Number", []).append(i)
        if ("manufacturer" in h or h.startswith("mfr")) and "part" not in h and "#" not in h:
            candidates.setdefault("Manufacturer", []).append(i)
        # Prefer "shipped" quantity over "ordered" over generic
        if "shipped" in h:
            candidates.setdefault("Quantity", []).insert(0, i)
        elif "quantity" in h or "qty" in h:
            candidates.setdefault("Quantity", []).append(i)
        if "description" in h:
            candidates.setdefault("Description", []).append(i)
        if "package" in h:
            candidates.setdefault("Package", []).append(i)
        if "unit price" in h or ("price" in h and "ext" not in h):
            candidates.setdefault("Unit Price($)", []).append(i)
        if ("ext" in h and ("price" in h or "usd" in h)) or "extended price" in h:
            candidates.setdefault("Ext.Price($)", []).append(i)
        if "rohs" in h:
            candidates.setdefault("RoHS", []).append(i)
        if "customer" in h:
            candidates.setdefault("Customer NO.", []).append(i)

    # Assign one source column per target (no duplicates)
    mapping: dict[str, str] = {}
    used_indices: set[int] = set()
    target_order = [
        "LCSC Part Number", "Digikey Part Number", "Pololu Part Number",
        "Mouser Part Number", "Manufacture Part Number",
        "Manufacturer", "Quantity", "Description", "Package",
        "Unit Price($)", "Ext.Price($)", "RoHS", "Customer NO.",
    ]
    for target in target_order:
        for idx in candidates.get(target, []):
            if idx not in used_indices:
                mapping[str(idx)] = target
                used_indices.add(idx)
                break

    return mapping


def open_file_dialog(title: str = "Select CSV file",
                     default_dir: str | None = None) -> dict[str, Any] | None:
    """Open native file dialog, return {name, content, directory, path} or None."""
    import webview
    kwargs = {"file_types": (
        "CSV Files (*.csv)", "TSV Files (*.tsv)",
        "Text Files (*.txt)", "Excel Files (*.xls)",
    )}
    if default_dir and os.path.isdir(default_dir):
        kwargs["directory"] = default_dir
    result = webview.windows[0].create_file_dialog(
        webview.FileDialog.OPEN,
        **kwargs,
    )
    if result and len(result) > 0:
        path = result[0]
        # Handle XLS files by converting to CSV
        if path.lower().endswith(".xls"):
            xls_data = convert_xls_to_csv(path)
            content = xls_data["csv_text"] if xls_data else ""
        else:
            content = read_text(path)
        resp = {
            "name": os.path.basename(path),
            "content": content,
            "directory": os.path.dirname(path),
            "path": path,
        }
        # Check for sidecar .links.json
        links_path = os.path.splitext(path)[0] + ".links.json"
        if os.path.exists(links_path):
            try:
                with open(links_path, encoding="utf-8") as lf:
                    resp["links"] = json.load(lf)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read sidecar links: %s", exc)
        return resp
    return None


def save_file_dialog(content: str, default_name: str = "export.csv",
                     default_dir: str | None = None,
                     links_json: str | list | None = None) -> dict[str, str] | None:
    """Open native Save As dialog and write content to the chosen path.
    If links_json is provided, writes a .links.json sidecar file next to the CSV.
    Returns {"path": chosen_path} on success, None if cancelled.
    """
    import webview
    kwargs = {"file_types": ("CSV Files (*.csv)",)}
    if default_dir and os.path.isdir(default_dir):
        kwargs["directory"] = default_dir
    if default_name:
        kwargs["save_filename"] = default_name
    result = webview.windows[0].create_file_dialog(
        webview.FileDialog.SAVE,
        **kwargs,
    )
    if result:
        path = result if isinstance(result, str) else result[0]
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write(content)
        # Write sidecar links file
        if links_json:
            links = ensure_parsed(links_json)
            if links:
                links_path = os.path.splitext(path)[0] + ".links.json"
                with open(links_path, "w", encoding="utf-8") as f:
                    json.dump(links, f, indent=2)
        return {"path": path}
    return None


def load_file(path: str) -> dict[str, Any] | None:
    """Load a file by path, return {name, content, directory, path, links?} or None."""
    if not path or not os.path.isfile(path):
        return None
    resp = {
        "name": os.path.basename(path),
        "content": read_text(path),
        "directory": os.path.dirname(path),
        "path": path,
    }
    # Check for sidecar .links.json
    links_path = os.path.splitext(path)[0] + ".links.json"
    if os.path.exists(links_path):
        try:
            with open(links_path, encoding="utf-8") as lf:
                resp["links"] = json.load(lf)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read sidecar links: %s", exc)
    return resp
