"""Differential property test: cache_db.catch_up() vs. a full rebuild.

The second reference implementation of the property harness (the first is
``tests/python/test_federation_properties.py``); read ``docs/property-testing.md``.

This is a *differential* property, the strongest kind available here.  dubIS has
two independent implementations of "what is the stock right now":

* the **authoritative** one — ``domain.inventory.rebuild``, which replays
  ``purchase_ledger.csv`` plus every row of ``adjustments.csv`` from nothing;
* the **fast** one — ``cache_db.catch_up``, which replays only the adjustment
  rows added since the last checkpoint straight into SQLite.

``rebuild_or_catchup`` prefers the fast one on every mutation, so the fast one is
what the user actually sees; the slow one is what the numbers are *supposed* to
be.  There is no assertion anywhere that the two agree, and a disagreement has no
symptom: the cache is self-consistent, nothing throws, and the number on screen
is simply wrong until someone deletes ``cache.db``.  That is the highest-ranked
entry in the guide's backlog for exactly this reason.

The property: for any ledger and any sequence of adjustments split at any point,
replaying the tail incrementally must land on the same inventory as replaying
everything from scratch.
"""

from __future__ import annotations

import csv
import os
import sqlite3
import tempfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import cache_db
import domain.inventory
from inventory_api import InventoryApi

FIELDNAMES = InventoryApi.FIELDNAMES
ADJ_FIELDNAMES = InventoryApi.ADJ_FIELDNAMES

#: Part keys the generator draws from.  A tiny closed alphabet on purpose: the
#: interesting space is the *sequence of adjustments*, and colliding on the same
#: few parts is what makes ordering effects reachable at all.  Two are LCSC-shaped
#: and one is not, because ``inventory_ops.apply_adjustments`` branches on that
#: when a ``set`` has to invent a part.
PART_KEYS = ["C1000", "C2000", "MPN-A"]

#: Keys that appear in adjustments but may or may not be in the ledger — the
#: "adjust a part the ledger has never heard of" case.
UNKNOWN_KEYS = ["C9999", "MPN-Z"]


def _ledger_row(part_key: str, qty: int, price: float) -> dict[str, str]:
    row = {name: "" for name in FIELDNAMES}
    if part_key.upper().startswith("C") and part_key[1:].isdigit():
        row["LCSC Part Number"] = part_key
    else:
        row["Manufacture Part Number"] = part_key
    row["Description"] = f"{part_key} test part"
    row["Package"] = "0402"
    row["Quantity"] = str(qty)
    row["Unit Price($)"] = f"{price:.4f}"
    row["Ext.Price($)"] = f"{price * qty:.4f}"
    return row


ledger_rows = st.lists(
    st.builds(
        _ledger_row,
        st.sampled_from(PART_KEYS),
        st.integers(min_value=0, max_value=5000),
        st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    ),
    min_size=1,
    max_size=4,
    unique_by=lambda r: r["LCSC Part Number"] or r["Manufacture Part Number"],
)


@st.composite
def adjustment_rows(draw, max_size: int = 8) -> list[dict[str, str]]:
    """Adjustment rows as ``append_adjustment`` writes them.

    ``quantity`` is **signed** for the delta types — that is the on-disk
    convention both replay paths read, and generating an unsigned "remove 5"
    would be testing a file dubIS never writes.
    """
    rows = []
    for i in range(draw(st.integers(min_value=0, max_value=max_size))):
        adj_type = draw(st.sampled_from(["add", "remove", "consume", "set"]))
        magnitude = draw(st.integers(min_value=0, max_value=2000))
        qty = magnitude if adj_type in ("add", "set") else -magnitude
        rows.append({
            "timestamp": f"2026-01-01T00:00:{i:02d}",
            "type": adj_type,
            "lcsc_part": draw(st.sampled_from(PART_KEYS + UNKNOWN_KEYS)),
            "quantity": str(qty),
            "bom_file": "",
            "board_qty": "",
            "note": "",
            "source": "test:property",
        })
    return rows


def _write(path: str, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _ctx(base: str, conn: sqlite3.Connection) -> domain.inventory.RebuildContext:
    return domain.inventory.RebuildContext(
        base_dir=base,
        input_csv=os.path.join(base, "purchase_ledger.csv"),
        adjustments_csv=os.path.join(base, "adjustments.csv"),
        events_dir=os.path.join(base, "events"),
        fieldnames=FIELDNAMES,
        adj_fieldnames=ADJ_FIELDNAMES,
        conn=conn,
    )


def _stock(inventory) -> dict[str, int]:
    """Just the numbers: ``{part key: qty}``.

    Deliberately narrower than the whole record.  Section, sort order and price
    are the *full rebuild's* business (``catch_up`` never touches them and a
    warm cache legitimately keeps whatever populate_full last wrote), whereas
    the quantity is the one thing both paths claim to compute.
    """
    out: dict[str, int] = {}
    for item in inventory:
        key = item["lcsc"] or item["mpn"] or item["digikey"] or item["pololu"] or item["mouser"]
        out[key] = item["qty"]
    return out


def _replay(base: str, ledger: list[dict], adjustments: list[dict]) -> dict[str, int]:
    """The authoritative answer: everything from nothing."""
    os.makedirs(base, exist_ok=True)
    _write(os.path.join(base, "purchase_ledger.csv"), FIELDNAMES, ledger)
    _write(os.path.join(base, "adjustments.csv"), ADJ_FIELDNAMES, adjustments)
    conn = cache_db.connect(os.path.join(base, "cache.db"))
    try:
        cache_db.create_schema(conn)
        inventory, _ = domain.inventory.rebuild(_ctx(base, conn))
        return _stock(inventory)
    finally:
        conn.close()


def _replay_then_catch_up(base: str, ledger: list[dict],
                          prefix: list[dict], suffix: list[dict]) -> dict[str, int]:
    """The fast answer: full rebuild at the checkpoint, then incremental."""
    os.makedirs(base, exist_ok=True)
    _write(os.path.join(base, "purchase_ledger.csv"), FIELDNAMES, ledger)
    _write(os.path.join(base, "adjustments.csv"), ADJ_FIELDNAMES, prefix)
    conn = cache_db.connect(os.path.join(base, "cache.db"))
    try:
        cache_db.create_schema(conn)
        domain.inventory.rebuild(_ctx(base, conn))  # establishes the checkpoint
        _write(os.path.join(base, "adjustments.csv"), ADJ_FIELDNAMES, prefix + suffix)
        inventory, _ = domain.inventory.rebuild_or_catchup(
            base_dir=base,
            input_csv=os.path.join(base, "purchase_ledger.csv"),
            adjustments_csv=os.path.join(base, "adjustments.csv"),
            events_dir=os.path.join(base, "events"),
            fieldnames=FIELDNAMES,
            adj_fieldnames=ADJ_FIELDNAMES,
            conn=conn,
        )
        return _stock(inventory)
    finally:
        conn.close()


# Heavier than a pure-function property: every example writes two CSVs, builds
# two SQLite caches and replays the whole pipeline twice.  Rather than pin a
# constant (which would make HYPOTHESIS_PROFILE=nightly a no-op here), scale off
# whichever profile conftest.py loaded, so `bash scripts/verify.sh` keeps its
# wall-clock budget and a deep search still deepens these too.
#   dev 50 -> 12   ci 100 -> 25   nightly 2000 -> 500
HEAVY = settings(
    max_examples=max(10, settings.default.max_examples // 4),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@HEAVY
@given(ledger_rows, adjustment_rows(), st.integers(min_value=0, max_value=8))
def test_catch_up_agrees_with_a_full_rebuild(ledger, adjustments, split):
    """Replaying the tail incrementally lands where replaying everything lands.

    The one invariant that makes ``cache.db`` safe to call "a derived, deletable
    materialized view" (CLAUDE.md, Data Flow).  If this can fail, the cache is
    not derived — it is a second, disagreeing source of truth.
    """
    split = min(split, len(adjustments))
    prefix, suffix = adjustments[:split], adjustments[split:]
    with tempfile.TemporaryDirectory() as tmp:
        authoritative = _replay(os.path.join(tmp, "full"), ledger, adjustments)
        incremental = _replay_then_catch_up(
            os.path.join(tmp, "incr"), ledger, prefix, suffix)
    assert incremental == authoritative


@HEAVY
@given(ledger_rows, adjustment_rows())
def test_a_full_rebuild_is_deterministic(ledger, adjustments):
    """Two rebuilds of the same inputs agree.

    Cheap, and it separates "catch_up is wrong" from "the pipeline is not a
    function of its inputs" when the differential above goes red.
    """
    with tempfile.TemporaryDirectory() as tmp:
        first = _replay(os.path.join(tmp, "a"), ledger, adjustments)
        second = _replay(os.path.join(tmp, "b"), ledger, adjustments)
    assert first == second


@HEAVY
@given(ledger_rows, adjustment_rows())
def test_stock_is_never_negative(ledger, adjustments):
    """Every write path floors at zero, so no replay of them can go below it."""
    with tempfile.TemporaryDirectory() as tmp:
        stock = _replay(os.path.join(tmp, "full"), ledger, adjustments)
    assert all(qty >= 0 for qty in stock.values()), stock
