import json
import os

from inventory_mirror import SnapshotStore


def test_update_then_get(tmp_path):
    store = SnapshotStore(str(tmp_path / "snap.json"))
    store.update({"inventory": [{"lcsc": "C1"}], "pushed_at": "T", "token": "secret"}, received_at="R")
    snap = store.get()
    assert snap["inventory"] == [{"lcsc": "C1"}]
    assert snap["received_at"] == "R"
    assert "token" not in snap  # token must never be persisted/served


def test_update_persists_atomically(tmp_path):
    p = tmp_path / "snap.json"
    SnapshotStore(str(p)).update({"inventory": [], "pushed_at": "T"}, received_at="R")
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["pushed_at"] == "T"
    assert not any(f.endswith(".tmp") for f in os.listdir(tmp_path))  # temp cleaned up


def test_load_restores_previous_snapshot(tmp_path):
    p = str(tmp_path / "snap.json")
    SnapshotStore(p).update({"inventory": [{"lcsc": "X"}], "pushed_at": "T"}, received_at="R")
    fresh = SnapshotStore(p)
    fresh.load()
    assert fresh.get()["inventory"] == [{"lcsc": "X"}]


def test_load_tolerates_missing_file(tmp_path):
    store = SnapshotStore(str(tmp_path / "nope.json"))
    store.load()
    assert store.get() is None


def test_load_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "snap.json"
    p.write_text("{ not json", encoding="utf-8")
    store = SnapshotStore(str(p))
    store.load()  # must not raise
    assert store.get() is None
