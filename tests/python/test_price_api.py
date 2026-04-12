"""Tests for price_history API-level functions (resolve, record, summary)."""

import os
import sqlite3

import pytest

import price_history


def _seed_parts(conn):
    """Insert test parts into cache with stock."""
    parts = [
        ("C1525", "C1525", "CL05B104KO5NNNC", "", "", "", "Samsung",
         "100nF 16V 0402 Capacitor MLCC", "0402"),
        ("C2875244", "C2875244", "RC0402FR-074K7L", "", "", "", "YAGEO",
         "4.7k\u03a9 0402 Resistor", "0402"),
        ("DRV8316C", "C9000", "DRV8316C", "296-DRV8316CRRGFRCT-ND", "", "595-DRV8316CRRGFR",
         "TI", "Motor driver IC", "QFN"),
    ]
    for pid, lcsc, mpn, dk, pololu, mouser, mfr, desc, pkg in parts:
        conn.execute(
            "INSERT INTO parts (part_id, lcsc, mpn, digikey, pololu, mouser, "
            "manufacturer, description, package, section) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, lcsc, mpn, dk, pololu, mouser, mfr, desc, pkg, "Misc"),
        )
        conn.execute(
            "INSERT INTO stock (part_id, quantity, unit_price) VALUES (?,100,0.01)",
            (pid,),
        )
    conn.commit()


class TestResolvePartKey:
    def test_direct_match(self, db):
        _seed_parts(db)
        assert price_history.resolve_part_key(db, "C1525") == "C1525"

    def test_resolve_via_lcsc(self, db):
        _seed_parts(db)
        # DRV8316C has lcsc=C9000, but part_id=DRV8316C, so C9000 should resolve
        assert price_history.resolve_part_key(db, "C9000") == "DRV8316C"

    def test_resolve_via_digikey(self, db):
        _seed_parts(db)
        assert price_history.resolve_part_key(db, "296-DRV8316CRRGFRCT-ND") == "DRV8316C"

    def test_resolve_via_mpn(self, db):
        _seed_parts(db)
        assert price_history.resolve_part_key(db, "CL05B104KO5NNNC") == "C1525"

    def test_resolve_via_mouser(self, db):
        _seed_parts(db)
        assert price_history.resolve_part_key(db, "595-DRV8316CRRGFR") == "DRV8316C"

    def test_unknown_key_returns_none(self, db):
        _seed_parts(db)
        assert price_history.resolve_part_key(db, "TOTALLY-UNKNOWN") is None

    def test_cache_busy_returns_raw_key(self, db):
        """When DB is busy, falls back to returning the raw key."""
        _seed_parts(db)

        class BusyConn:
            """Simulates a connection that raises OperationalError on execute."""
            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("database is locked")

        result = price_history.resolve_part_key(BusyConn(), "C1525")
        assert result == "C1525"


class TestRecordFetchedPrices:
    def test_records_and_populates_cache(self, db, events_dir):
        _seed_parts(db)
        price_history.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
            {"qty": 10, "price": 0.0070},
        ])
        rows = db.execute(
            "SELECT * FROM prices WHERE part_id = ? AND distributor = ?",
            ("C1525", "lcsc"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["latest_unit_price"] == pytest.approx(0.0070)
        assert rows[0]["price_count"] == 2

    def test_creates_events_dir(self, db, tmp_path):
        new_events = str(tmp_path / "new_events")
        _seed_parts(db)
        price_history.record_fetched_prices(db, new_events, "C1525", "lcsc", [
            {"qty": 1, "price": 0.01},
        ])
        assert os.path.isdir(new_events)

    def test_resolves_distributor_pn(self, db, events_dir):
        _seed_parts(db)
        price_history.record_fetched_prices(
            db, events_dir, "296-DRV8316CRRGFRCT-ND", "digikey", [
                {"qty": 1, "price": 2.80},
            ],
        )
        rows = db.execute(
            "SELECT * FROM prices WHERE part_id = ? AND distributor = ?",
            ("DRV8316C", "digikey"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["latest_unit_price"] == pytest.approx(2.80)

    def test_skips_unknown_part(self, db, events_dir):
        _seed_parts(db)
        price_history.record_fetched_prices(
            db, events_dir, "TOTALLY-UNKNOWN-PN", "digikey", [
                {"qty": 1, "price": 1.00},
            ],
        )
        rows = db.execute("SELECT * FROM prices").fetchall()
        assert len(rows) == 0

    def test_skips_zero_price_tiers(self, db, events_dir):
        _seed_parts(db)
        price_history.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
            {"qty": 1, "price": 0},
            {"qty": 10, "price": -5},
        ])
        rows = db.execute("SELECT * FROM prices").fetchall()
        assert len(rows) == 0


class TestGetPriceSummary:
    def test_empty_for_nonexistent_part(self, db, events_dir):
        _seed_parts(db)
        assert price_history.get_price_summary(db, events_dir, "NONEXISTENT") == {}

    def test_returns_distributor_data(self, db, events_dir):
        _seed_parts(db)
        price_history.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
        ])
        summary = price_history.get_price_summary(db, events_dir, "C1525")
        assert "lcsc" in summary
        assert summary["lcsc"]["latest_unit_price"] == pytest.approx(0.0080)
        assert summary["lcsc"]["price_count"] == 1
        assert "last_observed" in summary["lcsc"]
        assert "moq" in summary["lcsc"]
        assert "source" in summary["lcsc"]

    def test_multiple_distributors(self, db, events_dir):
        _seed_parts(db)
        price_history.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
        ])
        price_history.record_fetched_prices(db, events_dir, "C1525", "digikey", [
            {"qty": 1, "price": 0.012},
        ])
        summary = price_history.get_price_summary(db, events_dir, "C1525")
        assert "lcsc" in summary
        assert "digikey" in summary

    def test_resolves_distributor_pn(self, db, events_dir):
        _seed_parts(db)
        price_history.record_fetched_prices(db, events_dir, "DRV8316C", "digikey", [
            {"qty": 1, "price": 2.80},
        ])
        # Query using Digikey PN
        summary = price_history.get_price_summary(db, events_dir, "296-DRV8316CRRGFRCT-ND")
        assert "digikey" in summary

    def test_cache_busy_returns_empty(self, db, events_dir):
        """When DB is busy, returns empty dict gracefully."""
        call_count = 0

        class BusyConn:
            """First call to resolve_part_key returns key, then raises on summary query."""
            def execute(self, sql, params=()):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # resolve_part_key: direct match check
                    return _FakeResult(True)
                # get_price_summary: cache busy
                raise sqlite3.OperationalError("database is locked")

        class _FakeResult:
            def __init__(self, has_row):
                self._has_row = has_row
            def fetchone(self):
                return {"part_id": "C1525"} if self._has_row else None

        result = price_history.get_price_summary(BusyConn(), events_dir, "C1525")
        assert result == {}
