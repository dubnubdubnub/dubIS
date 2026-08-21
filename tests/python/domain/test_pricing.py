"""Tests for domain.pricing — price/quantity parsing, observation history, and API helpers."""

import csv
import json
import os
import sqlite3

import pytest

import domain.pricing
from domain.pricing import derive_missing_price, ensure_parsed, parse_price, parse_qty


# ── parse_qty ──────────────────────────────────────────────────────────────


class TestParseQty:
    """Tests for parse_qty()."""

    def test_integer_string(self):
        assert parse_qty("10") == 10

    def test_float_string_truncates(self):
        assert parse_qty("10.7") == 10

    def test_with_commas(self):
        assert parse_qty("1,000") == 1000

    def test_large_number_with_commas(self):
        assert parse_qty("1,234,567") == 1234567

    def test_empty_string_returns_default(self):
        assert parse_qty("") == 0

    def test_none_returns_default(self):
        assert parse_qty(None) == 0

    def test_custom_default(self):
        assert parse_qty("", default=-1) == -1

    def test_malformed_string_returns_default(self):
        assert parse_qty("abc") == 0

    def test_negative_value(self):
        assert parse_qty("-5") == -5

    def test_zero(self):
        assert parse_qty("0") == 0

    def test_whitespace_around_number(self):
        # str() + float() handles whitespace
        assert parse_qty(" 42 ") == 42

    def test_integer_input(self):
        assert parse_qty(100) == 100

    def test_float_input(self):
        assert parse_qty(3.9) == 3

    def test_negative_float(self):
        assert parse_qty("-2.8") == -2


# ── parse_price ────────────────────────────────────────────────────────────


class TestParsePrice:
    """Tests for parse_price()."""

    def test_plain_number(self):
        assert parse_price("1.25") == 1.25

    def test_dollar_sign(self):
        assert parse_price("$5.99") == 5.99

    def test_with_commas(self):
        assert parse_price("1,234.56") == 1234.56

    def test_dollar_and_commas(self):
        assert parse_price("$10,000.00") == 10000.00

    def test_empty_string_returns_zero(self):
        assert parse_price("") == 0.0

    def test_none_returns_default(self):
        assert parse_price(None) == 0.0

    def test_custom_default(self):
        assert parse_price("bad", default=-1.0) == -1.0

    def test_malformed_returns_default(self):
        assert parse_price("not-a-price") == 0.0

    def test_zero(self):
        assert parse_price("0") == 0.0

    def test_negative_price(self):
        assert parse_price("-3.50") == -3.50

    def test_integer_input(self):
        assert parse_price(5) == 5.0

    def test_float_input(self):
        assert parse_price(2.5) == 2.5

    def test_just_dollar_sign(self):
        """A lone '$' should parse to 0."""
        assert parse_price("$") == 0.0

    def test_whitespace(self):
        assert parse_price(" 4.20 ") == 4.20

    def test_large_price(self):
        assert parse_price("$99,999.99") == 99999.99


# ── ensure_parsed ──────────────────────────────────────────────────────────


class TestEnsureParsed:
    """Tests for ensure_parsed()."""

    def test_parses_json_string(self):
        result = ensure_parsed('{"a": 1}')
        assert result == {"a": 1}

    def test_parses_json_array_string(self):
        result = ensure_parsed('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_returns_dict_as_is(self):
        d = {"key": "value"}
        assert ensure_parsed(d) is d

    def test_returns_list_as_is(self):
        lst = [1, 2, 3]
        assert ensure_parsed(lst) is lst

    def test_returns_int_as_is(self):
        assert ensure_parsed(42) == 42

    def test_returns_none_as_is(self):
        assert ensure_parsed(None) is None

    def test_raises_on_invalid_json_string(self):
        with pytest.raises(json.JSONDecodeError):
            ensure_parsed("{bad json")

    def test_parses_nested_json(self):
        nested = '{"items": [{"name": "R1", "qty": 10}]}'
        result = ensure_parsed(nested)
        assert result["items"][0]["name"] == "R1"

    def test_parses_json_null_string(self):
        assert ensure_parsed("null") is None

    def test_parses_json_boolean_string(self):
        assert ensure_parsed("true") is True
        assert ensure_parsed("false") is False


# ── derive_missing_price ───────────────────────────────────────────────────


class TestDeriveMissingPrice:
    def test_derive_ext_from_unit_and_qty(self):
        unit, ext = derive_missing_price(2.50, None, 10)
        assert unit == 2.50
        assert ext == 25.00

    def test_derive_unit_from_ext_and_qty(self):
        unit, ext = derive_missing_price(None, 25.00, 10)
        assert unit == 2.50
        assert ext == 25.00

    def test_both_provided_returns_unchanged(self):
        unit, ext = derive_missing_price(3.00, 30.00, 10)
        assert unit == 3.00
        assert ext == 30.00

    def test_neither_provided_returns_nones(self):
        unit, ext = derive_missing_price(None, None, 10)
        assert unit is None
        assert ext is None

    def test_zero_qty_does_not_divide(self):
        unit, ext = derive_missing_price(None, 25.00, 0)
        assert unit is None
        assert ext == 25.00

    def test_zero_unit_price_returns_unchanged(self):
        unit, ext = derive_missing_price(0.0, None, 10)
        assert unit == 0.0
        assert ext is None


# ── record_observations ────────────────────────────────────────────────────


class TestRecordObservation:
    def test_creates_csv_with_header(self, events_dir):
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.0074,
             "source": "import"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        assert os.path.exists(csv_path)
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert "timestamp" in reader.fieldnames
            assert "part_id" in reader.fieldnames
            assert "distributor" in reader.fieldnames
            assert "unit_price" in reader.fieldnames

    def test_appends_rows(self, events_dir):
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.0074,
             "source": "import"},
        ])
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.0080,
             "source": "manual"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert float(rows[0]["unit_price"]) == pytest.approx(0.0074)
        assert float(rows[1]["unit_price"]) == pytest.approx(0.0080)

    def test_timestamp_auto_filled(self, events_dir):
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01,
             "source": "import"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["timestamp"]  # not empty
        assert "T" in row["timestamp"]  # ISO format

    def test_optional_fields_default_empty(self, events_dir):
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01,
             "source": "import"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["currency"] == ""
        assert row["moq"] == ""
        assert row["note"] == ""


# ── read_observations ──────────────────────────────────────────────────────


class TestReadObservations:
    def test_read_all(self, events_dir):
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.010,
             "source": "import"},
        ])
        obs = domain.pricing.read_observations(events_dir)
        assert len(obs) == 2

    def test_read_filtered_by_part(self, events_dir):
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C9999", "distributor": "lcsc", "unit_price": 0.050,
             "source": "import"},
        ])
        obs = domain.pricing.read_observations(events_dir, part_id="C1525")
        assert len(obs) == 1
        assert obs[0]["part_id"] == "C1525"

    def test_read_empty_returns_empty(self, events_dir):
        obs = domain.pricing.read_observations(events_dir)
        assert obs == []


# ── populate_prices_cache ──────────────────────────────────────────────────


class TestPopulatePricesCache:
    def test_populate_aggregates_by_distributor(self, events_dir, tmp_path):
        import cache_db
        conn = cache_db.connect(str(tmp_path / "cache.db"))
        cache_db.create_schema(conn)
        conn.execute("INSERT INTO parts (part_id) VALUES ('C1525')")
        conn.execute("INSERT INTO stock (part_id, quantity) VALUES ('C1525', 100)")
        conn.commit()

        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.009,
             "source": "import"},
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.012,
             "source": "import"},
        ])

        domain.pricing.populate_prices_cache(conn, events_dir)

        lcsc = conn.execute(
            "SELECT * FROM prices WHERE part_id='C1525' AND distributor='lcsc'"
        ).fetchone()
        assert lcsc["price_count"] == 2
        assert lcsc["latest_unit_price"] == pytest.approx(0.009)
        assert lcsc["avg_unit_price"] == pytest.approx(0.008)  # (0.007+0.009)/2

        dk = conn.execute(
            "SELECT * FROM prices WHERE part_id='C1525' AND distributor='digikey'"
        ).fetchone()
        assert dk["price_count"] == 1
        assert dk["latest_unit_price"] == pytest.approx(0.012)

        conn.close()


# ── resolve_part_key ───────────────────────────────────────────────────────


def _seed_parts(conn):
    """Insert test parts into cache with stock."""
    parts = [
        ("C1525", "C1525", "CL05B104KO5NNNC", "", "", "", "Samsung",
         "100nF 16V 0402 Capacitor MLCC", "0402"),
        ("C2875244", "C2875244", "RC0402FR-074K7L", "", "", "", "YAGEO",
         "4.7kΩ 0402 Resistor", "0402"),
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
        assert domain.pricing.resolve_part_key(db, "C1525") == "C1525"

    def test_resolve_via_lcsc(self, db):
        _seed_parts(db)
        # DRV8316C has lcsc=C9000, but part_id=DRV8316C, so C9000 should resolve
        assert domain.pricing.resolve_part_key(db, "C9000") == "DRV8316C"

    def test_resolve_via_digikey(self, db):
        _seed_parts(db)
        assert domain.pricing.resolve_part_key(db, "296-DRV8316CRRGFRCT-ND") == "DRV8316C"

    def test_resolve_via_mpn(self, db):
        _seed_parts(db)
        assert domain.pricing.resolve_part_key(db, "CL05B104KO5NNNC") == "C1525"

    def test_resolve_via_mouser(self, db):
        _seed_parts(db)
        assert domain.pricing.resolve_part_key(db, "595-DRV8316CRRGFR") == "DRV8316C"

    def test_unknown_key_returns_none(self, db):
        _seed_parts(db)
        assert domain.pricing.resolve_part_key(db, "TOTALLY-UNKNOWN") is None

    def test_cache_busy_returns_raw_key(self, db):
        """When DB is busy, falls back to returning the raw key."""
        _seed_parts(db)

        class BusyConn:
            """Simulates a connection that raises OperationalError on execute."""
            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("database is locked")

        result = domain.pricing.resolve_part_key(BusyConn(), "C1525")
        assert result == "C1525"


# ── record_fetched_prices ──────────────────────────────────────────────────


class TestRecordFetchedPrices:
    def test_records_and_populates_cache(self, db, events_dir):
        _seed_parts(db)
        domain.pricing.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
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
        domain.pricing.record_fetched_prices(db, new_events, "C1525", "lcsc", [
            {"qty": 1, "price": 0.01},
        ])
        assert os.path.isdir(new_events)

    def test_resolves_distributor_pn(self, db, events_dir):
        _seed_parts(db)
        domain.pricing.record_fetched_prices(
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
        domain.pricing.record_fetched_prices(
            db, events_dir, "TOTALLY-UNKNOWN-PN", "digikey", [
                {"qty": 1, "price": 1.00},
            ],
        )
        rows = db.execute("SELECT * FROM prices").fetchall()
        assert len(rows) == 0

    def test_skips_zero_price_tiers(self, db, events_dir):
        _seed_parts(db)
        domain.pricing.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
            {"qty": 1, "price": 0},
            {"qty": 10, "price": -5},
        ])
        rows = db.execute("SELECT * FROM prices").fetchall()
        assert len(rows) == 0


# ── get_price_summary ──────────────────────────────────────────────────────


class TestGetPriceSummary:
    def test_empty_for_nonexistent_part(self, db, events_dir):
        _seed_parts(db)
        assert domain.pricing.get_price_summary(db, events_dir, "NONEXISTENT") == {}

    def test_returns_distributor_data(self, db, events_dir):
        _seed_parts(db)
        domain.pricing.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
        ])
        summary = domain.pricing.get_price_summary(db, events_dir, "C1525")
        assert "lcsc" in summary
        assert summary["lcsc"]["latest_unit_price"] == pytest.approx(0.0080)
        assert summary["lcsc"]["price_count"] == 1
        assert "last_observed" in summary["lcsc"]
        assert "moq" in summary["lcsc"]
        assert "source" in summary["lcsc"]

    def test_multiple_distributors(self, db, events_dir):
        _seed_parts(db)
        domain.pricing.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
        ])
        domain.pricing.record_fetched_prices(db, events_dir, "C1525", "digikey", [
            {"qty": 1, "price": 0.012},
        ])
        summary = domain.pricing.get_price_summary(db, events_dir, "C1525")
        assert "lcsc" in summary
        assert "digikey" in summary

    def test_resolves_distributor_pn(self, db, events_dir):
        _seed_parts(db)
        domain.pricing.record_fetched_prices(db, events_dir, "DRV8316C", "digikey", [
            {"qty": 1, "price": 2.80},
        ])
        # Query using Digikey PN
        summary = domain.pricing.get_price_summary(db, events_dir, "296-DRV8316CRRGFRCT-ND")
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

        result = domain.pricing.get_price_summary(BusyConn(), events_dir, "C1525")
        assert result == {}


# ── get_sourced_distributors ───────────────────────────────────────────────


from domain.pricing import get_sourced_distributors


def _parts_conn():
    """In-memory parts table matching the columns resolve_part_key/query use."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE parts (part_id TEXT PRIMARY KEY, lcsc TEXT DEFAULT '', "
        "mpn TEXT DEFAULT '', digikey TEXT DEFAULT '', pololu TEXT DEFAULT '', "
        "mouser TEXT DEFAULT '')"
    )
    return conn


def _write_ledger(path, rows):
    cols = ["Digikey Part Number", "LCSC Part Number", "Pololu Part Number",
            "Mouser Part Number", "Manufacture Part Number", "Quantity"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


class TestGetSourcedDistributors:
    def test_has_pn_only(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C555', 'C555')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [])
        out = get_sourced_distributors(conn, str(ledger), "C555")
        assert out == [{"distributor": "lcsc", "part_number": "C555"}]

    def test_purchased_recovers_distributor_absent_from_record(self, tmp_path):
        # Record only shows LCSC (last-write-wins), but the part was also bought
        # from Digikey earlier — the ledger must recover the Digikey row.
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C555', 'C555')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [
            {"LCSC Part Number": "C555", "Quantity": "10"},
            {"Digikey Part Number": "DK-555", "LCSC Part Number": "C555", "Quantity": "5"},
        ])
        out = get_sourced_distributors(conn, str(ledger), "C555")
        assert out == [
            {"distributor": "lcsc", "part_number": "C555"},
            {"distributor": "digikey", "part_number": "DK-555"},
        ]

    def test_record_pn_preferred_over_ledger_pn(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C555', 'C555')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [{"LCSC Part Number": "C555-OLD", "Manufacture Part Number": "X"}])
        # Ledger row matches via nothing shared → not matched; use a shared key:
        _write_ledger(ledger, [{"LCSC Part Number": "C555"}])
        out = get_sourced_distributors(conn, str(ledger), "C555")
        # record PN 'C555' wins (identical here); single lcsc entry, no dup.
        assert out == [{"distributor": "lcsc", "part_number": "C555"}]

    def test_most_recent_ledger_pn_wins(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, mpn) VALUES ('XYZ', 'XYZ')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [
            {"Digikey Part Number": "DK-OLD", "Manufacture Part Number": "XYZ"},
            {"Digikey Part Number": "DK-NEW", "Manufacture Part Number": "XYZ"},
        ])
        out = get_sourced_distributors(conn, str(ledger), "XYZ")
        assert {"distributor": "digikey", "part_number": "DK-NEW"} in out
        assert {"distributor": "digikey", "part_number": "DK-OLD"} not in out

    def test_no_match_returns_empty(self, tmp_path):
        conn = _parts_conn()
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [{"LCSC Part Number": "C999"}])
        assert get_sourced_distributors(conn, str(ledger), "C555") == []

    def test_missing_ledger_file_uses_record_only(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, digikey) VALUES ('DK1', 'DK1')")
        out = get_sourced_distributors(conn, str(tmp_path / "nope.csv"), "DK1")
        assert out == [{"distributor": "digikey", "part_number": "DK1"}]


from domain.pricing import get_sourced_distributors_batch


class TestGetSourcedDistributorsBatch:
    def test_matches_per_call_for_each_key(self, tmp_path):
        # Same inputs the per-call test uses → the batch result per key must be
        # identical to calling get_sourced_distributors once per key.
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C555', 'C555')")
        conn.execute("INSERT INTO parts (part_id, mpn) VALUES ('XYZ', 'XYZ')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [
            {"LCSC Part Number": "C555", "Quantity": "10"},
            {"Digikey Part Number": "DK-555", "LCSC Part Number": "C555", "Quantity": "5"},
            {"Digikey Part Number": "DK-OLD", "Manufacture Part Number": "XYZ"},
            {"Digikey Part Number": "DK-NEW", "Manufacture Part Number": "XYZ"},
        ])
        keys = ["C555", "XYZ", "MISSING"]
        batch = get_sourced_distributors_batch(conn, str(ledger), keys)
        for k in keys:
            assert batch[k] == get_sourced_distributors(conn, str(ledger), k)
        # spot-check the union/recency behavior survived batching
        assert batch["C555"] == [
            {"distributor": "lcsc", "part_number": "C555"},
            {"distributor": "digikey", "part_number": "DK-555"},
        ]
        assert {"distributor": "digikey", "part_number": "DK-NEW"} in batch["XYZ"]
        assert batch["MISSING"] == []

    def test_reads_ledger_once_regardless_of_key_count(self, tmp_path, monkeypatch):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C1', 'C1')")
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C2', 'C2')")
        conn.execute("INSERT INTO parts (part_id, lcsc) VALUES ('C3', 'C3')")
        ledger = tmp_path / "purchase_ledger.csv"
        _write_ledger(ledger, [{"LCSC Part Number": "C1"}])

        opens = {"n": 0}
        real_open = open

        def counting_open(path, *a, **k):
            if str(path) == str(ledger):
                opens["n"] += 1
            return real_open(path, *a, **k)

        monkeypatch.setattr("builtins.open", counting_open)
        get_sourced_distributors_batch(conn, str(ledger), ["C1", "C2", "C3"])
        assert opens["n"] == 1  # single ledger read for the whole batch

    def test_empty_keys_returns_empty_dict(self, tmp_path):
        conn = _parts_conn()
        assert get_sourced_distributors_batch(conn, str(tmp_path / "nope.csv"), []) == {}

    def test_missing_ledger_file_uses_record_only(self, tmp_path):
        conn = _parts_conn()
        conn.execute("INSERT INTO parts (part_id, digikey) VALUES ('DK1', 'DK1')")
        out = get_sourced_distributors_batch(conn, str(tmp_path / "nope.csv"), ["DK1"])
        assert out == {"DK1": [{"distributor": "digikey", "part_number": "DK1"}]}


# ── packaging columns + header migration ────────────────────────────────────

# The exact header shipped before the packaging columns existed. Deployments
# (including the cluster PVC) hold files with this header, so it is spelled out
# literally rather than derived — deriving it from FIELDNAMES would make the
# migration tests move with the code they are supposed to pin.
LEGACY_FIELDNAMES = ["timestamp", "part_id", "distributor", "unit_price",
                     "currency", "source", "moq", "note"]

LEGACY_ROWS = [
    ["2026-01-01T00:00:00", "C1525", "lcsc", "0.0074", "USD", "import", "1", ""],
    ["2026-02-01T00:00:00", "C1525", "lcsc", "0.0070", "", "live_fetch", "10", "hi"],
    ["2026-03-01T00:00:00", "C2875244", "digikey", "0.0100", "USD", "manual", "", "x"],
]


def _write_legacy(events_dir, rows=LEGACY_ROWS, fieldnames=LEGACY_FIELDNAMES):
    """Write a pre-packaging 8-column observations file, as deployed today."""
    path = os.path.join(events_dir, domain.pricing.OBSERVATIONS_FILE)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fieldnames)
        w.writerows(rows)
    return path


def _header(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return next(csv.reader(f))


class TestPackagingSchema:
    def test_packaging_columns_appended_not_inserted(self):
        """Legacy columns keep their order and position; new ones come after."""
        assert domain.pricing.FIELDNAMES[:8] == LEGACY_FIELDNAMES
        assert domain.pricing.FIELDNAMES[8:] == domain.pricing.PACKAGING_FIELDNAMES

    def test_carrier_and_reel_derived_from_name(self, events_dir):
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.01,
             "moq": 1, "packaging": "Cut Tape (CT)"},
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.005,
             "moq": 3000, "packaging": "Tape & Reel (TR)", "reel_qty": "3,000",
             "reel_fee": 0},
        ])
        rows = domain.pricing.read_observations(events_dir)
        assert [r["packaging"] for r in rows] == ["Cut Tape (CT)", "Tape & Reel (TR)"]
        assert [r["carrier"] for r in rows] == ["tape", "tape"]
        # carrier alone cannot separate these two — is_reel is what does.
        assert [r["is_reel"] for r in rows] == ["0", "1"]
        assert [r["reel_qty"] for r in rows] == ["", "3000"]
        # reelPrice 0 means "will not custom-reel" -> unknown, not $0.00
        assert [r["reel_fee"] for r in rows] == ["", ""]

    def test_explicit_flags_override_derived(self, events_dir):
        """LCSC publishes an authoritative isReel that contradicts its own name."""
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C393939", "distributor": "lcsc", "unit_price": 0.02,
             "moq": 1, "packaging": "Reel", "is_reel": False, "reel_fee": "3"},
        ])
        row = domain.pricing.read_observations(events_dir)[0]
        assert row["packaging"] == "Reel"
        assert row["carrier"] == "tape"   # still derived
        assert row["is_reel"] == "0"      # not derived — the caller knew better
        assert row["reel_fee"] == "3.0"

    def test_the_stored_wire_format_survives_a_round_trip(self, events_dir):
        """`"0"` is what this writer emits and what cart_qty reads back as
        False, so handing a row's own value back must not invert it. A
        non-empty string is truthy in Python, which is exactly how every
        cut-tape row would silently become a reel -- and a reel ladder's lowest
        break IS the reel quantity, so the mislabelled row answers a 200-piece
        shortfall with a 5,000-piece reel."""
        for wire, expected in (("0", "0"), ("1", "1"), ("false", "0"),
                               ("False", "0"), ("true", "1")):
            domain.pricing.record_observations(events_dir, [
                {"part_id": f"C{wire}", "distributor": "lcsc", "unit_price": 0.01,
                 "moq": 1, "packaging": "Tape & Reel", "is_reel": wire},
            ])
        rows = {r["part_id"]: r["is_reel"]
                for r in domain.pricing.read_observations(events_dir)}
        assert rows == {"C0": "0", "C1": "1", "Cfalse": "0",
                        "CFalse": "0", "Ctrue": "1"}

    def test_an_empty_flag_defers_to_the_name_rather_than_recording_a_guess(self, events_dir):
        """"" means the caller said nothing, same as None -- so the packaging
        name decides, and only a nameless packaging stays unknown."""
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C_named", "distributor": "lcsc", "unit_price": 0.01,
             "moq": 1, "packaging": "Cut Tape (CT)", "is_reel": ""},
            {"part_id": "C_nameless", "distributor": "lcsc", "unit_price": 0.01,
             "moq": 1, "is_reel": ""},
        ])
        rows = {r["part_id"]: r["is_reel"]
                for r in domain.pricing.read_observations(events_dir)}
        assert rows == {"C_named": "0", "C_nameless": ""}

    def test_unknown_is_distinguishable_from_bulk(self, events_dir):
        """A missing packaging must never become a real carrier."""
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1", "distributor": "lcsc", "unit_price": 1.0, "moq": 1},
            {"part_id": "C2", "distributor": "lcsc", "unit_price": 1.0, "moq": 1,
             "packaging": "Bulk"},
        ])
        unknown, bulk = domain.pricing.read_observations(events_dir)
        assert (unknown["packaging"], unknown["carrier"], unknown["is_reel"]) == ("", "", "")
        assert (bulk["packaging"], bulk["carrier"], bulk["is_reel"]) == ("Bulk", "bulk", "0")

    def test_unrecognised_name_leaves_carrier_unknown(self, events_dir):
        """Scraped prose we don't have a token for stays unknown, not bulk."""
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1", "distributor": "digikey", "unit_price": 1.0,
             "moq": 1, "packaging": "Bespoke Vendor Wording"},
        ])
        row = domain.pricing.read_observations(events_dir)[0]
        assert row["packaging"] == "Bespoke Vendor Wording"
        assert row["carrier"] == ""
        assert row["is_reel"] == "0"


class TestObservationHeaderMigration:
    def test_legacy_file_migrates_on_append(self, events_dir):
        path = _write_legacy(events_dir)
        assert _header(path) == LEGACY_FIELDNAMES
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.006,
             "moq": 100, "packaging": "Reel"},
        ])
        assert _header(path) == domain.pricing.FIELDNAMES

    def test_legacy_rows_survive_migration_intact(self, events_dir):
        """No data loss: every legacy cell round-trips, byte for byte."""
        path = _write_legacy(events_dir)
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.006,
             "moq": 100, "packaging": "Reel"},
        ])
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(LEGACY_ROWS) + 1
        for original, migrated in zip(LEGACY_ROWS, rows):
            assert [migrated[c] for c in LEGACY_FIELDNAMES] == original

    def test_legacy_rows_read_as_packaging_unknown(self, events_dir):
        _write_legacy(events_dir)
        for row in domain.pricing.read_observations(events_dir):
            for col in domain.pricing.PACKAGING_FIELDNAMES:
                assert row[col] == "", col

    def test_legacy_file_readable_without_migrating(self, events_dir):
        """Reading is not a write: an unmigrated file still loads, unchanged."""
        path = _write_legacy(events_dir)
        with open(path, "rb") as f:
            before = f.read()
        rows = domain.pricing.read_observations(events_dir, part_id="C1525")
        assert len(rows) == 2
        assert all(r["packaging"] == "" for r in rows)
        with open(path, "rb") as f:
            assert f.read() == before

    def test_explicit_migration_is_idempotent(self, events_dir):
        """Second migrate leaves the file byte-identical — no rewrite churn."""
        _write_legacy(events_dir)
        path = os.path.join(events_dir, domain.pricing.OBSERVATIONS_FILE)
        assert domain.pricing.migrate_observations(events_dir) is True
        with open(path, "rb") as f:
            once = f.read()
        assert domain.pricing.migrate_observations(events_dir) is True
        with open(path, "rb") as f:
            assert f.read() == once
        assert _header(path) == domain.pricing.FIELDNAMES

    def test_already_migrated_file_is_untouched(self, events_dir):
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01,
             "moq": 1, "packaging": "Cut Tape"},
        ])
        path = os.path.join(events_dir, domain.pricing.OBSERVATIONS_FILE)
        with open(path, "rb") as f:
            before = f.read()
        assert domain.pricing.migrate_observations(events_dir) is True
        with open(path, "rb") as f:
            assert f.read() == before

    def test_absent_file_is_not_created_by_migration(self, events_dir):
        assert domain.pricing.migrate_observations(events_dir) is False
        assert not os.path.exists(
            os.path.join(events_dir, domain.pricing.OBSERVATIONS_FILE))

    def test_absent_dir_is_not_created_by_migration(self, tmp_path):
        missing = str(tmp_path / "nope")
        assert domain.pricing.migrate_observations(missing) is False
        assert not os.path.isdir(missing)

    def test_empty_file_gets_a_full_header(self, events_dir):
        """A 0-byte file (crash between create and write) must not stay headerless."""
        path = os.path.join(events_dir, domain.pricing.OBSERVATIONS_FILE)
        open(path, "w").close()
        assert domain.pricing.migrate_observations(events_dir) is False
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01, "moq": 1},
        ])
        assert _header(path) == domain.pricing.FIELDNAMES
        assert len(domain.pricing.read_observations(events_dir)) == 1

    def test_header_only_legacy_file_migrates(self, events_dir):
        path = _write_legacy(events_dir, rows=[])
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01, "moq": 1},
        ])
        assert _header(path) == domain.pricing.FIELDNAMES
        assert len(domain.pricing.read_observations(events_dir)) == 1

    def test_missing_events_dir_is_created_on_write(self, tmp_path):
        events_dir = str(tmp_path / "fresh" / "events")
        domain.pricing.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01, "moq": 1},
        ])
        assert len(domain.pricing.read_observations(events_dir)) == 1

    def test_legacy_file_populates_cache(self, events_dir, db):
        """A deployment that never appends still serves its old observations."""
        _seed_parts(db)
        _write_legacy(events_dir)
        domain.pricing.populate_prices_cache(db, events_dir)
        rows = db.execute(
            "SELECT * FROM prices WHERE part_id = 'C1525' AND distributor = 'lcsc'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["price_count"] == 2


class TestRecordFetchedPricesPackaging:
    def test_no_packaging_records_unchanged_rows(self, db, events_dir):
        """The pre-change call shape still writes the pre-change values."""
        _seed_parts(db)
        domain.pricing.record_fetched_prices(db, events_dir, "C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
            {"qty": 10, "price": 0.0070},
        ])
        rows = domain.pricing.read_observations(events_dir)
        assert [(r["moq"], r["unit_price"], r["source"]) for r in rows] == [
            ("1", "0.008", "live_fetch"), ("10", "0.007", "live_fetch"),
        ]
        for r in rows:
            for col in domain.pricing.PACKAGING_FIELDNAMES:
                assert r[col] == "", col

    def test_every_packaging_ladder_is_recorded(self, db, events_dir):
        """Digikey's Cut Tape and Tape & Reel ladders both land, each tagged."""
        _seed_parts(db)
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "digikey",
            [{"qty": 1, "price": 0.10}],
            packagings=[
                {"name": "Cut Tape (CT)", "carrier": "tape", "isReel": False,
                 "prices": [{"qty": 1, "price": 0.10}, {"qty": 10, "price": 0.09}]},
                {"name": "Tape & Reel (TR)", "carrier": "tape", "isReel": True,
                 "prices": [{"qty": 3000, "price": 0.04}]},
            ],
            reel_qty=3000, reel_fee=None,
        )
        rows = domain.pricing.read_observations(events_dir)
        assert [(r["packaging"], r["moq"], r["is_reel"]) for r in rows] == [
            ("Cut Tape (CT)", "1", "0"),
            ("Cut Tape (CT)", "10", "0"),
            ("Tape & Reel (TR)", "3000", "1"),
        ]
        assert {r["reel_qty"] for r in rows} == {"3000"}

    def test_active_ladder_not_double_recorded(self, db, events_dir):
        """`prices` IS the active packaging's ladder — recording both duplicates it."""
        _seed_parts(db)
        tiers = [{"qty": 1, "price": 0.10}, {"qty": 10, "price": 0.09}]
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "digikey", tiers,
            packagings=[{"name": "Cut Tape", "prices": tiers}],
        )
        assert len(domain.pricing.read_observations(events_dir)) == 2

    def test_priceless_packagings_fall_back_to_tiers(self, db, events_dir):
        """The DOM-only Digikey scrape knows the names but not whose ladder is whose."""
        _seed_parts(db)
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "digikey",
            [{"qty": 1, "price": 0.10}],
            packagings=[{"name": "Cut Tape (CT)", "prices": []},
                        {"name": "Tape & Reel (TR)", "prices": []}],
        )
        rows = domain.pricing.read_observations(events_dir)
        assert len(rows) == 1
        assert rows[0]["packaging"] == ""

    def test_per_packaging_packet_qty_wins(self, db, events_dir):
        """LCSC's per-entry packetQty beats the product-level reel qty."""
        _seed_parts(db)
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "lcsc", [],
            packagings=[{"name": "Reel", "packetQty": 4000, "isReel": True,
                         "prices": [{"qty": 1, "price": 0.02}]}],
            reel_qty=3000, reel_fee=3.0,
        )
        row = domain.pricing.read_observations(events_dir)[0]
        assert row["reel_qty"] == "4000"
        assert row["reel_fee"] == "3.0"

    def test_appends_packaged_rows_onto_a_legacy_file(self, db, events_dir):
        """The real upgrade path: live deployment, old file, new fetch."""
        _seed_parts(db)
        _write_legacy(events_dir)
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "digikey", [],
            packagings=[{"name": "Tray", "prices": [{"qty": 1, "price": 1.5}]}],
        )
        rows = domain.pricing.read_observations(events_dir)
        assert [r["packaging"] for r in rows] == ["", "", "", "Tray"]
        assert rows[-1]["carrier"] == "tray"


# ── prices-cache `moq` across packagings ───────────────────────────────────
#
# `moq` is one scalar per (part_id, distributor), filled last-row-wins. It
# reaches an agent verbatim through tools/dubis-mcp/server.py's `price_summary`
# tool, so a wrong number there is not cosmetic. With per-packaging ladders
# recorded, no single value is correct — so it is NULL when the moq-bearing
# observations span more than one packaging, and unchanged otherwise.


class TestPricesCacheMoqAcrossPackagings:
    def _moq(self, db, pid="C1525", dist="digikey"):
        row = db.execute(
            "SELECT moq FROM prices WHERE part_id = ? AND distributor = ?",
            (pid, dist),
        ).fetchone()
        return row["moq"] if row else "NO ROW"

    def test_single_packaging_keeps_last_row_wins(self, db, events_dir):
        """One packaging -> the pre-change value, untouched."""
        _seed_parts(db)
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "digikey", [],
            packagings=[{"name": "Cut Tape (CT)",
                         "prices": [{"qty": 1, "price": 0.10},
                                    {"qty": 10, "price": 0.09}]}],
        )
        assert self._moq(db) == 10

    def test_legacy_file_keeps_last_row_wins(self, db, events_dir):
        """All-unknown packaging is still ONE packaging — byte-identical result."""
        _seed_parts(db)
        _write_legacy(events_dir)
        domain.pricing.populate_prices_cache(db, events_dir)
        assert self._moq(db, "C1525", "lcsc") == 10   # last moq-bearing lcsc row

    def test_two_packagings_null_the_moq(self, db, events_dir):
        """1 and 3000 have no useful midpoint, and last-row-wins would report
        the reel quantity for a part you can buy one of."""
        _seed_parts(db)
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "digikey", [],
            packagings=[
                {"name": "Cut Tape (CT)", "prices": [{"qty": 1, "price": 0.10}]},
                {"name": "Tape & Reel (TR)", "prices": [{"qty": 3000, "price": 0.04}]},
            ],
        )
        assert self._moq(db) is None

    def test_unknown_plus_named_packaging_nulls_the_moq(self, db, events_dir):
        """The upgrade path: legacy moq rows could have been any packaging, so
        a later named ladder does not make them comparable."""
        _seed_parts(db)
        _write_legacy(events_dir)
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "lcsc", [],
            packagings=[{"name": "Reel", "prices": [{"qty": 3000, "price": 0.004}]}],
        )
        assert self._moq(db, "C1525", "lcsc") is None

    def test_moqless_rows_do_not_null_the_moq(self, db, events_dir):
        """import/manual observations carry no moq, so they never influence the
        value and must not widen the packaging set that nulls it."""
        _seed_parts(db)
        domain.pricing.record_observations(events_dir, [
            # No moq, no packaging — an import row.
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.2,
             "source": "import"},
        ])
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "digikey", [],
            packagings=[{"name": "Cut Tape (CT)",
                         "prices": [{"qty": 10, "price": 0.09}]}],
        )
        assert self._moq(db) == 10

    def test_null_moq_surfaces_through_get_price_summary(self, db, events_dir):
        """The MCP `price_summary` tool passes this dict straight to a caller."""
        _seed_parts(db)
        domain.pricing.record_fetched_prices(
            db, events_dir, "C1525", "digikey", [],
            packagings=[
                {"name": "Cut Tape (CT)", "prices": [{"qty": 1, "price": 0.10}]},
                {"name": "Tape & Reel (TR)", "prices": [{"qty": 3000, "price": 0.04}]},
            ],
        )
        summary = domain.pricing.get_price_summary(db, events_dir, "C1525")
        assert "moq" in summary["digikey"]          # key present, not dropped
        assert summary["digikey"]["moq"] is None    # ...and honestly empty
        # The other aggregates still answer "what does this cost".
        assert summary["digikey"]["price_count"] == 2
