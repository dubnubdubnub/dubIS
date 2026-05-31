"""Unit tests for distributor_fixtures — pure, deterministic, no network/I/O."""

from datetime import datetime

import pytest

import distributor_fixtures as df

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 31, 12, 0, 0)
NOW_ISO = "2026-05-31T12:00:00"


def _days_before(n: int) -> str:
    """ISO timestamp for exactly *n* days before NOW (same time-of-day)."""
    from datetime import timedelta

    return (NOW - timedelta(days=n)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# block_captured_at
# ---------------------------------------------------------------------------


class TestBlockCapturedAt:
    def test_per_block_timestamp_returned(self):
        fixture = {
            "captured_at": "2026-01-01T00:00:00",
            "lcsc": {"captured_at": "2026-03-15T10:00:00", "parts": {}},
        }
        assert df.block_captured_at(fixture, "lcsc") == "2026-03-15T10:00:00"

    def test_fallback_to_legacy_top_level_when_block_has_no_ts(self):
        fixture = {
            "captured_at": "2026-01-01T00:00:00",
            "lcsc": {"parts": {}},  # no captured_at in block
        }
        assert df.block_captured_at(fixture, "lcsc") == "2026-01-01T00:00:00"

    def test_fallback_to_legacy_when_distributor_absent(self):
        fixture = {"captured_at": "2026-01-01T00:00:00"}
        assert df.block_captured_at(fixture, "mouser") == "2026-01-01T00:00:00"

    def test_none_when_neither_present(self):
        fixture = {"lcsc": {"parts": {}}}
        assert df.block_captured_at(fixture, "lcsc") is None

    def test_none_when_both_absent(self):
        fixture = {}
        assert df.block_captured_at(fixture, "digikey") is None

    def test_non_dict_block_falls_back_to_legacy(self):
        # block is a string (corrupt fixture) — should not crash
        fixture = {"captured_at": "2026-02-01T00:00:00", "lcsc": "oops"}
        assert df.block_captured_at(fixture, "lcsc") == "2026-02-01T00:00:00"

    def test_non_dict_block_no_legacy_returns_none(self):
        fixture = {"lcsc": 42}
        assert df.block_captured_at(fixture, "lcsc") is None

    def test_per_block_takes_priority_over_legacy(self):
        # per-block is newer; legacy must NOT win
        fixture = {
            "captured_at": "2026-01-01T00:00:00",
            "pololu": {"captured_at": "2026-04-01T00:00:00", "parts": {}},
        }
        assert df.block_captured_at(fixture, "pololu") == "2026-04-01T00:00:00"


# ---------------------------------------------------------------------------
# stale_distributors
# ---------------------------------------------------------------------------


class TestStaleDistributors:
    def test_recent_block_not_stale(self):
        # 1 day old, max 30 — should be fresh
        fixture = {"lcsc": {"captured_at": _days_before(1), "parts": {}}}
        result = df.stale_distributors(fixture, ["lcsc"], NOW, max_age_days=30)
        assert result == set()

    def test_old_block_is_stale(self):
        # 60 days old, max 30
        fixture = {"lcsc": {"captured_at": _days_before(60), "parts": {}}}
        result = df.stale_distributors(fixture, ["lcsc"], NOW, max_age_days=30)
        assert result == {"lcsc"}

    def test_missing_timestamp_is_stale(self):
        fixture = {"lcsc": {"parts": {}}}  # no captured_at anywhere
        result = df.stale_distributors(fixture, ["lcsc"], NOW)
        assert result == {"lcsc"}

    def test_unparseable_timestamp_is_stale(self):
        fixture = {"lcsc": {"captured_at": "garbage"}}
        result = df.stale_distributors(fixture, ["lcsc"], NOW)
        assert result == {"lcsc"}

    def test_legacy_only_old_top_level_makes_all_scoped_stale(self):
        fixture = {"captured_at": _days_before(60)}  # old legacy, no per-block
        result = df.stale_distributors(fixture, ["lcsc", "pololu"], NOW, max_age_days=30)
        assert result == {"lcsc", "pololu"}

    def test_scope_filtering_excludes_unscoped_stale(self):
        # digikey is stale but NOT in scope — must not appear
        fixture = {
            "lcsc": {"captured_at": _days_before(1)},
            "digikey": {"captured_at": _days_before(60)},
        }
        result = df.stale_distributors(fixture, ["lcsc"], NOW, max_age_days=30)
        assert result == set()

    def test_scope_filtering_only_returns_names_in_scope(self):
        fixture = {
            "lcsc": {"captured_at": _days_before(60)},
            "digikey": {"captured_at": _days_before(60)},
            "mouser": {"captured_at": _days_before(60)},
        }
        # only lcsc and digikey in scope
        result = df.stale_distributors(fixture, ["lcsc", "digikey"], NOW, max_age_days=30)
        assert result == {"lcsc", "digikey"}

    def test_boundary_exactly_30_days_not_stale(self):
        # (now - captured).days == 30 → NOT stale (uses strict >)
        fixture = {"lcsc": {"captured_at": _days_before(30)}}
        result = df.stale_distributors(fixture, ["lcsc"], NOW, max_age_days=30)
        assert result == set()

    def test_boundary_31_days_is_stale(self):
        # 31 days > 30 → stale
        fixture = {"lcsc": {"captured_at": _days_before(31)}}
        result = df.stale_distributors(fixture, ["lcsc"], NOW, max_age_days=30)
        assert result == {"lcsc"}

    def test_empty_scope_returns_empty(self):
        fixture = {"lcsc": {"captured_at": _days_before(60)}}
        result = df.stale_distributors(fixture, [], NOW, max_age_days=30)
        assert result == set()

    def test_none_block_captured_at_is_stale(self):
        # distributor key absent entirely
        fixture = {}
        result = df.stale_distributors(fixture, ["digikey"], NOW)
        assert result == {"digikey"}


# ---------------------------------------------------------------------------
# merge_capture
# ---------------------------------------------------------------------------


class TestMergeCapture:
    def test_merged_blocks_get_now_timestamp(self):
        existing = {"captured_at": "2026-01-01T00:00:00", "lcsc": {"parts": {"C1": {}}}}
        new_blocks = {"lcsc": {"parts": {"C1": {}, "C2": {}}}}
        result = df.merge_capture(existing, new_blocks, NOW)
        assert result["lcsc"]["captured_at"] == NOW_ISO

    def test_untouched_distributor_preserved_identically(self):
        digikey_block = {"captured_at": "2026-02-01T00:00:00", "parts": {"DK1": {}}}
        existing = {
            "captured_at": "2026-01-01T00:00:00",
            "lcsc": {"parts": {}},
            "digikey": digikey_block,
        }
        new_blocks = {"lcsc": {"parts": {"C1": {}}}}
        result = df.merge_capture(existing, new_blocks, NOW)
        # digikey block preserved byte-identically
        assert result["digikey"] == digikey_block

    def test_top_level_captured_at_updated(self):
        existing = {"captured_at": "2026-01-01T00:00:00"}
        result = df.merge_capture(existing, {}, NOW)
        assert result["captured_at"] == NOW_ISO

    def test_existing_not_mutated(self):
        existing = {
            "captured_at": "2026-01-01T00:00:00",
            "lcsc": {"parts": {"C1": {}}},
        }
        existing_copy = {
            "captured_at": "2026-01-01T00:00:00",
            "lcsc": {"parts": {"C1": {}}},
        }
        df.merge_capture(existing, {"lcsc": {"parts": {"C1": {}, "C2": {}}}}, NOW)
        assert existing == existing_copy

    def test_new_blocks_not_mutated(self):
        existing = {"captured_at": "2026-01-01T00:00:00"}
        new_blocks = {"lcsc": {"parts": {"C1": {}}}}
        new_blocks_copy = {"lcsc": {"parts": {"C1": {}}}}
        df.merge_capture(existing, new_blocks, NOW)
        assert new_blocks == new_blocks_copy

    def test_multiple_blocks_merged(self):
        existing = {"captured_at": "2026-01-01T00:00:00"}
        new_blocks = {
            "lcsc": {"parts": {"C1": {}}},
            "pololu": {"parts": {"P1": {}}},
        }
        result = df.merge_capture(existing, new_blocks, NOW)
        assert result["lcsc"]["captured_at"] == NOW_ISO
        assert result["pololu"]["captured_at"] == NOW_ISO

    def test_new_distributor_added(self):
        # mouser did not exist in existing
        existing = {"captured_at": "2026-01-01T00:00:00"}
        new_blocks = {"mouser": {"parts": {"M1": {}}}}
        result = df.merge_capture(existing, new_blocks, NOW)
        assert "mouser" in result
        assert result["mouser"]["captured_at"] == NOW_ISO

    def test_merged_block_content_preserved(self):
        existing = {"captured_at": "2026-01-01T00:00:00"}
        new_blocks = {"lcsc": {"parts": {"C1": {"stock": 100}}, "errors": {}}}
        result = df.merge_capture(existing, new_blocks, NOW)
        assert result["lcsc"]["parts"] == {"C1": {"stock": 100}}
        assert result["lcsc"]["errors"] == {}

    def test_returns_new_dict_not_existing(self):
        existing = {"captured_at": "2026-01-01T00:00:00"}
        result = df.merge_capture(existing, {}, NOW)
        assert result is not existing

    def test_iso_format_seconds_precision(self):
        # Verify timespec='seconds' — no microseconds
        ts = datetime(2026, 5, 31, 12, 0, 0, 123456)
        existing = {"captured_at": "2026-01-01T00:00:00"}
        result = df.merge_capture(existing, {"lcsc": {}}, ts)
        assert result["captured_at"] == "2026-05-31T12:00:00"
        assert result["lcsc"]["captured_at"] == "2026-05-31T12:00:00"
