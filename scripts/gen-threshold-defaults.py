#!/usr/bin/env python3
"""Generate js/threshold-defaults.generated.js from data/constants.json.

The stock-value threshold a category colours with has one resolution order
(`domain/thresholds.py`): the user's override, then the shipped default, then
`BASE_THRESHOLD`. The middle table lives in `data/constants.json`, which the
frontend already reads — but through `js/constants.js`, whose top-level
`await fetch` makes that module unimportable from anything a test touches
(`js/store.js` is imported by thirty vitest files, each of which has to
`vi.mock` constants.js away). A threshold has to be known before the first row
renders, so it cannot arrive by a fetch that may not have landed.

So the table is generated into a plain module. Same arrangement, for the same
reason, as `scripts/gen-inventory-types.py` / `js/inventory-record.d.ts` and
`scripts/gen-property-schema.py`: two hand-maintained copies of one table is
one copy too many, and the drifting one is the one nobody is looking at.

Generation is also the validation step: a key that is not a section the app
knows about fails here rather than shipping a default nothing can ever select.

Usage:
    python scripts/gen-threshold-defaults.py          # write the module
    python scripts/gen-threshold-defaults.py --check  # exit 1 if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "js" / "threshold-defaults.generated.js"

sys.path.insert(0, str(REPO_ROOT))

from domain import thresholds  # noqa: E402
from domain.inventory import parse_section_order  # noqa: E402

_BANNER = """/* GENERATED FILE - do not edit.
 *
 * Source: data/constants.json's DEFAULT_THRESHOLDS and domain/thresholds.py's
 *         BASE_THRESHOLD, FAMILY_THRESHOLDS, BASE_BY_DECISION, PRESETS,
 *         PRESET_BUNDLES and PRESET_INFO.
 * Write:  python scripts/gen-threshold-defaults.py
 * Guard:  python scripts/gen-threshold-defaults.py --check   (verify.sh + CI)
 *
 * Generated because js/constants.js fetches data/constants.json at top level,
 * which no module a vitest file imports can depend on. The resolution order
 * this feeds lives in js/threshold-logic.js, mirrored in domain/thresholds.py,
 * and both are pinned against tests/fixtures/thresholds/legacy-defaults.json.
 *
 * The derivation tables (family prefixes, base-by-decision) are generated from
 * the Python originals for the same reason two hand-maintained copies of one
 * table is one copy too many: the drifting one is the one nobody is looking
 * at. The *matching* (prefix match, decision-first) is mirrored by hand in
 * both languages and pinned by the shared fixture's derived cases.
 */

// @ts-check

/** The threshold of last resort: a category no table names and no family rule
 *  has an opinion about. */
export const BASE_THRESHOLD = {base};

/** Shipped per-category defaults - only the categories that differ from
 *  BASE_THRESHOLD. A "Parent > Sub" section inherits its parent's entry.
 *  @type {{Readonly<Record<string, number>>}} */
export const DEFAULT_THRESHOLDS = Object.freeze({{
{entries}
}});

/** Family-class derivation: prefix -> threshold, longest prefix wins, applied
 *  only when no override, preset or default names the category. Mirrors
 *  domain/thresholds.py's FAMILY_THRESHOLDS.
 *  @type {{ReadonlyArray<Readonly<[string, number]>>}} */
export const FAMILY_THRESHOLDS = Object.freeze([
{family}
]);

/** Categories the hand-tuned era left at the base ON PURPOSE - derivation must
 *  not re-decide them, and the legacy fixture pins them. Mirrors
 *  domain/thresholds.py's BASE_BY_DECISION. @type {{ReadonlySet<string>}} */
export const BASE_BY_DECISION = Object.freeze(new Set([
{decisions}
]));

/** Preset ids, in picker order. The first is the shipped sentinel (no bundle).
 *  Mirrors domain/thresholds.py's PRESETS. @type {{ReadonlyArray<string>}} */
export const PRESETS = Object.freeze({presets});

/** Sparse preset bundles - only the categories a bundle moves, in the same
 *  shape as DEFAULT_THRESHOLDS (" > " parent inheritance included).
 *  Mirrors domain/thresholds.py's PRESET_BUNDLES.
 *  @type {{Readonly<Record<string, Readonly<Record<string, number>>>>}} */
export const PRESET_BUNDLES = Object.freeze({{
{bundles}
}});

/** Display name + description per preset id, for the Preferences picker.
 *  Mirrors domain/thresholds.py's PRESET_INFO.
 *  @typedef {{{{ name: string, description: string }}}} PresetInfo */
/** @type {{Readonly<Record<string, PresetInfo>>}} */
export const PRESET_INFO = Object.freeze({{
{info}
}});
"""


def _render() -> str:
    constants = json.loads(
        (REPO_ROOT / "data" / "constants.json").read_text(encoding="utf-8")
    )
    defaults = constants["DEFAULT_THRESHOLDS"]
    flat_order, _ = parse_section_order(constants["SECTION_ORDER"])
    known = set(flat_order)

    unknown = sorted(set(defaults) - known)
    if unknown:
        raise SystemExit(
            "data/constants.json DEFAULT_THRESHOLDS names sections the app has "
            f"no such category for: {unknown}\n"
            "A default nothing can ever select is a typo; fix the key or add the "
            "category to SECTION_ORDER."
        )
    redundant = sorted(k for k, v in defaults.items() if v == thresholds.BASE_THRESHOLD)
    if redundant:
        raise SystemExit(
            "DEFAULT_THRESHOLDS repeats BASE_THRESHOLD for "
            f"{redundant}; the base already says that. Listing every category "
            "makes the base untestable and turns a future default change into a "
            "diff over the whole vocabulary."
        )

    entries = ",\n".join(
        f'  {json.dumps(section)}: {int(value)}' for section, value in sorted(defaults.items())
    )
    family = ",\n".join(
        f'  [{json.dumps(prefix)}, {int(value)}]'
        for prefix, value in thresholds.FAMILY_THRESHOLDS
    )
    decisions = ",\n".join(
        f"  {json.dumps(section)}" for section in sorted(thresholds.BASE_BY_DECISION)
    )
    bundles = ",\n".join(
        "  {}: Object.freeze({{\n{}\n  }})".format(
            json.dumps(preset_id),
            ",\n".join(
                f'    {json.dumps(section)}: {int(value)}'
                for section, value in sorted(bundle.items())
            ),
        )
        for preset_id, bundle in thresholds.PRESET_BUNDLES.items()
    )
    info = ",\n".join(
        "  {}: Object.freeze({{ name: {}, description: {} }})".format(
            json.dumps(preset_id),
            json.dumps(meta["name"]),
            json.dumps(meta["description"]),
        )
        for preset_id, meta in thresholds.PRESET_INFO.items()
    )
    return _BANNER.format(
        base=thresholds.BASE_THRESHOLD,
        entries=entries,
        family=family,
        decisions=decisions,
        presets=json.dumps(list(thresholds.PRESETS)),
        bundles=bundles,
        info=info,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the module is stale")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rendered = _render()
    if args.check:
        if not args.out.exists():
            print(f"missing {args.out.relative_to(REPO_ROOT)} — run "
                  "`python scripts/gen-threshold-defaults.py`")
            return 1
        if args.out.read_text(encoding="utf-8") != rendered:
            print(f"{args.out.relative_to(REPO_ROOT)} is stale — run "
                  "`python scripts/gen-threshold-defaults.py` and commit.")
            return 1
        return 0

    args.out.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
