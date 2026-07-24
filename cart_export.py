"""Serialize a cart to LCSC/DigiKey CSV or a paste list."""
from __future__ import annotations

import csv
import io

_LCSC_HEADER = ["Index", "LCSC#", "MPN", "Manufacturer", "Package", "Customer #",
                "Description", "RoHS", "Quantity", "MOQ", "Multiple",
                "Unit Price($)", "Extended Price($)", "Product Link"]
_DIGIKEY_HEADER = ["Index", "DigiKey Part #", "Manufacturer Part Number", "Manufacturer",
                   "Description", "Customer Reference", "Quantity", "Backorder",
                   "Unit Price", "Extended Price"]


def build(items, distributor, fmt, resolve_pn, part_meta):
    resolved, unresolved = [], []
    for it in items:
        pn = resolve_pn(it.get("part_id"), distributor)
        if pn:
            resolved.append((it, pn))
        else:
            unresolved.append({"ref": it["ref"], "part_id": it.get("part_id"), "raw": it.get("raw")})

    if fmt == "paste":
        content = "\n".join(f"{pn}\t{it['qty']}" for it, pn in resolved)
        return {"content": content, "unresolved": unresolved, "filename": f"cart_{distributor}.txt"}

    buf = io.StringIO()
    w = csv.writer(buf)
    if distributor == "lcsc":
        w.writerow(_LCSC_HEADER)
        for i, (it, pn) in enumerate(resolved, start=1):
            m = part_meta(it.get("part_id")) or {}
            w.writerow([i, pn, m.get("mpn", ""), m.get("manufacturer", ""), m.get("package", ""),
                        "", m.get("description", ""), "yes", it["qty"], 1, 1, "", "", ""])
    elif distributor == "digikey":
        w.writerow(_DIGIKEY_HEADER)
        for i, (it, pn) in enumerate(resolved, start=1):
            m = part_meta(it.get("part_id")) or {}
            w.writerow([i, pn, m.get("mpn", ""), m.get("manufacturer", ""),
                        m.get("description", ""), "", it["qty"], "", "", ""])
    else:
        raise ValueError(f"unknown distributor {distributor!r}")
    return {"content": buf.getvalue(), "unresolved": unresolved, "filename": f"cart_{distributor}.csv"}
