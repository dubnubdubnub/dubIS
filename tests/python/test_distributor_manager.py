"""Tests for DistributorManager — inference and client initialisation."""

from __future__ import annotations

import sqlite3

import pytest

from distributor_manager import DistributorManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(db_rows: dict[str, dict] | None = None) -> DistributorManager:
    """Create a DistributorManager backed by an in-memory SQLite database.

    db_rows maps part_id -> {digikey, pololu, mouser} column values.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE parts (part_id TEXT PRIMARY KEY, digikey TEXT, pololu TEXT, mouser TEXT)"
    )
    if db_rows:
        for part_id, cols in db_rows.items():
            conn.execute(
                "INSERT INTO parts (part_id, digikey, pololu, mouser) VALUES (?, ?, ?, ?)",
                (
                    part_id,
                    cols.get("digikey", ""),
                    cols.get("pololu", ""),
                    cols.get("mouser", ""),
                ),
            )
    conn.commit()

    def get_cache():
        return conn

    return DistributorManager(base_dir="/tmp", get_cache=get_cache)


# ---------------------------------------------------------------------------
# infer_distributor (static method — tested via class and instance)
# ---------------------------------------------------------------------------

class TestInferDistributor:
    def test_lcsc_wins_when_populated(self):
        row = {"LCSC Part Number": "C12345", "Digikey Part Number": "DK-123"}
        assert DistributorManager.infer_distributor(row) == "lcsc"

    def test_digikey_when_no_lcsc(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": "296-1234-1-ND"}
        assert DistributorManager.infer_distributor(row) == "digikey"

    def test_mouser(self):
        row = {"Mouser Part Number": "512-LM358N"}
        assert DistributorManager.infer_distributor(row) == "mouser"

    def test_pololu(self):
        row = {"Pololu Part Number": "2135"}
        assert DistributorManager.infer_distributor(row) == "pololu"

    def test_unknown_when_all_empty(self):
        row = {"LCSC Part Number": "", "Digikey Part Number": ""}
        assert DistributorManager.infer_distributor(row) == "unknown"

    def test_unknown_when_missing_keys(self):
        assert DistributorManager.infer_distributor({}) == "unknown"

    def test_whitespace_only_is_empty(self):
        row = {"LCSC Part Number": "   "}
        assert DistributorManager.infer_distributor(row) == "unknown"

    def test_none_value_treated_as_empty(self):
        row = {"LCSC Part Number": None, "Digikey Part Number": "DK-999"}
        assert DistributorManager.infer_distributor(row) == "digikey"

    def test_accessible_as_instance_method(self):
        mgr = _make_manager()
        row = {"LCSC Part Number": "C9999"}
        assert mgr.infer_distributor(row) == "lcsc"


# ---------------------------------------------------------------------------
# infer_distributor_for_key
# ---------------------------------------------------------------------------

class TestInferDistributorForKey:
    def test_lcsc_pattern_c_digits(self):
        mgr = _make_manager()
        assert mgr.infer_distributor_for_key("C12345") == "lcsc"

    def test_lcsc_pattern_lowercase(self):
        """The check is case-insensitive for the leading letter."""
        mgr = _make_manager()
        assert mgr.infer_distributor_for_key("c99999") == "lcsc"

    def test_lcsc_pattern_too_short_falls_through(self):
        """'C' alone is not a valid LCSC key pattern (no digits)."""
        mgr = _make_manager()
        result = mgr.infer_distributor_for_key("C")
        # No DB entry -> unknown
        assert result == "unknown"

    def test_lcsc_pattern_c_with_non_digits_falls_through(self):
        """C + non-digits should not be classified as LCSC."""
        mgr = _make_manager()
        result = mgr.infer_distributor_for_key("CDEF123")
        assert result == "unknown"

    def test_digikey_from_cache(self):
        mgr = _make_manager({"MPN-001": {"digikey": "296-1234-1-ND"}})
        assert mgr.infer_distributor_for_key("MPN-001") == "digikey"

    def test_pololu_from_cache(self):
        mgr = _make_manager({"MPN-002": {"pololu": "2135"}})
        assert mgr.infer_distributor_for_key("MPN-002") == "pololu"

    def test_mouser_from_cache(self):
        mgr = _make_manager({"MPN-003": {"mouser": "512-LM358N"}})
        assert mgr.infer_distributor_for_key("MPN-003") == "mouser"

    def test_digikey_beats_pololu(self):
        """When both digikey and pololu are populated, digikey takes priority."""
        mgr = _make_manager({"MPN-004": {"digikey": "DK-X", "pololu": "P-X"}})
        assert mgr.infer_distributor_for_key("MPN-004") == "digikey"

    def test_unknown_when_not_in_cache(self):
        mgr = _make_manager()
        assert mgr.infer_distributor_for_key("UNKNOWN-PART") == "unknown"

    def test_unknown_when_all_cols_empty(self):
        mgr = _make_manager({"MPN-005": {}})
        assert mgr.infer_distributor_for_key("MPN-005") == "unknown"
