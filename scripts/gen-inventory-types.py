#!/usr/bin/env python3
"""Generate js/inventory-record.d.ts from domain.schema.INVENTORY_FIELDS.

Usage:
    python scripts/gen-inventory-types.py          # write the .d.ts
    python scripts/gen-inventory-types.py --check  # exit 1 if the .d.ts is stale

Mirrors scripts/gen-code-map.py's --check pattern exactly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "js" / "inventory-record.d.ts"

# Make sure the repo root is on sys.path so `domain` is importable.
sys.path.insert(0, str(REPO_ROOT))

from domain.schema import INVENTORY_FIELDS  # noqa: E402

# TS type mapping for PartHistoryEntry fields (derived from the TypedDict in schema.py)
_PART_HISTORY_ENTRY_TS_FIELDS: list[tuple[str, str]] = [
    ("timestamp", "string"),
    ("kind", "string"),
    ("qty_delta", "number"),
    ("source", "string"),
    ("note", "string"),
]


def render_dts() -> str:
    """Render the TypeScript interface declarations as a string."""
    lines = [
        "// AUTO-GENERATED — do not edit by hand.",
        "// Source of truth: domain/schema.py :: INVENTORY_FIELDS + PartHistoryEntry",
        "// Regenerate: python scripts/gen-inventory-types.py",
        "",
        "export interface InventoryItem {",
    ]
    for f in INVENTORY_FIELDS:
        if not f.to_js:
            continue
        lines.append(f"  {f.py_key}: {f.ts_type};")
    lines.append("}")
    lines.append("")
    lines.append("export interface PartHistoryEntry {")
    for py_key, ts_type in _PART_HISTORY_ENTRY_TS_FIELDS:
        lines.append(f"  {py_key}: {ts_type};")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help="Output path for the .d.ts (default: js/inventory-record.d.ts)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Exit 1 if the output file is missing or stale (does not write)",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    rendered = render_dts()

    if args.check:
        if not out.exists():
            print(
                f"error: {out} does not exist. "
                "Run `python scripts/gen-inventory-types.py` and commit.",
                file=sys.stderr,
            )
            return 1
        existing = out.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                f"error: {out} is stale. "
                "Run `python scripts/gen-inventory-types.py` and commit.",
                file=sys.stderr,
            )
            return 1
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
