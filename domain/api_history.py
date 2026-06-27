"""PartHistory facade — read-only per-part adjustment history."""

from __future__ import annotations

import csv
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.schema import PartHistoryEntry

logger = logging.getLogger(__name__)

_HISTORY_CAP = 100


def read_part_history(adjustments_csv: str, part_key: str) -> list["PartHistoryEntry"]:
    """Return chronological adjustment history for *part_key*, capped at _HISTORY_CAP.

    Reads ``adjustments.csv`` directly (no cache, no lock — pure read).
    If the file is missing or empty, returns [].

    Returns a list of dicts:
        timestamp  — ISO-8601 string from the ledger row
        kind       — adjustment type ("set", "add", "consume", …)
        qty_delta  — int delta (negative for consume, positive for add/set)
        source     — source tag (e.g. "openpnp", "import", "manual", "test:…")
        note       — free-form note string

    If the total matching rows exceed _HISTORY_CAP the result is truncated to
    the most recent _HISTORY_CAP entries and a warning is logged once.
    """
    if not os.path.exists(adjustments_csv):
        return []

    matching: list[PartHistoryEntry] = []
    try:
        with open(adjustments_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row.get("lcsc_part") or "").strip() != part_key:
                    continue
                matching.append(_parse_row(row))
    except OSError as exc:
        logger.error("get_part_history: cannot read %s: %s", adjustments_csv, exc)
        return []

    if len(matching) > _HISTORY_CAP:
        logger.warning(
            "get_part_history: %d entries for %r truncated to %d",
            len(matching), part_key, _HISTORY_CAP,
        )
        matching = matching[-_HISTORY_CAP:]

    return matching


def _parse_row(row: dict[str, str]) -> "PartHistoryEntry":
    """Convert a raw adjustments.csv row into a PartHistoryEntry dict."""
    raw_qty = row.get("quantity") or "0"
    try:
        qty_delta = int(raw_qty)
    except (ValueError, TypeError):
        qty_delta = 0

    return {
        "timestamp": (row.get("timestamp") or "").strip(),
        "kind": (row.get("type") or "").strip(),
        "qty_delta": qty_delta,
        "source": (row.get("source") or "").strip(),
        "note": (row.get("note") or "").strip(),
    }


class PartHistoryFacade:
    """Facade that exposes get_part_history on the public InventoryApi surface."""

    def __init__(self, api) -> None:
        self._api = api

    def get_part_history(self, part_key: str) -> list["PartHistoryEntry"]:
        """Return chronological adjustment history for *part_key*.

        Pure read — never acquires the write lock, never touches the cache.
        Capped to the most recent 100 entries (warning logged if truncated).
        """
        return read_part_history(self._api.adjustments_csv, part_key)
