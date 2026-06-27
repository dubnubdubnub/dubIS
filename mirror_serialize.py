"""Inventory snapshot serialization — shared by poll_api and inventory_mirror.

Pure functions, stdlib only. No dubIS app imports so the mirror daemon can use it
standalone.
"""

import csv
import io
from collections import Counter

INVENTORY_CSV_FIELDS = [
    "section", "lcsc", "mpn", "digikey", "pololu", "mouser",
    "manufacturer", "package", "description", "qty",
    "unit_price", "ext_price", "primary_vendor_id",
]


def inventory_to_csv(inventory, fields=None):
    """Render an inventory list as CSV text."""
    fieldnames = fields if fields is not None else INVENTORY_CSV_FIELDS
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in inventory:
        writer.writerow(item)
    return buf.getvalue()


def inventory_stats(inventory):
    """Compute summary stats for an inventory list."""
    sections = Counter(item.get("section") or "" for item in inventory)
    total_qty = sum(int(item.get("qty") or 0) for item in inventory)
    return {
        "part_count": len(inventory),
        "total_qty": total_qty,
        "section_counts": dict(sections),
    }
