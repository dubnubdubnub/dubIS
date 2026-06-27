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
import typing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "js" / "inventory-record.d.ts"

# Make sure the repo root is on sys.path so `domain` is importable.
sys.path.insert(0, str(REPO_ROOT))

from domain.schema import INVENTORY_FIELDS, PartHistoryEntry  # noqa: E402

# TS type mapping for Python annotation types → TypeScript types.
_PY_TO_TS: dict[str, str] = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
}


def _ts_type_for(annotation) -> str:
    """Convert a Python type annotation to a TypeScript type string."""
    name = getattr(annotation, "__name__", None) or str(annotation)
    return _PY_TO_TS.get(name, "string")


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
    for py_key, annotation in typing.get_type_hints(PartHistoryEntry).items():
        lines.append(f"  {py_key}: {_ts_type_for(annotation)};")
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
