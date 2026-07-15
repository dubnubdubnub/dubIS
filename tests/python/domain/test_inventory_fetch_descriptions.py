"""Tests for domain.inventory.fetch_missing_descriptions."""

import csv

import pytest

import domain.inventory as inv
from inventory_api import InventoryApi
from tests.python.helpers import make_part, write_ledger


def _fetchers(mapping):
    # mapping: pn -> product dict (or None); raises for pns in `_errors`
    def make(dist):
        def fetch(pn):
            if pn in mapping.get("_errors", set()):
                raise RuntimeError("boom")
            return mapping.get(pn)
        return fetch
    return {d: make(d) for d in ("lcsc", "digikey", "mouser", "pololu")}


class _Env:
    def __init__(self, api, conn):
        self.ledger = api.input_csv
        self.kwargs = dict(
            input_csv=api.input_csv,
            adjustments_csv=api.adjustments_csv,
            adj_fieldnames=InventoryApi.ADJ_FIELDNAMES,
            base_dir=api.base_dir,
            fieldnames=InventoryApi.FIELDNAMES,
            events_dir=api.events_dir,
            conn=conn,
        )


@pytest.fixture
def fetch_desc_env(api, db, events_dir):
    """Ledger with 3 rows: needy (PN, no description), has-description (skip),
    and no-PN-at-all (never needy)."""
    write_ledger(api, [
        make_part(lcsc="C111", desc=""),
        make_part(lcsc="C222", desc="Resistor 10kΩ"),
        make_part(lcsc="", mpn="", desc=""),
    ])
    return _Env(api, db)


def test_fills_missing_description_from_lcsc(fetch_desc_env):
    env = fetch_desc_env  # provides paths + conn + a ledger with 3 rows
    fetchers = _fetchers({"C111": {"description": "Cap 47uF"}})
    out = inv.fetch_missing_descriptions(fetchers=fetchers, **env.kwargs)
    assert out["summary"]["updated"] == 1
    # the written CSV now has the description
    rows = list(csv.DictReader(open(env.ledger, encoding="utf-8-sig")))
    got = [r for r in rows if r["LCSC Part Number"] == "C111"][0]
    assert got["Description"] == "Cap 47uF"


def test_skips_rows_that_already_have_description(fetch_desc_env):
    env = fetch_desc_env
    fetchers = _fetchers({})  # nothing to fetch
    out = inv.fetch_missing_descriptions(fetchers=fetchers, **env.kwargs)
    assert out["summary"]["updated"] == 0


def test_counts_failure_when_all_distributors_error(fetch_desc_env):
    env = fetch_desc_env
    fetchers = _fetchers({"_errors": {"C111"}})
    out = inv.fetch_missing_descriptions(fetchers=fetchers, **env.kwargs)
    assert out["summary"]["failed"] == 1
    assert out["summary"]["updated"] == 0


def test_returns_fresh_inventory_list(fetch_desc_env):
    env = fetch_desc_env
    fetchers = _fetchers({"C111": {"description": "Cap 47uF"}})
    out = inv.fetch_missing_descriptions(fetchers=fetchers, **env.kwargs)
    assert isinstance(out["inventory"], list)
