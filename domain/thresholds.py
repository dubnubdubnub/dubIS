"""Stock-value thresholds — the one resolution order, stated once.

A threshold is the dollar value of a category's stock at which the qty cell is
fully green; `js/ui-helpers.js`'s `stockValueColor` ramps red→green across it,
and `dubis low-stock` flags a part whose qty is at or below its section's.

## The resolution spec (the one place this is written down)

Effective value for a category, first hit wins, each table consulted exact
then parent-inherited ("Parent > Sub"):

1. **client override** — the window's own sparse deltas
   (`preferences.json`'s `thresholds`);
2. **client preset** — the bundle the window chose
   (`preferences.json`'s `threshold_preset`, contents served on `/v1/meta`);
3. **server active snapshot** — the profile *this server* says
   (`domain/threshold_snapshots.py`; `GET /v1/thresholds`'s `active.values`);
4. **snapshot 0** — the shipped default profile
   (`data/constants.json`'s `DEFAULT_THRESHOLDS`, `/v1/meta`'s
   `default_thresholds`) — which *is* the snapshot history's snapshot 0, not
   a separate entity;
5. **derivation** — the family tables (`derive`, see *Derivation* below);
6. **base** — `BASE_THRESHOLD`, or the caller's own base.

Levels 1–2 are the *client* layer (what this window was told to show), 3–4
the *server* layer (what this machine's profile is), 5–6 the fallbacks.
Within each layer the more specific beats the coarser: a per-category delta
outranks a bundle choice, the active snapshot outranks the shipped one. A
client table outranks a server table because the client spoke about *this
window* more recently and locally; the server snapshot outranks snapshot 0
because it is a deliberate, attributed, recorded choice over a build fact.

This spec is implemented by `resolve()` below and mirrored in
`js/threshold-logic.js`, and BOTH languages are pinned against every case by
the shared fixture `tests/fixtures/thresholds/resolution-cases.json` — the
agreement is the whole reason the fixture exists (the frontend once fell back
to $50 and `dubis low-stock` to 0, so the same part was low for one caller
and fine for the other).

That order predates per-server profiles: originally the three tables were
the user's override, the chosen preset, and the shipped default (the
per-category numbers the app has always been configured with by hand), with
derivation and the base behind them. The server active snapshot slots between
the preset and the shipped default; nothing about the old order moved.

A compound section ("Parent > Sub") inherits its parent at *each* level, so an
override on "Passives - Resistors" covers "… > Chip Resistors" without either
table having to list every child. That inheritance is not a convenience: the
inventory's section strings come from the part descriptions, and a sub-category
nobody typed anything about must not silently fall to the base.

## Derivation — why the base is no longer the answer to "unknown category"

The distributor-import work is bringing in a wave of categories that postdate
the hand-tuned table, and every one of them used to land on the flat
`BASE_THRESHOLD` — a $2 MCU reel and a $300 FPGA reel both "fine" at $50 of
stock. So a category with neither an override nor a default is now asked of
the *family tables below* before it is allowed to fall to the base:

* `FAMILY_THRESHOLDS` — prefix → value, longest prefix wins. Seeded from the
  operator's own hand edits (the record lives in the PR that added this):
  expensive IC classes went 2–4× up (mode of the tuned IC numbers: 100),
  passives stayed low (median of the tuned passives: 20), and the
  connector/discrete family kept its tuned 20.
* `BASE_BY_DECISION` — the categories the hand-tuned era *did* speak about and
  left at the base. Derivation must not re-decide them: the legacy fixture
  (`tests/fixtures/thresholds/legacy-defaults.json`) pins every one of these
  names at $50, and a family rule that silently moved them would be exactly
  the "new answer for an old category" change derivation exists to avoid.
  Membership here is a decision, and the guard test
  (`test_no_vocabulary_category_falls_to_the_base_silently`) is what makes
  every future base-landing category either derive or be *named* here.

A decision covers the exact category, never its children: "ICs - Power /
Voltage Regulators" staying at $50 is an opinion about that row, while a new
"… > DC-DC" under it is a category nobody has tuned and must extrapolate to
the IC family. That asymmetry is deliberate.

## Presets

Copy the cart-preset shape (`domain/cart_plan.py`), not the saved-view shape:
a chosen preset **id** plus sparse per-category deltas, never a materialised
threshold table. An unrecognised *stored* id falls back to `PRESET_SHIPPED`
(`stored_preset_id`) because a stale string in preferences.json must degrade
one preference, not make the app unreadable; an unrecognised *caller* id
raises (`bundle_for`) because a caller can fix its request and quietly serving
a different bundle than the one asked for is worse than saying no — the same
asymmetry as `plan_cart`'s `default_preset`.

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

# The threshold of last resort: a category no table names and no family rule
# has an opinion about.
BASE_THRESHOLD = 50

# ── Derivation tables ───────────────────────────────────────────────────────
# Prefix → threshold, longest prefix wins. Keyed on the section string alone,
# which is the only input the Python and JS sides share at call time; the JS
# mirror is GENERATED from these tables (scripts/gen-threshold-defaults.py),
# and the behaviour of both is pinned by the shared fixture.
#
# Seeded from the operator's hand edits, not invented:
#   ICs 100 — the mode of the tuned IC overrides (Amplifiers, ESD Protection,
#             Motor Drivers, USB, Load Switches, Switchers all went 50→100;
#             Microcontrollers and Sensors went to 200, which is what the
#             `ic-heavy` preset and per-category overrides are for).
#   Passives 20 — the median of the tuned passives (4 / 20 / 50).
#   Discrete Semiconductors + Connectors 20 — the connector/discrete default,
#             seeded from the tuned connector number.
FAMILY_THRESHOLDS: tuple[tuple[str, int], ...] = (
    ("ICs - ", 100),
    ("Passives - ", 20),
    ("Discrete Semiconductors", 20),
    ("Connectors", 20),
)

# Categories the hand-tuned era left at the base ON PURPOSE. Derivation skips
# these; the legacy fixture pins them. A new category that lands on the base
# must join this set (base by decision) or a family rule — silence is the one
# outcome the guard test forbids.
BASE_BY_DECISION: frozenset[str] = frozenset({
    "Development Boards, Kits, Programmers",
    "Discrete Semiconductors",
    "Discrete Semiconductors > MOSFETs",
    "ICs - Amplifiers",
    "ICs - ESD Protection",
    "ICs - Interface",
    "ICs - Microcontrollers",
    "ICs - Motor Drivers",
    "ICs - Power / Voltage Regulators",
    "ICs - Power / Voltage Regulators > LDOs",
    "ICs - Power / Voltage Regulators > Load Switches",
    "ICs - Power / Voltage Regulators > Switchers",
    "ICs - Sensors",
    "ICs - USB",
    "ICs - Voltage References",
    "LEDs",
    "Mechanical & Hardware",
    "Other",
    "Passives - Inductors",
    "Switches",
})

# ── Presets ─────────────────────────────────────────────────────────────────
PRESET_SHIPPED = "shipped"
"""No bundle: the resolution order is exactly the shipped tables."""

PRESET_IC_HEAVY = "ic-heavy"
"""The operator's own hand-tuned IC numbers, promoted to a bundle."""

PRESET_TIGHT = "tight"
"""Double every shipped number: a category stays amber until it holds twice
the stock, so thin shelves flag sooner."""

PRESETS: tuple[str, ...] = (PRESET_SHIPPED, PRESET_IC_HEAVY, PRESET_TIGHT)

# Sparse bundles — only the categories the bundle moves. Parent inheritance
# (" > ") applies to a bundle exactly as it does to an override table, so a
# bundle may name a parent to cover its children.
PRESET_BUNDLES: dict[str, dict[str, int]] = {
    PRESET_IC_HEAVY: {
        "ICs - Microcontrollers": 200,
        "ICs - Sensors": 200,
        "ICs - Amplifiers": 100,
        "ICs - ESD Protection": 100,
        "ICs - Motor Drivers": 100,
        "ICs - USB": 100,
        "ICs - Power / Voltage Regulators > Load Switches": 100,
        "ICs - Power / Voltage Regulators > Switchers": 100,
        "ICs - Power / Voltage Regulators > LDOs": 30,
    },
    PRESET_TIGHT: {
        "Connectors": 40,
        "Crystals & Oscillators": 20,
        "Diodes": 20,
        "Passives - Capacitors": 40,
        "Passives - Resistors": 8,
        "Discrete Semiconductors": 100,
        "LEDs": 100,
        "Mechanical & Hardware": 100,
        "Passives - Inductors": 100,
        "Switches": 100,
    },
}

PRESET_INFO: dict[str, dict[str, str]] = {
    PRESET_SHIPPED: {
        "name": "Server defaults",
        "description": "No bundle — the shipped table, derived families, and the base.",
    },
    PRESET_IC_HEAVY: {
        "name": "Expensive ICs",
        "description": (
            "IC classes carry their real price: MCUs and sensors at $200, the rest "
            "of the IC families at $100, LDOs pulled below the base to $30."
        ),
    },
    PRESET_TIGHT: {
        "name": "Flag sooner",
        "description": (
            "Twice the shipped number for every cheap category — a shelf stays "
            "amber until it holds double the stock."
        ),
    },
}


def parent_of(section: str) -> str:
    """The parent half of a compound section, or "" for a top-level one."""
    sep = section.find(SECTION_SEPARATOR)
    return section[:sep] if sep != -1 else ""


def derive(section: str) -> int | None:
    """The family-class threshold for a category no table names, or None.

    None means "no opinion", and the caller falls to the base — but only for a
    string outside every family. A category named in `BASE_BY_DECISION` also
    answers None: that is a decision to keep the base, not an absence of one,
    and the guard test in tests/python/domain/test_threshold_defaults.py is
    what keeps the two indistinguishable from the outside and very different
    in intent.
    """
    if section in BASE_BY_DECISION:
        return None
    best: tuple[str, int] | None = None
    for prefix, value in FAMILY_THRESHOLDS:
        if not section.startswith(prefix):
            continue
        if best is None or len(prefix) > len(best[0]):
            best = (prefix, value)
    return best[1] if best else None


def bundle_for(preset_id: str) -> dict[str, int]:
    """The bundle named by `preset_id`, for a CALLER: unknown raises.

    The `plan_cart(default_preset=...)` asymmetry — a caller can fix its
    request, so guessing a different bundle than the one asked for is worse
    than saying no.
    """
    if preset_id == PRESET_SHIPPED:
        return {}
    if preset_id in PRESET_BUNDLES:
        return PRESET_BUNDLES[preset_id]
    raise ValueError(
        f"unknown threshold preset {preset_id!r}; expected one of {PRESETS}"
    )


def stored_preset_id(raw: object) -> str:
    """A preset id read back from stored state: unrecognised falls back.

    The `_effective_preset` asymmetry — one stale string should degrade to the
    shipped order, not make the whole preference unreadable.
    """
    raw = raw.strip() if isinstance(raw, str) else ""
    return raw if raw in PRESETS else PRESET_SHIPPED


def presets_payload() -> list[dict[str, object]]:
    """The preset bundles as they travel on `GET /v1/meta`.

    Served there and NOT through /v1/preferences, because savePreferences
    posts the whole in-memory bag — defaults served through it would persist
    into preferences.json as user state on the next unrelated save (see
    js/store.js). Presets are read-only server facts, which is what /v1/meta
    already carries for defaults.
    """
    payload = [{"id": PRESET_SHIPPED, **PRESET_INFO[PRESET_SHIPPED], "thresholds": {}}]
    for preset_id in PRESETS:
        if preset_id == PRESET_SHIPPED:
            continue
        payload.append({
            "id": preset_id,
            **PRESET_INFO[preset_id],
            "thresholds": PRESET_BUNDLES[preset_id],
        })
    return payload


def resolve(
    section: str,
    overrides: Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | None = None,
    base: int | None = None,
    preset: Mapping[str, Any] | None = None,
    server: Mapping[str, Any] | None = None,
) -> int:
    """The effective threshold for `section` — see *The resolution spec* at the
    top of this module for the order and *why* each layer sits where it does.

    Exact override, then the override inherited from the parent; then the same
    two steps against the chosen preset bundle; then the server's active
    snapshot (`server`, the values of `threshold_snapshots`' active snapshot —
    absent/None for a client that has no server profile or a server that
    predates the feature, which resolves exactly as it always did); then the
    exact default, then the default inherited from the parent; then
    derivation (`derive`, the family tables); then `base` (`BASE_THRESHOLD`
    when the caller does not name one).

    A parent's *override* outranks a child's *default* on purpose: the user
    spoke about that family, and nothing in the shipped table should overrule
    something they set deliberately. The preset sits between the two for the
    same reason: it is a choice the user made, but a coarser one than a
    per-category slider, so an explicit delta still wins over it. The server
    snapshot sits below the preset for its mirror reason: it is explicit
    per-category tuning, but of the *machine*, and a window's own bundle
    choice is the closer statement of what that window means to show.

    Nothing that already resolved changes: the server layer is inert when it
    names nothing, derivation runs only where the old order fell straight
    through to the base, and every category the old order answered at the
    base by the operator's decision is named in `BASE_BY_DECISION`, which
    derivation respects.

    `base` exists for a caller that is not this build — `dubis low-stock` asks
    the server it is talking to what its base is, rather than assuming a remote
    server agrees with this checkout. `preset` is the chosen bundle (look it
    up with `bundle_for` for a caller, or from the server's own
    `/v1/meta` `threshold_presets` for a client).
    """
    for table in (overrides, preset, server, defaults):
        if not table:
            continue
        if section in table:
            return int(table[section])
        parent = parent_of(section)
        if parent and parent in table:
            return int(table[parent])
    derived = derive(section)
    if derived is not None:
        return derived
    return BASE_THRESHOLD if base is None else int(base)
