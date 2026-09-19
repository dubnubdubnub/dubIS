#!/usr/bin/env python3
"""Generate tests/js/property/inventory-schema.generated.mjs from domain/schema.py.

The JS property tests need the same schema the Python ones derive their
generators from.  Python can import ``domain.schema``; vitest cannot, so the
field table is generated into a JS module and guarded for staleness — the same
arrangement as ``scripts/gen-inventory-types.py`` / ``js/inventory-record.d.ts``,
for the same reason: two hand-maintained copies of a field list is one copy too
many, and the one that drifts is always the one nobody is looking at.

What travels is deliberately more than the ``.d.ts`` carries.  A generator needs
to know whether a ``number`` is an integer (``qty``) or a fractional amount
(``unit_price``), which ``ts_type`` alone cannot say; ``domain/schema.py`` does
say it, in the type of the declared default, so that is what this script reads
and emits as ``jsType``.  A field whose (ts_type, default type) pair is not in
``_JS_TYPE_BY_SHAPE`` fails the generation rather than being guessed at — the
same guard ``tests/python/strategies.py`` applies on its side.

Usage:
    python scripts/gen-property-schema.py          # write the module
    python scripts/gen-property-schema.py --check  # exit 1 if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "tests" / "js" / "property" / "inventory-schema.generated.mjs"

sys.path.insert(0, str(REPO_ROOT))

from domain.schema import INVENTORY_FIELDS  # noqa: E402

# (ts_type, type of declared default) -> the generator-facing JS type name.
# Kept in lockstep with _STRATEGY_BY_SHAPE in tests/python/strategies.py.
_JS_TYPE_BY_SHAPE: dict[tuple[str, type], str] = {
    ("string", str): "string",
    ("number", int): "int",
    ("number", float): "float",
    ("string[]", list): "string[]",
}


class UnknownFieldShape(Exception):
    """A schema field this script has no JS type name for."""


def js_type_for(field) -> str:
    shape = (field.ts_type, type(field.default))
    try:
        return _JS_TYPE_BY_SHAPE[shape]
    except KeyError:
        raise UnknownFieldShape(
            f"domain/schema.py field {field.py_key!r} declares ts_type="
            f"{field.ts_type!r} with a {type(field.default).__name__} default. "
            "scripts/gen-property-schema.py has no JS type name for that shape — "
            "add one to _JS_TYPE_BY_SHAPE and the matching strategy to "
            "_STRATEGY_BY_SHAPE in tests/python/strategies.py, so both halves of "
            "the property harness learn the new field at the same time."
        ) from None


def render() -> str:
    fields = [
        {
            "key": f.py_key,
            "tsType": f.ts_type,
            "jsType": js_type_for(f),
            "default": f.default,
        }
        for f in INVENTORY_FIELDS
        if f.to_js
    ]
    body = json.dumps(fields, indent=2)
    return (
        "// AUTO-GENERATED — do not edit by hand.\n"
        "// Source of truth: domain/schema.py :: INVENTORY_FIELDS\n"
        "// Regenerate: python scripts/gen-property-schema.py\n"
        "//\n"
        "// The field table tests/js/property/arbitraries.mjs builds its inventory-record\n"
        "// arbitrary from. `jsType` splits the schema's single `number` into `int` and\n"
        "// `float` using the type of the declared default — see the script's docstring.\n"
        "\n"
        f"export const INVENTORY_FIELDS = /** @type {{const}} */ ({body});\n"
        "\n"
        "export const FIELD_KEYS = INVENTORY_FIELDS.map((f) => f.key);\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the generated module is stale")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out = Path(args.out)
    rendered = render()

    if args.check:
        if not out.exists():
            print(f"FAIL: {out.relative_to(REPO_ROOT)} does not exist — run "
                  "`python scripts/gen-property-schema.py`", file=sys.stderr)
            return 1
        if out.read_text(encoding="utf-8") != rendered:
            print(f"FAIL: {out.relative_to(REPO_ROOT)} is stale — domain/schema.py "
                  "changed. Run `python scripts/gen-property-schema.py` and commit.",
                  file=sys.stderr)
            return 1
        print(f"OK: {out.relative_to(REPO_ROOT)} is up to date")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(f"Wrote {out.relative_to(REPO_ROOT)} ({len(INVENTORY_FIELDS)} fields)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
