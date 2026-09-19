"""Merge inventory records from several dubIS servers into one view.

Pure logic: no HTTP, no sqlite, no imports from ``server/``.  ``server/fanout.py``
does the fetching and hands the results here as ``[(SourceInfo, records), ...]``
in roster order; everything this module decides is a function of that argument.
That is what makes the merge testable without a second server running.

Two decisions are worth stating up front, because both are places where a
"reasonable" alternative would quietly corrupt the merged view:

**Identity is the frontend's rule, ported verbatim.**  ``part_key`` is a port of
``invPartKey`` (``js/part-keys.js:67``), not of ``domain/part_registry.derive_key``.
The two agree on precedence (LCSC-when-C-prefixed > MPN > DigiKey > Pololu >
Mouser) but differ in one detail: ``derive_key`` strips whitespace before
testing, ``invPartKey`` does not, so a ``lcsc`` of ``" C1000"`` falls through to
the MPN in JS and does not in Python.  The merged rows are keyed by exactly what
the browser will key ``rowMap`` and ``tr.dataset.partKey`` on
(``js/inventory/inv-state.js:16``, ``js/inventory/inv-html-builders.js:182``), so
the JS rule is the one that has to win here.  ``test_federation.py`` pins the
port against a case table *and* against the text of the JS function, so a future
edit to either side fails a test instead of silently splitting one part into two
rows.

**The per-field rule is derived from ``domain/schema.py``, not hardcoded.**
``INVENTORY_FIELDS`` already carries ``ts_type`` (``string`` / ``number`` /
``string[]``), which is enough to decide union-vs-scalar, and the three fields
whose arithmetic is special (``qty``, ``ext_price``, ``unit_price``) are named in
``_RULE_OVERRIDES`` with a reason each.  A field added to the schema later is
therefore merged sensibly without touching this file; a field added with a
``ts_type`` this module has never seen raises rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from domain.schema import INVENTORY_FIELDS
from dubis_errors import DubISError

# ── Identity ──────────────────────────────────────────────────────────────────

# js/part-keys.js:68 — `/^C/i`.  Case-insensitive, anchored, and deliberately not
# `C\d+`: the JS accepts any C-prefixed LCSC value and so must this.
_LCSC_PREFIX = re.compile(r"^C", re.IGNORECASE)

# js/part-keys.js:70 — the fallback chain, in order.
_FALLBACK_FIELDS: tuple[str, ...] = ("mpn", "digikey", "pololu", "mouser")


class UnkeyablePartError(DubISError):
    """An inventory record carries no usable part number.

    Deliberately louder than ``domain/part_registry.derive_key``, which returns
    ``""`` and lets ``cache_db`` skip the row.  That is right for *ledger* rows:
    a hand-edited CSV can contain junk, and dropping it is the documented
    behavior.  It is wrong here.  These records come from a peer's
    ``GET /v1/parts``, i.e. from that server's ``query_inventory``, and every row
    it emits already has a ``part_id`` — so an unkeyable record means the peer is
    broken or the payload is not an inventory record at all.  Dropping it would
    silently subtract stock from the merged totals, which is the failure mode
    that is hardest to notice and most expensive to believe.

    Carries the offending record so the caller can name the source in its log.
    """

    def __init__(self, message: str, *, record: Mapping[str, Any] | None = None,
                 source_id: str = ""):
        super().__init__(message)
        self.record = dict(record or {})
        self.source_id = source_id


def part_key(record: Mapping[str, Any]) -> str:
    """The part's identity, exactly as ``invPartKey`` computes it in the browser.

    ``js/part-keys.js:67``::

        var lcsc = item.lcsc || "";
        if (lcsc && /^C/i.test(lcsc)) return lcsc;
        return item.mpn || item.digikey || item.pololu || item.mouser || "";

    Ported literally, including the details that look like accidents and are not:

    * the LCSC value is returned **as written** — ``"c1000"`` keys as ``"c1000"``,
      not ``"C1000"``; only the prefix *test* is case-insensitive;
    * a non-C ``lcsc`` (e.g. a stray ``"n/a"``) does not win and does not block —
      it falls through to the MPN;
    * nothing is stripped, because the JS does not strip (see the module
      docstring);
    * falsy values fall through, so ``""`` and ``None`` behave identically.

    Where the JS returns ``""``, this raises ``UnkeyablePartError``: the browser
    can afford a blank key for a row it is only rendering, a merge cannot.
    """
    lcsc = record.get("lcsc") or ""
    if not isinstance(lcsc, str):
        lcsc = str(lcsc)
    if lcsc and _LCSC_PREFIX.match(lcsc):
        return lcsc
    for field_name in _FALLBACK_FIELDS:
        value = record.get(field_name)
        if value:
            return value if isinstance(value, str) else str(value)
    raise UnkeyablePartError(
        "inventory record has no usable part number "
        f"(lcsc/mpn/digikey/pololu/mouser all empty): {dict(record)!r}",
        record=record,
    )


# ── Sources ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceInfo:
    """One data source in the merge: the roster id and its display name.

    Frozen because it is used as the identity of a column in the output and must
    not drift while a merge is running.
    """

    id: str
    name: str


class MergeError(DubISError):
    """The inputs to ``merge_inventories`` cannot be merged as given."""


# ── Per-field merge rules ─────────────────────────────────────────────────────

class MergeRule(str, Enum):
    """How one field's values from several sources become one value."""

    SUM = "sum"
    """Add them.  For extensive quantities only — stock and money."""

    WEIGHTED_MEAN_BY_QTY = "weighted_mean_by_qty"
    """Average, weighted by each source's ``qty``.  For per-unit money."""

    FIRST_NON_EMPTY = "first_non_empty"
    """First non-empty value in source order wins; disagreement is a conflict."""

    UNION = "union"
    """Concatenate the lists in source order, dropping repeats."""


# Fields whose semantics differ from what their type alone would imply.
#
#   qty        — stock is extensive: 850 on the bench plus 400 in the shop is
#                1250 parts, and the per-source breakdown keeps the split.
#   ext_price  — extended price is qty x unit price, so it is extensive too.
#   unit_price — per-unit price is intensive: summing it would invent a price
#                nobody quoted.  Weighted by qty so the merged row's
#                unit_price x qty still reconciles with the summed ext_price.
_RULE_OVERRIDES: dict[str, MergeRule] = {
    "qty": MergeRule.SUM,
    "ext_price": MergeRule.SUM,
    "unit_price": MergeRule.WEIGHTED_MEAN_BY_QTY,
}

# Default rule per schema ts_type.  A plain `number` is NOT summed: summing is a
# claim about the field's meaning (extensive) that only the overrides above make.
# A future numeric field — a threshold, a reorder point — would be nonsense as a
# total, so the safe default is "first source wins, disagreement is flagged".
_RULE_BY_TS_TYPE: dict[str, MergeRule] = {
    "string": MergeRule.FIRST_NON_EMPTY,
    "number": MergeRule.FIRST_NON_EMPTY,
    "string[]": MergeRule.UNION,
}

_FIELD_DEFS = {f.py_key: f for f in INVENTORY_FIELDS}

# Keys the merge owns outright.  If an input record carries one (i.e. it is
# itself already a merged row, from a peer that is also a hub), it is recomputed
# from this level's sources rather than merged — a nested breakdown has no
# meaning once the rows are pooled again.
RESERVED_KEYS: frozenset[str] = frozenset({"sources", "conflicts"})

QTY_FIELD = "qty"


def rule_for(field_name: str, sample_value: Any = None) -> MergeRule:
    """The merge rule for ``field_name``, driven off ``domain/schema.py``.

    Unknown fields (anything not in ``INVENTORY_FIELDS`` — a peer running a newer
    build, say) fall back to a decision on the value's shape: lists union, and
    everything else takes first-non-empty.  Never SUM, because a total is a claim
    about a field whose meaning this build does not know.
    """
    override = _RULE_OVERRIDES.get(field_name)
    if override is not None:
        return override
    field_def = _FIELD_DEFS.get(field_name)
    if field_def is None:
        return MergeRule.UNION if isinstance(sample_value, (list, tuple)) else MergeRule.FIRST_NON_EMPTY
    try:
        return _RULE_BY_TS_TYPE[field_def.ts_type]
    except KeyError:
        raise MergeError(
            f"domain/schema.py field {field_name!r} has ts_type {field_def.ts_type!r}, "
            "which domain/federation.py has no merge rule for — add one to "
            "_RULE_BY_TS_TYPE (or _RULE_OVERRIDES) rather than letting the merged "
            "view guess"
        ) from None


# ── Emptiness ─────────────────────────────────────────────────────────────────

def _is_empty(value: Any) -> bool:
    """Whether a value counts as "this source has nothing to say".

    Zero counts as empty for numbers, matching ``domain/schema.py``, where the
    defaults for absent data are exactly ``""``, ``0``, ``0.0`` and ``[]`` — a
    ``unit_price`` of 0.0 means "no price recorded", not "free".  Strings are
    compared after stripping, so trailing whitespace is not a second opinion.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _normalized(value: Any) -> Any:
    """The form used to decide whether two sources actually disagree."""
    return value.strip() if isinstance(value, str) else value


def _as_number(value: Any, *, field_name: str, source_id: str) -> float | int:
    """Coerce a record value to a number, loudly."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise MergeError(f"source {source_id!r} sent a bool for numeric field {field_name!r}")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            text = value.strip()
            return int(text) if text.lstrip("+-").isdigit() else float(text)
        except ValueError:
            pass
    raise MergeError(
        f"source {source_id!r} sent a non-numeric value {value!r} for numeric field {field_name!r}"
    )


# ── The merge ─────────────────────────────────────────────────────────────────

def merge_inventories(
    by_source: Sequence[tuple[SourceInfo, Sequence[Mapping[str, Any]]]],
) -> list[dict[str, Any]]:
    """Pool several sources' inventories into one row per part key.

    Ordering is fully determined by the input: sources are visited in the order
    given (the roster order ``server/fanout.py`` preserves), each source's
    records in the order it returned them (``query_inventory``'s sort), and a
    part takes the output position of its *first* appearance.  So adding a
    second source never reshuffles the rows the first one contributed.

    Every output row carries two keys the inputs do not:

    ``sources``
        ``[{"id", "name", "qty"}]``, one entry per contributing source, in
        source order — present even when only one source has the part, so the
        UI never branches on "merged or not".
    ``conflicts``
        The names of fields where two sources held different non-empty values,
        in field order.  Always present, empty list when there is nothing to
        flag.  Only first-non-empty fields can conflict: summed and unioned
        fields have a defined answer for disagreement, so "disagreeing" is not a
        meaningful thing for them to do.

    Raises ``MergeError`` for a duplicated source id, and ``UnkeyablePartError``
    for a record with no part number (see that class for why it is not skipped).
    A source that returned nothing simply contributes nothing; an empty
    ``by_source`` yields ``[]``.
    """
    seen_ids: set[str] = set()
    # part key -> [(SourceInfo, record), ...] in first-seen order
    contributions: dict[str, list[tuple[SourceInfo, Mapping[str, Any]]]] = {}

    for info, records in by_source:
        if info.id in seen_ids:
            raise MergeError(
                f"source id {info.id!r} appears twice in the merge input — "
                "the per-source breakdown would be ambiguous"
            )
        seen_ids.add(info.id)
        for record in records:
            try:
                key = part_key(record)
            except UnkeyablePartError as exc:
                raise UnkeyablePartError(
                    f"source {info.id!r} returned a record with no usable part number: "
                    f"{dict(record)!r}",
                    record=record,
                    source_id=info.id,
                ) from exc
            contributions.setdefault(key, []).append((info, record))

    return [_merge_one(key, rows) for key, rows in contributions.items()]


def _merge_one(key: str, rows: list[tuple[SourceInfo, Mapping[str, Any]]]) -> dict[str, Any]:
    """Collapse one part key's per-source records into a single row."""
    merged: dict[str, Any] = {}
    conflicts: list[str] = []

    for field_name in _field_order(rows):
        values = [(info, record[field_name]) for info, record in rows if field_name in record]
        if not values:
            continue
        rule = rule_for(field_name, values[0][1])
        if rule is MergeRule.SUM:
            merged[field_name] = _sum_field(field_name, values)
        elif rule is MergeRule.WEIGHTED_MEAN_BY_QTY:
            merged[field_name] = _weighted_mean(field_name, values, rows)
        elif rule is MergeRule.UNION:
            merged[field_name] = _union(field_name, values)
        else:
            value, conflicted = _first_non_empty(values)
            merged[field_name] = value
            if conflicted:
                conflicts.append(field_name)

    _preserve_identity(key, merged, rows)
    merged["sources"] = _source_breakdown(rows)
    merged["conflicts"] = conflicts
    return merged


def _preserve_identity(
    key: str,
    merged: dict[str, Any],
    rows: list[tuple[SourceInfo, Mapping[str, Any]]],
) -> None:
    """Make sure the merged row still keys to the part it was merged *for*.

    Gap-filling is the point of ``FIRST_NON_EMPTY``: a merged row carries the
    DigiKey number source A knows and the Mouser number source B knows.  For the
    five identity fields that same gap-filling can move *which* field wins
    ``part_key``, because the merge and the key disagree about what "empty"
    means: ``_is_empty`` strips before testing, ``part_key`` (like ``invPartKey``)
    does not.  So a whitespace-only part number is a *value* for grouping and a
    *gap* for merging, and a row grouped under it can come out keyed on a
    different field entirely.

    That is not cosmetic.  The browser recomputes the key from the row it is
    handed (``js/inventory/inv-state.js`` builds ``rowMap`` with ``invPartKey``),
    so a row that keys differently from its own group collides with whichever
    row genuinely owns that key — one of the two silently disappears from the
    table while its stock is still in the totals.

    The repair: fall back to the *first contributor's* identity block wholesale.
    Every contributor keys to ``key`` by construction, so copying one is always
    correct, and it costs only the cross-source gap-filling of part numbers, and
    only on the rare rows where the two emptiness rules disagree.  Discovered by
    ``tests/python/test_federation_properties.py``; shrunk counterexample: source
    A ``{digikey: "\\t"}`` and source B ``{mpn: "\\t", digikey: "0"}`` both key to
    ``"\\t"`` and merged to a row keyed ``"0"``.
    """
    identity = ("lcsc", *_FALLBACK_FIELDS)
    if not any(name in merged for name in identity):
        return
    try:
        if part_key(merged) == key:
            return
    except UnkeyablePartError:
        pass  # the merge emptied every identity field; repair below

    first = rows[0][1]
    for name in identity:
        if name in merged:
            # A field the first contributor does not carry is falsy to
            # ``invPartKey``, so "" is the faithful stand-in here.
            merged[name] = first.get(name, "")

    if part_key(merged) != key:  # pragma: no cover - defensive
        raise MergeError(
            f"merged row for part key {key!r} keys as {part_key(merged)!r} even after "
            "falling back to the first contributor's identity fields; this should be "
            "impossible and means part_key is no longer a function of those fields alone"
        )


def _field_order(rows: Iterable[tuple[SourceInfo, Mapping[str, Any]]]) -> list[str]:
    """Every field any contributing record carries, in a deterministic order.

    Schema order first (so a merged row reads like an inventory record), then any
    extra keys in first-seen order.  Reserved keys are dropped: they are
    recomputed, never merged.
    """
    present: dict[str, None] = {}
    for _, record in rows:
        for field_name in record:
            if field_name not in RESERVED_KEYS:
                present.setdefault(field_name, None)
    ordered = [f.py_key for f in INVENTORY_FIELDS if f.py_key in present]
    ordered += [name for name in present if name not in _FIELD_DEFS]
    return ordered


def _sum_field(field_name: str, values: list[tuple[SourceInfo, Any]]) -> float | int:
    """Add an extensive field.  Stays an ``int`` when every part of it is one."""
    numbers = [_as_number(v, field_name=field_name, source_id=info.id) for info, v in values]
    total = sum(numbers)
    if all(isinstance(n, int) for n in numbers):
        return int(total)
    return total


def _weighted_mean(
    field_name: str,
    values: list[tuple[SourceInfo, Any]],
    rows: list[tuple[SourceInfo, Mapping[str, Any]]],
) -> float | int:
    """Quantity-weighted mean, with a documented answer when the weights vanish.

    One contribution short-circuits to that source's own value, so a single-source
    row is byte-identical passthrough rather than ``p * q / q`` and its float
    dust.

    Divide-by-zero is a real case, not a bug: a part every source has run out of
    has total qty 0 and still has a last known price.  Raising there would make
    the merged view fail on exactly the parts a user is most likely to be looking
    at.  The documented rule is to fall back to the plain mean of the non-empty
    prices, which is what "weighted by nothing" should mean; if no source has a
    price either, the schema default 0.0 stands.
    """
    if len(values) == 1:
        return values[0][1]

    # Weight per *record*, not per source.  An earlier version built a
    # {source id: qty} dict from `rows`, which silently collapsed a source that
    # lists the same part twice down to whichever of its records came last:
    # every one of that source's prices was then weighted by that single qty,
    # and the merged unit_price stopped reconciling with the summed ext_price.
    # `_source_breakdown` already documents that a source CAN repeat a part
    # (query_inventory keys on part_id; this module keys on invPartKey, and the
    # two are not the same function), so this is the one place that disagreed
    # with the rest of the merge.  Found by
    # tests/python/test_federation_properties.py's reconciliation property —
    # shrunk counterexample: one source, two rows keyed C0, (qty 0, price 1.0)
    # and (qty 1, price 0.0), which merged to unit_price 0.5 against ext 0.0.
    weighted = 0.0
    total_qty = 0.0
    for info, record in rows:
        if field_name not in record:
            continue
        price = _as_number(record[field_name], field_name=field_name, source_id=info.id)
        qty = _as_number(record.get(QTY_FIELD, 0), field_name=QTY_FIELD, source_id=info.id)
        weighted += price * qty
        total_qty += qty

    if total_qty:
        return weighted / total_qty

    priced = [
        _as_number(v, field_name=field_name, source_id=info.id)
        for info, v in values
        if not _is_empty(v)
    ]
    if not priced:
        return 0.0
    return sum(priced) / len(priced)


def _union(field_name: str, values: list[tuple[SourceInfo, Any]]) -> list[Any]:
    """Concatenate list fields in source order, first occurrence wins."""
    out: list[Any] = []
    for info, value in values:
        if value is None or value == "":
            continue
        if not isinstance(value, (list, tuple)):
            raise MergeError(
                f"source {info.id!r} sent {value!r} for list field {field_name!r}; "
                "a list-valued field cannot be unioned with a scalar"
            )
        for item in value:
            if item not in out:
                out.append(item)
    return out


def _first_non_empty(values: list[tuple[SourceInfo, Any]]) -> tuple[Any, bool]:
    """``(winning value, did sources disagree)`` for a scalar field.

    The winner is the first non-empty value in source order, returned unmodified
    (the comparison strips, the value kept does not).  Disagreement means two
    sources held *different* non-empty values — a source that simply has nothing
    to say is not disagreeing with one that does, which is the whole point of
    first-non-empty: filling gaps is a merge, overwriting is a conflict.
    """
    winner: Any = None
    have_winner = False
    distinct: list[Any] = []
    for _, value in values:
        if _is_empty(value):
            continue
        if not have_winner:
            winner, have_winner = value, True
        norm = _normalized(value)
        if norm not in distinct:
            distinct.append(norm)
    if not have_winner:
        # Nothing to pick from: keep the first value seen so the field still
        # exists on the row with its own (empty) shape, e.g. "" vs 0.
        return values[0][1], False
    return winner, len(distinct) > 1


def _source_breakdown(rows: list[tuple[SourceInfo, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """The per-source ``{"id", "name", "qty"}`` entries, in source order.

    One entry per *source*, not per record: if a source somehow lists the same
    part twice (its own bug — ``query_inventory`` keys on ``part_id``), the
    quantities fold into that source's single entry, so the breakdown keeps
    summing to the row's ``qty`` instead of showing a source twice.
    """
    breakdown: dict[str, dict[str, Any]] = {}
    for info, record in rows:
        qty = _as_number(record.get(QTY_FIELD, 0), field_name=QTY_FIELD, source_id=info.id)
        entry = breakdown.get(info.id)
        if entry is None:
            breakdown[info.id] = {"id": info.id, "name": info.name, "qty": qty}
        else:
            entry["qty"] += qty
    return list(breakdown.values())
