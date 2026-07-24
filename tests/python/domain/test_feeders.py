"""Tests for domain.feeders — the loading-station feeder entity (register
tag -> feeder, bind feeder -> reel; entity-store pattern like part_registry)."""

from __future__ import annotations

import json
import os

import pytest

from domain import feeders


class TestLoadSave:
    def test_load_missing_file_returns_empty_store(self, tmp_path):
        store = feeders.load(str(tmp_path))
        assert store.feeders == {}
        assert store.dirty is False

    def test_save_load_roundtrip(self, tmp_path):
        store = feeders.load(str(tmp_path))
        feeders.register(store, "12", "strip-feeder")
        feeders.load_reel(store, "12", "C100000", 500, "2026-07-21T00:00:00+00:00",
                          tape_width_mm=8.0)
        feeders.save(str(tmp_path), store)

        path = os.path.join(str(tmp_path), "feeders.json")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["version"] == 1
        assert raw["feeders"]["12"]["feeder_type"] == "strip-feeder"

        store2 = feeders.load(str(tmp_path))
        assert store2.feeders == store.feeders
        assert store2.dirty is False


class TestRegisterLoadUnload:
    def test_register_creates_unloaded_feeder(self, tmp_path):
        store = feeders.load(str(tmp_path))
        record = feeders.register(store, "1", "strip-feeder")
        assert record == {
            "family": "apriltag_36h11",
            "feeder_type": "strip-feeder",
            "loaded": None,
        }
        assert feeders.get(store, "1") == record
        assert store.dirty is True

    def test_register_twice_fails(self, tmp_path):
        store = feeders.load(str(tmp_path))
        feeders.register(store, "1", "strip-feeder")
        with pytest.raises(ValueError):
            feeders.register(store, "1", "strip-feeder")

    def test_load_before_register_fails(self, tmp_path):
        store = feeders.load(str(tmp_path))
        with pytest.raises(KeyError):
            feeders.load_reel(store, "99", "C100000", 100, "2026-07-21T00:00:00+00:00")

    def test_unload_before_register_fails(self, tmp_path):
        store = feeders.load(str(tmp_path))
        with pytest.raises(KeyError):
            feeders.unload(store, "99")

    def test_load_then_unload_roundtrip(self, tmp_path):
        store = feeders.load(str(tmp_path))
        feeders.register(store, "1", "strip-feeder")
        loaded = feeders.load_reel(store, "1", "C100000", 500,
                                   "2026-07-21T00:00:00+00:00", tape_width_mm=8.0)
        assert loaded["loaded"] == {
            "part_key": "C100000", "qty": 500,
            "tape_width_mm": 8.0, "loaded_at": "2026-07-21T00:00:00+00:00",
        }
        unloaded = feeders.unload(store, "1")
        assert unloaded["loaded"] is None
        # Registration survives unload — only the loaded reel is cleared.
        assert unloaded["feeder_type"] == "strip-feeder"

    def test_list_all_returns_all_feeders(self, tmp_path):
        store = feeders.load(str(tmp_path))
        feeders.register(store, "1", "strip-feeder")
        feeders.register(store, "2", "tray-feeder")
        assert set(feeders.list_all(store).keys()) == {"1", "2"}

    def test_get_unregistered_returns_none(self, tmp_path):
        store = feeders.load(str(tmp_path))
        assert feeders.get(store, "no-such-tag") is None

    def test_state_persists_across_reload(self, tmp_path):
        store = feeders.load(str(tmp_path))
        feeders.register(store, "1", "strip-feeder")
        feeders.load_reel(store, "1", "C100000", 500, "2026-07-21T00:00:00+00:00")
        feeders.save(str(tmp_path), store)

        reloaded = feeders.load(str(tmp_path))
        record = feeders.get(reloaded, "1")
        assert record["loaded"]["part_key"] == "C100000"
        assert record["loaded"]["qty"] == 500
