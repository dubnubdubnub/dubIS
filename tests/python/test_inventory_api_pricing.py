"""Tests for InventoryApi — pricing, price history, and distributor inference."""

import os

import pytest

from distributor_manager import DistributorManager
from tests.python.helpers import make_part as _make_part
from tests.python.helpers import write_ledger as _write_ledger


class TestUpdatePartPrice:
    def test_unit_price_auto_ext(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.update_part_price("C100000", unit_price=0.05)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["unit_price"] == pytest.approx(0.05)
        assert part["ext_price"] == pytest.approx(0.50)

    def test_ext_price_auto_unit(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.update_part_price("C100000", ext_price=1.00)
        part = next(r for r in result if r["lcsc"] == "C100000")
        assert part["unit_price"] == pytest.approx(0.10)
        assert part["ext_price"] == pytest.approx(1.00)

    def test_missing_part_creates_row(self, api):
        _write_ledger(api, [_make_part(lcsc="C100000", qty=10)])
        result = api.update_part_price("C999999", unit_price=0.01)
        # Should not error — creates a new row
        assert isinstance(result, list)
        assert any(r["lcsc"] == "C999999" for r in result), "New part C999999 should appear in result"

    def test_no_ledger_error(self, api):
        with pytest.raises(ValueError, match="No purchase ledger found"):
            api.update_part_price("C100000", unit_price=0.01)


class TestInferDistributor:
    def test_lcsc(self):
        row = {"LCSC Part Number": "C1525", "Digikey Part Number": "", "Mouser Part Number": "", "Pololu Part Number": ""}
        assert DistributorManager.infer_distributor(row) == "lcsc"

    def test_digikey(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "DK-123", "Mouser Part Number": "", "Pololu Part Number": ""}
        assert DistributorManager.infer_distributor(row) == "digikey"

    def test_mouser(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "", "Mouser Part Number": "M-123", "Pololu Part Number": ""}
        assert DistributorManager.infer_distributor(row) == "mouser"

    def test_pololu(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "", "Mouser Part Number": "", "Pololu Part Number": "1992"}
        assert DistributorManager.infer_distributor(row) == "pololu"

    def test_unknown(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "", "Mouser Part Number": "", "Pololu Part Number": ""}
        assert DistributorManager.infer_distributor(row) == "unknown"


class TestPriceHistoryOnImport:
    def test_import_records_price_observations(self, api, tmp_path):
        import csv as csv_mod
        rows = [
            {"LCSC Part Number": "C1525", "Manufacture Part Number": "",
             "Digikey Part Number": "", "Pololu Part Number": "",
             "Mouser Part Number": "",
             "Manufacturer": "", "Quantity": "100",
             "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
             "Description": "", "Package": "", "RoHS": "",
             "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
        ]
        api.import_purchases(rows)
        events_dir = os.path.join(api.base_dir, "events")
        obs_path = os.path.join(events_dir, "price_observations.csv")
        assert os.path.exists(obs_path)
        with open(obs_path, newline="", encoding="utf-8") as f:
            obs = list(csv_mod.DictReader(f))
        assert len(obs) == 1
        assert obs[0]["part_id"] == "C1525"
        assert obs[0]["distributor"] == "lcsc"
        assert float(obs[0]["unit_price"]) == pytest.approx(0.0074)
        assert obs[0]["source"] == "import"


class TestPriceHistoryOnManualEdit:
    def _setup_part(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])

    def test_price_update_records_observation(self, api, tmp_path):
        import csv as csv_mod
        self._setup_part(api)
        api.update_part_price("C1525", unit_price=0.01)
        events_dir = os.path.join(api.base_dir, "events")
        obs_path = os.path.join(events_dir, "price_observations.csv")
        with open(obs_path, newline="", encoding="utf-8") as f:
            obs = list(csv_mod.DictReader(f))
        manual = [o for o in obs if o["source"] == "manual"]
        assert len(manual) == 1
        assert manual[0]["part_id"] == "C1525"
        assert float(manual[0]["unit_price"]) == pytest.approx(0.01)


class TestRecordFetchedPrices:
    def _setup_part(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])

    def test_record_fetched_prices(self, api):
        self._setup_part(api)
        api.record_fetched_prices("C1525", "lcsc", [
            {"qty": 1, "price": 0.0080},
            {"qty": 10, "price": 0.0070},
        ])
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary
        assert summary["lcsc"]["latest_unit_price"] == pytest.approx(0.0070)
        assert summary["lcsc"]["price_count"] >= 2

    def test_get_price_summary_empty(self, api):
        summary = api.get_price_summary("NONEXISTENT")
        assert summary == {}

    def test_get_price_summary_multiple_distributors(self, api):
        self._setup_part(api)
        api.record_fetched_prices("C1525", "digikey", [
            {"qty": 1, "price": 0.012},
        ])
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary
        assert "digikey" in summary


class TestPricesFKResolution:
    """Test that distributor-specific PNs resolve to inventory part_ids."""

    def _setup_part_with_digikey(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "DRV8316C",
            "Digikey Part Number": "296-DRV8316CRRGFRCT-ND",
            "Pololu Part Number": "", "Mouser Part Number": "",
            "Manufacturer": "TI", "Quantity": "10",
            "Unit Price($)": "2.50", "Ext.Price($)": "25.00",
            "Description": "Motor driver", "Package": "QFN",
            "RoHS": "Yes", "Customer NO.": "",
            "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])

    def test_record_fetched_prices_with_digikey_pn(self, api):
        """record_fetched_prices resolves Digikey PN to inventory part_id."""
        self._setup_part_with_digikey(api)
        # Pass the Digikey PN, not the inventory part_id (C1525)
        api.record_fetched_prices("296-DRV8316CRRGFRCT-ND", "digikey", [
            {"qty": 1, "price": 2.80},
        ])
        # Should be queryable by either key
        summary = api.get_price_summary("C1525")
        assert "digikey" in summary
        assert summary["digikey"]["latest_unit_price"] == pytest.approx(2.80)

    def test_get_price_summary_with_digikey_pn(self, api):
        """get_price_summary resolves Digikey PN to inventory part_id."""
        self._setup_part_with_digikey(api)
        api.record_fetched_prices("C1525", "digikey", [
            {"qty": 1, "price": 2.80},
        ])
        # Query using the Digikey PN
        summary = api.get_price_summary("296-DRV8316CRRGFRCT-ND")
        assert "digikey" in summary

    def test_record_fetched_prices_with_mpn(self, api):
        """record_fetched_prices resolves MPN to inventory part_id."""
        self._setup_part_with_digikey(api)
        api.record_fetched_prices("DRV8316C", "mouser", [
            {"qty": 1, "price": 2.70},
        ])
        summary = api.get_price_summary("C1525")
        assert "mouser" in summary

    def test_record_fetched_prices_unknown_part_skipped(self, api):
        """record_fetched_prices silently skips unknown part keys."""
        self._setup_part_with_digikey(api)
        api.record_fetched_prices("TOTALLY-UNKNOWN-PN", "digikey", [
            {"qty": 1, "price": 1.00},
        ])
        # No crash, no data written
        summary = api.get_price_summary("C1525")
        assert "digikey" not in summary

    def test_populate_prices_cache_resolves_distributor_pn(self, api):
        """populate_prices_cache resolves distributor PNs in historical data."""
        import domain.pricing
        self._setup_part_with_digikey(api)
        # Write observation with Digikey PN directly to the log
        events_dir = os.path.join(api.base_dir, "events")
        os.makedirs(events_dir, exist_ok=True)
        domain.pricing.record_observations(events_dir, [{
            "part_id": "296-DRV8316CRRGFRCT-ND",
            "distributor": "digikey",
            "unit_price": 2.80,
            "source": "live_fetch",
        }])
        # Rebuild cache — should resolve the Digikey PN
        conn = api._get_cache()
        domain.pricing.populate_prices_cache(conn, events_dir)
        rows = conn.execute(
            "SELECT * FROM prices WHERE part_id = ? AND distributor = ?",
            ("C1525", "digikey"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["latest_unit_price"] == pytest.approx(2.80)


class TestPricesCacheOnRebuild:
    def test_rebuild_populates_prices_cache(self, api):
        api.import_purchases([{
            "LCSC Part Number": "C1525", "Manufacture Part Number": "",
            "Digikey Part Number": "", "Pololu Part Number": "",
            "Mouser Part Number": "",
            "Manufacturer": "", "Quantity": "100",
            "Unit Price($)": "0.0074", "Ext.Price($)": "0.74",
            "Description": "", "Package": "", "RoHS": "",
            "Customer NO.": "", "Estimated lead time (business days)": "",
            "Date Code / Lot No.": "",
        }])
        summary = api.get_price_summary("C1525")
        assert "lcsc" in summary
        assert summary["lcsc"]["latest_unit_price"] == pytest.approx(0.0074)
