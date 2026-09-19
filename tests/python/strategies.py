"""Shared Hypothesis strategies for dubIS's core data shapes.

This is the Python half of the repo's property-testing harness; the JS half is
``tests/js/property/arbitraries.mjs``.  Read ``docs/property-testing.md`` before
adding to either.

The load-bearing idea
---------------------
``inventory_records()`` is **derived from** ``domain/schema.py``'s
``INVENTORY_FIELDS`` — the same list ``scripts/gen-inventory-types.py`` reads to
emit ``js/inventory-record.d.ts``.  Nothing here enumerates field names.  Add a
field to the schema and every property test starts generating it on the next
run; there is no second list to forget.

The one thing a ``FieldDef`` does not say outright is whether a ``number`` is an
``int`` or a ``float`` — ``ts_type`` collapses both.  Rather than hardcode
"``qty`` is an int", this module reads the *type of the declared default*
(``qty``'s default is ``0``, ``unit_price``'s is ``0.0``), which is information
the schema already carries and which ``cache_db.query_inventory`` already
honours.  The pair ``(ts_type, type(default))`` is looked up in
``_STRATEGY_BY_SHAPE``; a combination this module has never seen raises
``UnknownFieldShape`` rather than falling back to something plausible.  That is
the whole guarantee: **a schema change cannot silently produce invalid generated
data — it either flows through or it fails loudly.**

Realism vs. validity
--------------------
The generators aim at *schema-valid*, not *plausible*.  Text fields can be any
short text, including whitespace-only, because that is exactly the input a
property test is for: the surprising-but-legal value a human would never pick.
Where a caller needs realism it passes overrides (``inventory_records(section=...)``).
"""

from __future__ import annotations

from typing import Any, Mapping

from hypothesis import strategies as st

from domain import federation
from domain.schema import INVENTORY_FIELDS, FieldDef

__all__ = [
    "IDENTITY_FIELDS",
    "UnknownFieldShape",
    "field_strategy",
    "identity_fields",
    "inventory_records",
    "keyed_inventory_records",
    "part_key_cases",
    "source_infos",
    "rosters",
    "federated_inputs",
    "consistent_inventory_records",
]


class UnknownFieldShape(Exception):
    """A schema field whose (ts_type, default type) pair has no strategy.

    Raised at strategy-construction time, i.e. at test collection, so the
    failure names the field instead of surfacing later as a type error deep
    inside whatever was being tested.
    """


# ── Alphabets ─────────────────────────────────────────────────────────────────
#
# Printable, no control characters, but deliberately including whitespace and a
# few characters that CSV and JSON round-trips have historically disliked.  Kept
# short so shrunk counterexamples stay readable.
_TEXT = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    min_size=0,
    max_size=12,
)

#: Money.  Bounded and rounded so float arithmetic in properties stays legible;
#: negative prices are not generated because no dubIS write path can produce one.
_MONEY = st.floats(
    min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False,
).map(lambda v: round(v, 4))

#: Stock.  Non-negative because every write path floors at zero
#: (``cache_db.apply_stock_delta``, ``inventory_ops.compute_adjusted_qty``).
_QTY = st.integers(min_value=0, max_value=1_000_000)

_STRING_LIST = st.lists(_TEXT.filter(lambda s: s.strip()), max_size=3)


# ── Schema-derived per-field strategies ───────────────────────────────────────
#
# Keyed by (ts_type, type(default)).  Adding a key here is the deliberate act of
# saying "this shape is generatable"; the absence of a key is the guard.
_STRATEGY_BY_SHAPE: dict[tuple[str, type], st.SearchStrategy] = {
    ("string", str): _TEXT,
    ("number", int): _QTY,
    ("number", float): _MONEY,
    ("string[]", list): _STRING_LIST,
}


def field_strategy(field_def: FieldDef) -> st.SearchStrategy:
    """The strategy for one ``domain/schema.py`` field.

    Raises ``UnknownFieldShape`` for a field whose type pair this module has not
    been taught — the "adding a field cannot silently break the generators"
    guarantee described in the module docstring.
    """
    shape = (field_def.ts_type, type(field_def.default))
    try:
        return _STRATEGY_BY_SHAPE[shape]
    except KeyError:
        raise UnknownFieldShape(
            f"domain/schema.py field {field_def.py_key!r} declares "
            f"ts_type={field_def.ts_type!r} with a {type(field_def.default).__name__} "
            f"default, a shape tests/python/strategies.py has no generator for. "
            f"Add one to _STRATEGY_BY_SHAPE (and its mirror in "
            f"scripts/gen-property-schema.py) rather than letting the property "
            f"tests generate a record the schema would reject."
        ) from None


# ── Part identity ─────────────────────────────────────────────────────────────
#
# The fields ``invPartKey`` (js/part-keys.js:67) consults, in precedence order.
# Read off ``domain/federation`` rather than retyped, so a change to the chain
# breaks this import loudly instead of leaving the generators testing the old
# rule.  ``_FALLBACK_FIELDS`` is private to that module on purpose; reaching for
# it here is the deliberate alternative to a second copy of the list.
IDENTITY_FIELDS: tuple[str, ...] = ("lcsc",) + tuple(federation._FALLBACK_FIELDS)

#: A part number that is *not* an LCSC code, i.e. never starts with C/c.  Used
#: to drive the fallback branches of ``invPartKey`` on purpose.
_NON_LCSC_TOKEN = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=8,
).filter(lambda s: not s.upper().startswith("C"))

#: An LCSC-shaped value: "C" or "c" then digits, as the real catalog writes them.
_LCSC_TOKEN = st.builds(
    lambda prefix, digits: prefix + digits,
    st.sampled_from(["C", "c"]),
    st.text(alphabet="0123456789", min_size=1, max_size=6),
)

#: Absent.  Exactly ``""`` -- the ONLY value ``invPartKey`` treats as "keep
#: looking", because the JS chain is `a || b || c` and JS falsiness is not
#: "blank after trimming".  An earlier draft of this module used a whitespace
#: string here and the properties caught it within seconds: `" "` is truthy, so
#: a field meant to be skipped won the key instead.  See docs/property-testing.md.
_BLANK = st.just("")

#: Whitespace-only.  Truthy to ``invPartKey`` (so it CAN win the key) and empty
#: to ``domain.federation._is_empty`` (which strips before testing).  Generated
#: on purpose: that mismatch between the two notions of "empty" is a real seam,
#: and a generator that never produces one can never find what lives there.
_WHITESPACE = st.sampled_from([" ", "   ", "\t"])


@st.composite
def identity_fields(draw, branch: str | None = None) -> dict[str, Any]:
    """The five identity fields, aimed at one branch of the ``invPartKey`` rule.

    ``branch`` is one of:

    ``"lcsc"``       a C-prefixed lcsc wins outright;
    ``"mpn"`` / ``"digikey"`` / ``"pololu"`` / ``"mouser"``
                     every earlier field is empty (or a non-C lcsc) so the named
                     one wins;
    ``"unkeyable"``  all five empty — the branch where JS returns ``""`` and
                     ``domain.federation.part_key`` raises;
    ``None``         pick a branch at random, which is what most callers want.

    Every returned dict also carries a ``"__branch"`` key naming the branch it
    was built for, so a property can assert the rule *and* report which branch
    the counterexample came from.  Callers that feed these into a real record
    strip it (``inventory_records`` does).
    """
    branches = list(IDENTITY_FIELDS) + ["unkeyable"]
    if branch is None:
        branch = draw(st.sampled_from(branches))
    if branch not in branches:
        raise ValueError(f"unknown invPartKey branch {branch!r}; expected one of {branches}")

    out = {name: "" for name in IDENTITY_FIELDS}
    out["__branch"] = branch

    anything = st.one_of(_BLANK, _WHITESPACE, _NON_LCSC_TOKEN, _LCSC_TOKEN)
    winnable = st.one_of(_WHITESPACE, _NON_LCSC_TOKEN, _LCSC_TOKEN)

    if branch == "unkeyable":
        # Every field exactly "": whitespace would key the part, not skip it.
        for name in IDENTITY_FIELDS:
            out[name] = draw(_BLANK)
        return out

    if branch == "lcsc":
        out["lcsc"] = draw(_LCSC_TOKEN)
        # Later fields are free: a C-prefixed lcsc short-circuits the chain, and
        # filling them is how we prove it.
        for name in IDENTITY_FIELDS[1:]:
            out[name] = draw(anything)
        return out

    winner = IDENTITY_FIELDS.index(branch)
    # lcsc must not win: blank, whitespace, or present but not C-prefixed.
    out["lcsc"] = draw(st.one_of(_BLANK, _WHITESPACE, _NON_LCSC_TOKEN))
    for i, name in enumerate(IDENTITY_FIELDS[1:], start=1):
        if i < winner:
            out[name] = draw(_BLANK)
        elif i == winner:
            out[name] = draw(winnable)
        else:
            out[name] = draw(anything)
    return out


def part_key_cases() -> st.SearchStrategy[dict[str, Any]]:
    """Identity-field dicts covering every branch of the ``invPartKey`` rule.

    Uniform over branches rather than over values, so a run of N examples spends
    roughly N/6 of its budget on each branch instead of drowning the rare ones.
    """
    return st.one_of(*[identity_fields(branch=b)
                       for b in list(IDENTITY_FIELDS) + ["unkeyable"]])


# ── Inventory records ─────────────────────────────────────────────────────────

def _non_identity_fields() -> list[FieldDef]:
    return [f for f in INVENTORY_FIELDS if f.py_key not in IDENTITY_FIELDS]


@st.composite
def inventory_records(draw, *, keyable: bool = True, **overrides: Any) -> dict[str, Any]:
    """One schema-valid inventory record: every ``INVENTORY_FIELDS`` key, nothing else.

    ``keyable=True`` (the default) guarantees the record has a part number, i.e.
    that ``domain.federation.part_key`` will not raise.  Pass ``keyable=False``
    to include the unkeyable branch on purpose.

    ``overrides`` replaces a field's generator: ``inventory_records(qty=st.just(0))``
    or ``inventory_records(section=st.sampled_from(SECTIONS))``.  An override for
    a key the schema does not define is an error, not a silently-added key —
    otherwise the generator would drift from the record shape it claims to
    produce.
    """
    known = {f.py_key for f in INVENTORY_FIELDS}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(
            f"inventory_records() override(s) {sorted(unknown)} are not in "
            f"domain/schema.py's INVENTORY_FIELDS — add the field to the schema "
            f"first (see the 'Inventory record field change' trap in CLAUDE.md)"
        )

    ident = draw(identity_fields() if not keyable
                 else st.one_of(*[identity_fields(branch=b) for b in IDENTITY_FIELDS]))
    record: dict[str, Any] = {k: v for k, v in ident.items() if k != "__branch"}
    for field_def in _non_identity_fields():
        record[field_def.py_key] = draw(field_strategy(field_def))
    for key, strategy in overrides.items():
        record[key] = draw(strategy)
    return record


def keyed_inventory_records(**overrides: Any) -> st.SearchStrategy[dict[str, Any]]:
    """``inventory_records`` restricted to records that ``part_key`` accepts."""
    return inventory_records(keyable=True, **overrides)


@st.composite
def consistent_inventory_records(draw, **overrides: Any) -> dict[str, Any]:
    """A record whose money reconciles: ``ext_price == unit_price * qty``.

    Real rows satisfy this (``csv_io`` carries both columns from the
    distributor's invoice), and several merge properties are only *statable*
    against inputs that do — the merge cannot make ``unit_price x qty`` agree
    with a summed ``ext_price`` if its inputs never did.
    """
    record = draw(inventory_records(**overrides))
    if "ext_price" not in overrides:
        record["ext_price"] = round(record["unit_price"] * record["qty"], 6)
    return record


# ── Sources and rosters ───────────────────────────────────────────────────────

_SOURCE_ID = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=8)


def source_infos() -> st.SearchStrategy[federation.SourceInfo]:
    """One ``SourceInfo``.  Display name is free text; the id is roster-shaped."""
    return st.builds(federation.SourceInfo, id=_SOURCE_ID, name=_TEXT)


def rosters(min_size: int = 1, max_size: int = 4) -> st.SearchStrategy[list[federation.SourceInfo]]:
    """A roster of sources with **distinct ids**.

    Duplicate ids are a ``MergeError`` by design, so the happy-path generator
    excludes them; the test that exercises the duplicate lives on its own.
    """
    return st.lists(source_infos(), min_size=min_size, max_size=max_size,
                    unique_by=lambda s: s.id)


@st.composite
def federated_inputs(
    draw,
    *,
    min_sources: int = 1,
    max_sources: int = 3,
    max_parts: int = 5,
    consistent_money: bool = False,
) -> list[tuple[federation.SourceInfo, list[dict[str, Any]]]]:
    """``[(SourceInfo, [record, ...]), ...]`` — the exact shape ``merge_inventories`` eats.

    Built from a shared *pool* of identity-field sets and then dealt out, so the
    sources' key sets genuinely overlap: a pool drawn independently per source
    would almost never collide on a generated part number, and a merge test
    where nothing merges proves nothing.  Each source keeps or drops each pooled
    part independently, which covers the full lattice from fully disjoint
    (every source got a different subset) to fully overlapping.

    The non-identity fields are drawn *per source per part*, so two sources
    genuinely disagree about description, package, price and so on — that is
    what makes the conflict and weighted-mean rules reachable.
    """
    sources = draw(rosters(min_size=min_sources, max_size=max_sources))
    pool = draw(st.lists(
        st.one_of(*[identity_fields(branch=b) for b in IDENTITY_FIELDS]),
        min_size=1, max_size=max_parts,
    ))
    record_strategy = consistent_inventory_records if consistent_money else inventory_records

    out: list[tuple[federation.SourceInfo, list[dict[str, Any]]]] = []
    for info in sources:
        records: list[dict[str, Any]] = []
        for ident in pool:
            if not draw(st.booleans()):
                continue
            base = draw(record_strategy())
            base.update({k: v for k, v in ident.items() if k != "__branch"})
            if consistent_money:
                base["ext_price"] = round(base["unit_price"] * base["qty"], 6)
            records.append(base)
        out.append((info, records))
    return out


# ── Convenience ───────────────────────────────────────────────────────────────

def schema_defaults() -> dict[str, Any]:
    """The all-defaults record, as ``domain/schema.py`` declares it."""
    return {f.py_key: (list(f.default) if isinstance(f.default, list) else f.default)
            for f in INVENTORY_FIELDS}


def is_schema_valid(record: Mapping[str, Any]) -> bool:
    """Whether *record* has exactly the schema's keys with the schema's types."""
    defaults = schema_defaults()
    if set(record) != set(defaults):
        return False
    for field_def in INVENTORY_FIELDS:
        value = record[field_def.py_key]
        expected = type(field_def.default)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            continue  # an int is an acceptable float
        if not isinstance(value, expected) or isinstance(value, bool):
            return False
    return True
