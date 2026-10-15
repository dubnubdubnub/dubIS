"""Stock-value thresholds — the one resolution order, stated once.

A threshold is the dollar value of a category's stock at which the qty cell is
fully green; `js/ui-helpers.js`'s `stockValueColor` ramps red→green across it,
and `dubis low-stock` flags a part whose qty is at or below its section's.

Three tables feed one number, and the order is the whole design:

1. **the user's override** — `preferences.json`'s `thresholds`, what the
   Preferences sliders write;
2. **the shipped default** — `data/constants.json`'s `DEFAULT_THRESHOLDS`, the
   per-category numbers the app has always been configured with by hand;
3. **`BASE_THRESHOLD`** — the floor for a category with neither.

A compound section ("Parent > Sub") inherits its parent at *each* level, so an
override on "Passives - Resistors" covers "… > Chip Resistors" without either
table having to list every child. That inheritance is not a convenience: the
inventory's section strings come from the part descriptions, and a sub-category
nobody typed anything about must not silently fall to the base.

## Why this lives in `domain/` and not in the frontend

The number used to exist only as `return 50` at the end of `js/store.js`'s
`getThreshold()`, and `dubis low-stock` had its own, different default of `0`
— so the same part was "low" for one caller and fine for the other, and the
divergence was documented in `curated.py` instead of fixed. One function, read
by both sides, is what removes the class of bug rather than the instance:

* the frontend mirrors it in `js/threshold-logic.js`, and both are pinned
  against the same legacy table (`tests/fixtures/thresholds/legacy-defaults.json`);
* the CLI reads the shipped defaults over `/v1/meta` rather than carrying a
  third copy — it is an HTTP client of `/v1`, not a reader of this checkout's
  data files, and a remote server's defaults are that server's business.

`DEFAULT_THRESHOLDS` deliberately holds only the categories that differ from
`BASE_THRESHOLD`. Listing all of them would make the base untestable and turn
every future default change into a diff over the whole vocabulary.
"""

from __future__ import annotations

from typing import Any, Mapping

# The separator between a section and its sub-category, as the inventory writes
# it and as `data/constants.json`'s SECTION_ORDER renders its children.
SECTION_SEPARATOR = " > "

# The threshold of last resort: a category with no override and no default.
BASE_THRESHOLD = 50


def parent_of(section: str) -> str:
    """The parent half of a compound section, or "" for a top-level one."""
    sep = section.find(SECTION_SEPARATOR)
    return section[:sep] if sep != -1 else ""


def resolve(
    section: str,
    overrides: Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | None = None,
    base: int | None = None,
) -> int:
    """The effective threshold for `section`.

    Exact override, then the override inherited from the parent, then the exact
    default, then the default inherited from the parent, then `base`
    (`BASE_THRESHOLD` when the caller does not name one).

    A parent's *override* outranks a child's *default* on purpose: the user
    spoke about that family, and nothing in the shipped table should overrule
    something they set deliberately.

    `base` exists for a caller that is not this build — `dubis low-stock` asks
    the server it is talking to what its base is, rather than assuming a remote
    server agrees with this checkout.
    """
    for table in (overrides, defaults):
        if not table:
            continue
        if section in table:
            return int(table[section])
        parent = parent_of(section)
        if parent and parent in table:
            return int(table[parent])
    return BASE_THRESHOLD if base is None else int(base)
