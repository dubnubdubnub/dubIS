"""Predicate-evaluation facade — judge a candidate against stored parametrics.

Mirrors `domain/api_attributes.py`: `domain/predicates.py` is pure and takes
plain dicts, and this facade is what supplies them from `InventoryApi` state
under the API lock.

The candidate's package is read from the parts cache when the caller does not
supply one, so a package predicate does not silently degrade to "unknown"
merely because the caller did not think to pass it. A caller evaluating a part
that is not in inventory yet — the common case for a proposed alternate — can
still pass `package` explicitly.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from domain.predicates import Predicate, evaluate_all

logger = logging.getLogger(__name__)


def _package_of(conn: sqlite3.Connection, part_key: str) -> str:
    """The candidate's package from the parts cache, or "" if unknown."""
    try:
        row = conn.execute(
            "SELECT package FROM parts WHERE part_key = ?", (part_key,)
        ).fetchone()
    except sqlite3.Error as exc:  # a missing/malformed cache must not fail the call
        logger.warning("package lookup failed for %s: %s", part_key, exc)
        return ""
    if row is None:
        return ""
    return str(row["package"] if isinstance(row, sqlite3.Row) else row[0]) or ""


class PredicatesFacade:
    """Evaluate substitution requirements for a part held in this inventory."""

    def __init__(self, api: Any) -> None:
        self._api = api

    def evaluate_part_predicates(
        self,
        part_key: str,
        predicates: list[dict[str, Any]] | None,
        package: str | None = None,
        prefer: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run `predicates` against `part_key`'s stored attributes.

        Returns the report envelope: overall `status`, one entry per predicate
        in `verdicts`, the non-passing subset as `spec_deltas` (ready to record
        on a generic-part member review), and `blockers` for the blocking
        failures that make a candidate unapprovable.

        Raises ValueError for a malformed predicate -- surfaced as a 400 rather
        than silently dropping a requirement, since a requirement that quietly
        vanishes reads as a pass.
        """
        specs = [Predicate(**_clean(p)) for p in (predicates or [])]
        with self._api._lock:
            conn = self._api._get_cache()
            attributes = self._api._attrs.get_part_attributes(part_key)
            resolved_package = package if package is not None else _package_of(conn, part_key)

        report = evaluate_all(
            specs, attributes,
            package=resolved_package or None,
            **({"prefer": tuple(prefer)} if prefer else {}),
        )
        return {
            "part_key": part_key,
            "package": resolved_package,
            "status": report.status,
            "verdicts": [
                {
                    "field": v.predicate.display,
                    "kind": v.kind,
                    "status": v.status,
                    "blocking": bool(v.predicate.blocking),
                    "reference": v.reference,
                    "candidate": v.candidate,
                    "distributor": v.distributor,
                    "note": v.note,
                }
                for v in report.verdicts
            ],
            "spec_deltas": report.spec_deltas(),
            "blockers": [v.predicate.display for v in report.blockers],
        }


_ALLOWED = {
    "attribute", "op", "bound", "value", "unit", "values", "package",
    "qualifier", "blocking", "label", "note",
}


def _clean(raw: Any) -> dict[str, Any]:
    """Keep only recognised predicate fields, and refuse unknown ones loudly.

    A typo'd key silently ignored would drop a requirement the caller believes
    is being enforced, which is the one failure mode worse than rejecting the
    request.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"predicate must be an object, got {type(raw).__name__}")
    unknown = set(raw) - _ALLOWED
    if unknown:
        raise ValueError(
            f"unknown predicate field(s) {', '.join(sorted(unknown))}; "
            f"expected any of {', '.join(sorted(_ALLOWED))}"
        )
    out = dict(raw)
    if "values" in out and out["values"] is not None:
        out["values"] = tuple(out["values"])
    return {k: v for k, v in out.items() if v is not None}
