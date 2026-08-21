"""Predicate evaluation for alternate-part approval.

`spec_extractor.spec_matches` can only answer five fixed questions about
passives (value, package, voltage_min, tolerance, dielectric) from a part's
*description text*. Most real substitution decisions are numeric comparisons on
named parametrics that description text never carries — and those parametrics
are now stored per part × attribute × distributor by `domain/attributes.py`.

This module turns a requirement into predicates and evaluates them against that
store, producing spec-delta records in the shape `domain/generic_parts.py`
records on a member review. So "is this substitute acceptable" becomes a
mechanical check whose evidence is written down, instead of a judgement someone
has to redo.

Worked examples, all from a real sourcing review:

    # a level translator must reach down to 0.9 V on the A side, or it breaks
    # the low-voltage IO banks -- most cheap 1T45 parts floor at 1.65 V
    Predicate("supply_voltage", "lte", bound="lower", value=0.9, unit="V")

    # an ESD diode on high-speed IO: capacitance is the whole reason for the
    # part, and plentiful general-purpose parts are 10-40 pF
    Predicate("capacitance", "lte", value=0.5, unit="pF")

    # a buck's feedback divider is already on the board, so Vref must match
    Predicate("feedback_reference_voltage", "eq", value=0.6, unit="V")

    # a temperature sensor may not lose resolution
    Predicate("resolution_bits", "gte", value=13)

Three rules the whole module is built around:

1. **Unknown is never pass.** An absent parametric means "we do not know",
   and a blocking predicate over missing data yields ``indeterminate`` for the
   whole report, never approval. Silently blessing a substitute we could not
   check is the one outcome worse than refusing to judge it.
2. **A unit mismatch is unknown, not a verdict.** Comparing volts to farads
   means the stored attribute is not the one the predicate meant; answering
   either way would be worse than admitting confusion.
3. **Bounds are explicit.** A stored value is frequently a range
   ("0.9 V ~ 5.5 V"), and requirements address one end of it. Which end is
   part of the predicate, never inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from domain.attribute_parse import canonical_name, parse_value
from domain.packages import packages_equivalent

# Verdict statuses.
PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

# Report statuses. `indeterminate` is deliberately distinct from `fail`: one
# says the candidate is wrong, the other says we could not tell, and only a
# human should convert the second into a decision.
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_INDETERMINATE = "indeterminate"

OPS = ("lte", "gte", "eq", "enum", "package_equivalent")
BOUNDS = ("value", "lower", "upper")

# Which distributor to believe when several publish the same parametric.
# DigiKey first because its parametric vocabulary is consistently named with
# parseable units, where LCSC's is a sparse long tail with free-text entries --
# measured at ~12 recurring attribute names across 21 parts. Order is a default,
# not a law: pass `prefer=` to override per call.
DISTRIBUTOR_PREFERENCE = ("digikey", "mouser", "element14", "lcsc", "pololu")

# Relative tolerance for `eq` on floats, matching spec_extractor.spec_matches
# so the two agree on what "the same value" means.
EQ_RELATIVE_TOLERANCE = 0.001


@dataclass(frozen=True)
class Predicate:
    """One checkable requirement.

    `attribute` is a canonical attribute name (see
    `domain.attribute_parse.canonical_name`); it is canonicalized on
    construction so callers may pass a vendor spelling.

    `bound` selects which end of a stored range to test. `qualifier`, when set,
    requires the stored value to carry a matching measurement condition -- the
    `Rds(on) @ 10 V` case, where a number measured at a different gate voltage
    is not the number the requirement is about.
    """

    attribute: str
    op: str
    bound: str = "value"
    value: float | None = None
    unit: str = ""
    values: tuple[str, ...] = ()
    package: str = ""
    qualifier: str = ""
    blocking: bool = True
    label: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"unknown op {self.op!r} (expected one of {', '.join(OPS)})")
        if self.bound not in BOUNDS:
            raise ValueError(f"unknown bound {self.bound!r} (expected one of {', '.join(BOUNDS)})")
        if self.op in ("lte", "gte", "eq") and self.value is None:
            raise ValueError(f"op {self.op!r} requires a numeric value")
        if self.op == "enum" and not self.values:
            raise ValueError("op 'enum' requires a non-empty values tuple")
        if self.op == "package_equivalent" and not self.package:
            raise ValueError("op 'package_equivalent' requires a reference package")
        object.__setattr__(self, "attribute", canonical_name(self.attribute) if self.attribute else "")

    @property
    def display(self) -> str:
        """Human-readable requirement, used as the spec-delta `field`."""
        if self.label:
            return self.label
        if self.op == "package_equivalent":
            return "package"
        return f"{self.attribute}.{self.bound}" if self.bound != "value" else self.attribute


@dataclass
class Verdict:
    """The outcome of one predicate against one candidate."""

    predicate: Predicate
    status: str
    reference: str = ""
    candidate: str = ""
    distributor: str = ""
    note: str = ""

    @property
    def kind(self) -> str:
        """The `domain.generic_parts` spec-delta kind this verdict belongs to."""
        return "package" if self.predicate.op == "package_equivalent" else "parametric"

    def as_spec_delta(self) -> dict[str, Any]:
        """Render as a spec-delta record for a member review."""
        return {
            "field": self.predicate.display,
            "kind": self.kind,
            "reference": self.reference,
            "candidate": self.candidate,
            "blocking": bool(self.predicate.blocking),
            "note": self.note or self.predicate.note,
            "evidence": f"{self.distributor} parametric" if self.distributor else "",
        }


@dataclass
class Report:
    """Every verdict for a candidate, plus the overall status."""

    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def status(self) -> str:
        """`fail` beats `indeterminate` beats `pass`, considering blocking only.

        A non-blocking predicate never changes the status -- it is advisory, and
        its verdict is still recorded so a reviewer sees it.
        """
        blocking = [v for v in self.verdicts if v.predicate.blocking]
        if any(v.status == FAIL for v in blocking):
            return STATUS_FAIL
        if any(v.status == UNKNOWN for v in blocking):
            return STATUS_INDETERMINATE
        return STATUS_PASS

    @property
    def blockers(self) -> list[Verdict]:
        """Blocking verdicts that did not pass -- why this candidate is not approvable."""
        return [v for v in self.verdicts if v.predicate.blocking and v.status != PASS]

    def spec_deltas(self) -> list[dict[str, Any]]:
        """Spec-delta records for every verdict that is not a clean pass."""
        return [v.as_spec_delta() for v in self.verdicts if v.status != PASS]


def _absent_note(predicate: Predicate, attributes: Iterable[dict[str, Any]]) -> str:
    """Explain *why* there was nothing to test.

    "not published" and "you named the attribute wrong" are the same silence to
    a caller, and only one of them is worth investigating. Attribute names are
    canonicalized against a table that covers a few dozen spellings and passes
    everything else through normalized, so a predicate naming
    `feedback_reference_voltage` finds nothing when the store holds
    `feedback reference voltage` -- a mistake worth naming rather than
    reporting as missing data.
    """
    names = sorted({str(r.get("canonical_name", "")) for r in attributes if r.get("canonical_name")})
    if predicate.qualifier and any(n == predicate.attribute for n in names):
        return f"no reading at {predicate.qualifier}"
    if not names:
        return "no stored attributes for this part"
    near = [n for n in names if _looks_like(n, predicate.attribute)]
    if near:
        return (f"{predicate.attribute!r} not stored; did you mean "
                f"{', '.join(repr(n) for n in near[:3])}?")
    return f"{predicate.attribute!r} not published ({len(names)} other attributes stored)"


def _looks_like(stored: str, wanted: str) -> bool:
    """Same words, different separators or order -- the canonicalization trap."""
    if not stored or not wanted:
        return False
    a = {t for t in stored.replace("_", " ").split() if t}
    b = {t for t in wanted.replace("_", " ").split() if t}
    return bool(a) and bool(b) and (a == b or a <= b or b <= a)


def _to_si(value: float, unit: str) -> tuple[float, str] | None:
    """Convert a predicate's value+unit into the SI base the store uses.

    Routed through the real value parser rather than a private unit table, so a
    predicate and a stored attribute can never disagree about what "pF" means.
    """
    if not unit:
        return (float(value), "")
    parsed = parse_value(f"{value}{unit}")
    if not parsed.parsed or parsed.value_min is None:
        return None
    return (parsed.value_min, parsed.unit)


def _pick(rows: Sequence[dict[str, Any]], prefer: Sequence[str]) -> dict[str, Any] | None:
    """Choose which distributor's reading to use for one attribute.

    Prefers a row with a usable number over one without -- an unparsed reading
    from a favoured distributor is worth less than a parsed one from any other.
    """
    if not rows:
        return None
    order = {name: i for i, name in enumerate(prefer)}

    def rank(row: dict[str, Any]) -> tuple[int, int, str]:
        has_number = row.get("value_min") is not None
        return (
            0 if has_number else 1,
            order.get(str(row.get("distributor", "")).lower(), len(order)),
            str(row.get("observed_at", "")),
        )

    return sorted(rows, key=rank)[0]


def _bound_of(row: dict[str, Any], bound: str) -> float | None:
    lo, hi = row.get("value_min"), row.get("value_max")
    if bound == "lower":
        return lo
    if bound == "upper":
        return hi if hi is not None else lo
    # A scalar stores the same number in both ends; a genuine range has no
    # single "value", so refuse rather than silently pick an end.
    if lo is not None and hi is not None and lo != hi:
        return None
    return lo


def evaluate(
    predicate: Predicate,
    attributes: Iterable[dict[str, Any]],
    *,
    package: str | None = None,
    prefer: Sequence[str] = DISTRIBUTOR_PREFERENCE,
) -> Verdict:
    """Evaluate one predicate against a candidate's stored attributes."""
    if predicate.op == "package_equivalent":
        if not package:
            return Verdict(predicate, UNKNOWN, reference=predicate.package,
                           note="candidate package unknown")
        ok = packages_equivalent(predicate.package, package)
        return Verdict(
            predicate, PASS if ok else FAIL,
            reference=predicate.package, candidate=package,
            note="" if ok else "different land pattern",
        )

    rows = [r for r in attributes if str(r.get("canonical_name", "")) == predicate.attribute]
    if predicate.qualifier:
        want = predicate.qualifier.strip().lower()
        rows = [r for r in rows if str(r.get("qualifier", "")).strip().lower() == want]
    row = _pick(rows, prefer)
    ref = _format_requirement(predicate)
    if row is None:
        note = _absent_note(predicate, attributes)
        return Verdict(predicate, UNKNOWN, reference=ref, note=note)

    dist = str(row.get("distributor", ""))
    raw = str(row.get("raw_value", ""))

    if predicate.op == "enum":
        want = {v.strip().lower() for v in predicate.values}
        got = raw.strip().lower()
        ok = got in want
        return Verdict(predicate, PASS if ok else FAIL, reference=ref,
                       candidate=raw, distributor=dist,
                       note="" if ok else f"expected one of {', '.join(predicate.values)}")

    target = _to_si(predicate.value, predicate.unit)  # type: ignore[arg-type]
    if target is None:
        return Verdict(predicate, UNKNOWN, reference=ref, candidate=raw, distributor=dist,
                       note=f"could not interpret requirement unit {predicate.unit!r}")
    want_value, want_unit = target

    got = _bound_of(row, predicate.bound)
    if got is None:
        note = ("stored value is a range; specify bound='lower' or 'upper'"
                if row.get("value_min") is not None else "value not numeric")
        return Verdict(predicate, UNKNOWN, reference=ref, candidate=raw, distributor=dist, note=note)

    got_unit = str(row.get("unit") or "")
    if want_unit != got_unit:
        return Verdict(predicate, UNKNOWN, reference=ref, candidate=raw, distributor=dist,
                       note=f"unit mismatch: requirement in {want_unit or 'none'!r}, "
                            f"stored in {got_unit or 'none'!r}")

    if predicate.op == "lte":
        ok = got <= want_value
    elif predicate.op == "gte":
        ok = got >= want_value
    else:
        scale = max(abs(got), abs(want_value))
        ok = got == want_value if scale == 0 else abs(got - want_value) / scale <= EQ_RELATIVE_TOLERANCE

    return Verdict(predicate, PASS if ok else FAIL, reference=ref, candidate=raw, distributor=dist,
                   note="" if ok else _explain(predicate, got, want_value, got_unit))


def evaluate_all(
    predicates: Iterable[Predicate],
    attributes: Iterable[dict[str, Any]],
    *,
    package: str | None = None,
    prefer: Sequence[str] = DISTRIBUTOR_PREFERENCE,
) -> Report:
    """Evaluate every predicate against one candidate."""
    rows = list(attributes)
    return Report([evaluate(p, rows, package=package, prefer=prefer) for p in predicates])


def _format_requirement(p: Predicate) -> str:
    if p.op == "enum":
        return " | ".join(p.values)
    symbol = {"lte": "≤", "gte": "≥", "eq": "="}[p.op]
    return f"{symbol} {_trim(p.value)}{p.unit}".strip()


def _explain(p: Predicate, got: float, want: float, unit: str) -> str:
    symbol = {"lte": "≤", "gte": "≥", "eq": "="}[p.op]
    return f"{_trim(got)}{unit} violates {symbol} {_trim(want)}{unit}"


def _trim(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:.10g}"
    return text
