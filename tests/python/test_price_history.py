"""Tests for price_history module."""

import csv
import os

import pytest

import price_history


class TestRecordObservation:
    def test_creates_csv_with_header(self, events_dir):
        price_history.record_observations(events_dir, [
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
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.0074,
             "source": "import"},
        ])
        price_history.record_observations(events_dir, [
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
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01,
             "source": "import"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["timestamp"]  # not empty
        assert "T" in row["timestamp"]  # ISO format

    def test_optional_fields_default_empty(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.01,
             "source": "import"},
        ])
        csv_path = os.path.join(events_dir, "price_observations.csv")
        with open(csv_path, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["currency"] == ""
        assert row["moq"] == ""
        assert row["note"] == ""


class TestReadObservations:
    def test_read_all(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.010,
             "source": "import"},
        ])
        obs = price_history.read_observations(events_dir)
        assert len(obs) == 2

    def test_read_filtered_by_part(self, events_dir):
        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C9999", "distributor": "lcsc", "unit_price": 0.050,
             "source": "import"},
        ])
        obs = price_history.read_observations(events_dir, part_id="C1525")
        assert len(obs) == 1
        assert obs[0]["part_id"] == "C1525"

    def test_read_empty_returns_empty(self, events_dir):
        obs = price_history.read_observations(events_dir)
        assert obs == []


class TestPopulatePricesCache:
    def test_populate_aggregates_by_distributor(self, events_dir, tmp_path):
        import cache_db
        conn = cache_db.connect(str(tmp_path / "cache.db"))
        cache_db.create_schema(conn)
        conn.execute("INSERT INTO parts (part_id) VALUES ('C1525')")
        conn.execute("INSERT INTO stock (part_id, quantity) VALUES ('C1525', 100)")
        conn.commit()

        price_history.record_observations(events_dir, [
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.007,
             "source": "import"},
            {"part_id": "C1525", "distributor": "lcsc", "unit_price": 0.009,
             "source": "import"},
            {"part_id": "C1525", "distributor": "digikey", "unit_price": 0.012,
             "source": "import"},
        ])

        price_history.populate_prices_cache(conn, events_dir)

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
