"""The threshold snapshot store: append-only, undo-appends-a-copy, seed-on-empty.

The decisions these pin are the module docstring's (domain/threshold_snapshots.py):
snapshot 0 is derived from the shipped defaults and never persisted; every
write appends; undo never deletes; a corrupt store raises rather than reading
as "no history".
"""

from __future__ import annotations

import json

import pytest

from domain import threshold_snapshots as store
from dubis_errors import NotFoundError, NothingToUndoError

DEFAULTS = {"Diodes": 10, "Passives - Resistors": 4}
SECTIONS = frozenset({
    "Diodes", "Passives - Resistors", "Passives - Resistors > Chip Resistors",
    "Connectors", "LEDs", "ICs - Clock and Timing",
})


def _create(data_dir, values, *, mode="delta", created_by="cli", note="", defaults=DEFAULTS):
    return store.create(
        data_dir, defaults, values=values, mode=mode, created_by=created_by,
        note=note, known_sections=SECTIONS,
    )


# ── seed-on-empty ────────────────────────────────────────────────────────────


def test_fresh_store_has_snapshot_zero_and_writes_no_file(tmp_path):
    history = store.load(str(tmp_path), DEFAULTS)
    assert [s.id for s in history.snapshots] == [0]
    active = history.active
    assert active.id == 0 and active.active and active.seeded
    assert dict(active.values) == DEFAULTS
    assert active.created_by == "server"
    # Snapshot 0 is a build fact, not an event: no timestamp to count down to.
    assert active.created_at == ""
    assert not (tmp_path / store.FILENAME).exists(), "a read must not write the store"


def test_snapshot_zero_tracks_the_shipped_table(tmp_path):
    """Snapshot 0 IS the default profile — re-read with different defaults and
    it follows, because it is derived, not stored."""
    history = store.load(str(tmp_path), {"Diodes": 99})
    assert dict(history.active.values) == {"Diodes": 99}


# ── append ───────────────────────────────────────────────────────────────────


def test_delta_create_merges_onto_the_active_profile(tmp_path):
    history, new = _create(str(tmp_path), {"Diodes": 7, "LEDs": 3})
    assert new.id == 1 and new.active
    assert dict(new.values) == {"Diodes": 7, "Passives - Resistors": 4, "LEDs": 3}
    loaded = store.load(str(tmp_path), DEFAULTS)
    assert loaded.active.id == 1
    assert [s.id for s in loaded.snapshots] == [0, 1]


def test_delta_null_removes_a_category_so_it_falls_through(tmp_path):
    history, new = _create(str(tmp_path), {"Passives - Resistors": None})
    assert "Passives - Resistors" not in new.values
    # Resolution-wise it is back in snapshot 0's hands (same value here, but
    # the NAME is gone from the recorded state — that is what a removal means).
    assert "Passives - Resistors" not in json.loads(
        (tmp_path / store.FILENAME).read_text()
    )["snapshots"][0]["values"]


def test_full_create_records_the_profile_as_is(tmp_path):
    _history, new = _create(str(tmp_path), {"Diodes": 1}, mode="full")
    assert dict(new.values) == {"Diodes": 1}


def test_full_mode_refuses_nulls(tmp_path):
    with pytest.raises(ValueError, match="removals are only allowed"):
        _create(str(tmp_path), {"Diodes": None}, mode="full")


def test_every_write_appends_and_moves_active(tmp_path):
    _create(str(tmp_path), {"Diodes": 7})
    history, second = _create(str(tmp_path), {"Diodes": 8})
    assert second.id == 2 and second.active
    by_id = {s.id: s for s in history.snapshots}
    assert not by_id[1].active and not by_id[0].active
    # Append-only: snapshot 1 still holds the value it was created with.
    assert dict(by_id[1].values)["Diodes"] == 7


def test_the_file_never_holds_snapshot_zero(tmp_path):
    _create(str(tmp_path), {"Diodes": 7})
    body = json.loads((tmp_path / store.FILENAME).read_text())
    assert body["version"] == store.STORE_VERSION
    assert [entry["id"] for entry in body["snapshots"]] == [1]


def test_create_records_attribution_and_note(tmp_path):
    _history, new = _create(str(tmp_path), {"Diodes": 7}, created_by="cli@alice", note="tuned")
    assert new.created_by == "cli@alice" and new.note == "tuned"
    assert new.created_at.endswith("+00:00")


def test_anonymous_created_by_is_refused(tmp_path):
    with pytest.raises(ValueError, match="created_by"):
        _create(str(tmp_path), {"Diodes": 7}, created_by="  ")


def test_note_length_is_enforced_loudly(tmp_path):
    with pytest.raises(ValueError, match="at most"):
        _create(str(tmp_path), {"Diodes": 7}, note="x" * 301)


# ── value validation (loud on write) ─────────────────────────────────────────


def test_unknown_category_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a category this server serves"):
        _create(str(tmp_path), {"Not A Section": 5})


def test_negative_and_non_integer_thresholds_are_refused(tmp_path):
    for bad in (-1, "ten", 1.5, True):
        with pytest.raises(ValueError):
            _create(str(tmp_path), {"Diodes": bad})


def test_numeric_strings_are_coerced(tmp_path):
    _history, new = _create(str(tmp_path), {"Diodes": "12"})
    assert dict(new.values)["Diodes"] == 12


def test_unknown_mode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown mode"):
        _create(str(tmp_path), {"Diodes": 1}, mode="vibes")


# ── undo ─────────────────────────────────────────────────────────────────────


def test_undo_appends_a_copy_and_leaves_history_intact(tmp_path):
    _create(str(tmp_path), {"Diodes": 7})
    _create(str(tmp_path), {"Diodes": 8})
    history, undone = store.undo(str(tmp_path), DEFAULTS, created_by="cli@bob")
    assert undone.id == 3 and undone.active
    # Previous of snapshot 2 is snapshot 1 — its values, copied.
    assert dict(undone.values) == dict(history.snapshots[1].values)
    assert undone.note.startswith("undo: copy of snapshot 1")
    assert undone.created_by == "cli@bob"
    by_id = {s.id: s for s in history.snapshots}
    assert dict(by_id[2].values)["Diodes"] == 8, "the undone snapshot is untouched"
    assert not by_id[2].active


def test_undo_from_the_first_write_returns_to_the_shipped_profile(tmp_path):
    _create(str(tmp_path), {"Diodes": 7})
    history, undone = store.undo(str(tmp_path), DEFAULTS, created_by="cli")
    assert undone.id == 2
    assert dict(undone.values) == DEFAULTS  # a copy of snapshot 0
    assert history.active.id == 2


def test_undo_with_snapshot_zero_active_is_loud(tmp_path):
    with pytest.raises(NothingToUndoError, match="no earlier state"):
        store.undo(str(tmp_path), DEFAULTS, created_by="cli")


# ── activate ─────────────────────────────────────────────────────────────────


def test_activate_appends_a_copy_of_the_target(tmp_path):
    _create(str(tmp_path), {"Diodes": 7})
    _create(str(tmp_path), {"Diodes": 8})
    history, act = store.activate(str(tmp_path), DEFAULTS, 1, created_by="cli")
    assert act.id == 3 and act.active
    assert dict(act.values)["Diodes"] == 7
    assert act.note.startswith("activate snapshot 1")


def test_activate_unknown_id_is_not_found(tmp_path):
    _create(str(tmp_path), {"Diodes": 7})
    with pytest.raises(NotFoundError, match="no threshold snapshot 9"):
        store.activate(str(tmp_path), DEFAULTS, 9, created_by="cli")


def test_activate_the_active_snapshot_is_refused(tmp_path):
    _create(str(tmp_path), {"Diodes": 7})
    with pytest.raises(ValueError, match="already active"):
        store.activate(str(tmp_path), DEFAULTS, 1, created_by="cli")


# ── corrupt store reads loud, never as "empty" ───────────────────────────────


def _write_raw(tmp_path, text):
    (tmp_path / store.FILENAME).write_text(text, encoding="utf-8")


def test_invalid_json_names_the_path(tmp_path):
    _write_raw(tmp_path, "{not json")
    with pytest.raises(ValueError, match=store.FILENAME):
        store.load(str(tmp_path), DEFAULTS)


def test_unknown_version_is_refused(tmp_path):
    _write_raw(tmp_path, json.dumps({"version": 99, "snapshots": []}))
    with pytest.raises(ValueError, match="version"):
        store.load(str(tmp_path), DEFAULTS)


def test_two_active_snapshots_are_refused(tmp_path):
    entry = {
        "id": 1, "values": {}, "created_by": "cli", "created_at": "x",
        "note": "", "active": True,
    }
    _write_raw(tmp_path, json.dumps({"version": 1, "snapshots": [entry, dict(entry, id=2)]}))
    with pytest.raises(ValueError, match="active"):
        store.load(str(tmp_path), DEFAULTS)


def test_non_ascending_ids_are_refused(tmp_path):
    entry = {
        "id": 5, "values": {}, "created_by": "cli", "created_at": "x",
        "note": "", "active": True,
    }
    _write_raw(tmp_path, json.dumps({"version": 1, "snapshots": [dict(entry, id=6), entry]}))
    with pytest.raises(ValueError, match="ascend"):
        store.load(str(tmp_path), DEFAULTS)


# ── resolved_profile (the server half of the spec) ───────────────────────────


def test_resolved_profile_layers_active_over_zero_over_derivation_over_base(tmp_path):
    history, _new = _create(str(tmp_path), {"Diodes": 7, "ICs - Clock and Timing": 400})
    resolved = store.resolved_profile(history.active, DEFAULTS, [
        "Diodes",                                   # active names it
        "Passives - Resistors",                     # snapshot 0 names it
        "Connectors",                               # neither -> shipped default? no: not in DEFAULTS -> derivation (20)
        "LEDs",                                     # neither, no family -> base 50
        "ICs - Clock and Timing",                   # active names it, over its derived 100
    ])
    assert resolved["Diodes"] == 7
    assert resolved["Passives - Resistors"] == 4
    assert resolved["Connectors"] == 20
    assert resolved["LEDs"] == 50
    assert resolved["ICs - Clock and Timing"] == 400
