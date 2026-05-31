"""
Pure helpers for distributor-capture fixture freshness and merging.

== Per-distributor timestamp format (new) ==
Each distributor block carries its own ``captured_at`` key:

    {
        "captured_at": "2026-04-06T14:37:14",   # legacy top-level mirror
        "lcsc":    {"captured_at": "2026-04-06T14:37:14", "parts": {...}, ...},
        "digikey": {"captured_at": "2026-04-06T14:37:14", "parts": {...}, ...},
        ...
    }

This lets a *partial* refresh (e.g. a public-only cron that re-pulls LCSC and
Pololu but leaves DigiKey/Mouser untouched) stamp only the refreshed blocks.
DigiKey/Mouser then retain their own older timestamps and will still be reported
as stale when checked against ``max_age_days``.

== Legacy fallback ==
Old fixtures only have the top-level ``captured_at``.  ``block_captured_at``
falls back to that key so existing fixtures continue to work until they are
regenerated.

== Mutation policy ==
All functions are pure.  ``merge_capture`` returns a new dict and never mutates
its inputs.
"""

from datetime import datetime

DISTRIBUTORS = ("lcsc", "digikey", "mouser", "pololu")


def block_captured_at(fixture: dict, distributor: str) -> "str | None":
    """Return the ISO capture timestamp for one distributor.

    Preference order:
    1. ``fixture[distributor]["captured_at"]`` (per-block, new format)
    2. ``fixture["captured_at"]``              (legacy top-level)
    3. ``None``                                (absent in both places)

    Returns ``None`` — rather than raising — when ``fixture[distributor]``
    is missing or not a dict.
    """
    block = fixture.get(distributor)
    if isinstance(block, dict):
        ts = block.get("captured_at")
        if ts is not None:
            return ts
    # fall back to legacy top-level
    return fixture.get("captured_at")


def stale_distributors(fixture: dict, scope, now: datetime, max_age_days: int = 30) -> "set[str]":
    """Return the subset of *scope* whose capture timestamp is stale.

    A distributor is considered stale when its timestamp is MISSING,
    UNPARSEABLE, or strictly older than *max_age_days* relative to *now*:

        (now - captured).days > max_age_days

    Distributors whose names are not in *scope* are never returned, even if
    they would otherwise be stale.
    """
    stale: set[str] = set()
    for name in scope:
        ts = block_captured_at(fixture, name)
        if ts is None:
            stale.add(name)
            continue
        try:
            captured = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            stale.add(name)
            continue
        if (now - captured).days > max_age_days:
            stale.add(name)
    return stale


def merge_capture(existing: dict, new_blocks: dict, now: datetime) -> dict:
    """Return a new fixture dict merging *new_blocks* into *existing*.

    - Starts with a shallow copy of *existing*.
    - For each distributor in *new_blocks*, merges a shallow copy of that
      block stamped with ``captured_at = now.isoformat(timespec='seconds')``.
    - Distributor blocks NOT in *new_blocks* are preserved byte-identically.
    - Sets the top-level ``"captured_at"`` to the same ISO string (legacy
      mirror) so old consumers of the file still see a meaningful timestamp.
    - Does NOT mutate *existing* or *new_blocks*.
    """
    iso = now.isoformat(timespec="seconds")
    result = dict(existing)
    for distributor, block in new_blocks.items():
        stamped = dict(block)
        stamped["captured_at"] = iso
        result[distributor] = stamped
    result["captured_at"] = iso
    return result
