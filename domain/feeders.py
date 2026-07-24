"""Feeder identity + reel binding — the dubIS loading-station entity.

The loading station (a workstation separate from the PnP machine — see
docs/plans/phase3a-openpnp-bridge-design.md's "Feeder identity" section) lets
an operator print AprilTag 36h11 markers, stick one on each physical feeder,
register the tag -> feeder in dubIS, then bind a picked-in-dubIS part (a
reel) to that feeder. There is no barcode scanning in this flow — the part is
selected in dubIS's own UI/API, not read off a physical label.

data/feeders.json is the durable store, entity-store pattern (mirrors
domain/part_registry.py):
    {"version": 1, "feeders": {"<tag_id>": {
        "family": "apriltag_36h11",
        "feeder_type": "<str>",
        "loaded": {"part_key": "<canonical>", "qty": <int>,
                   "tape_width_mm": <num|null>, "loaded_at": "<iso>"} | null
    }}}

feeders.json is USER RUNTIME STATE (like data/preferences.json) — never
commit real content; a missing file is treated as an empty store (created on
first write) exactly like part_registry.load's self-healing behavior.
"""

from __future__ import annotations

import json
import os

import csv_io

_JSON_FILE = "feeders.json"

DEFAULT_FAMILY = "apriltag_36h11"


class FeederStore:
    def __init__(self, feeders: dict[str, dict] | None = None):
        self.feeders: dict[str, dict] = feeders or {}
        self.dirty = False


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def load(data_dir: str) -> FeederStore:
    """Load the feeder store; missing file -> empty store (self-healing)."""
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return FeederStore()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return FeederStore(data.get("feeders", {}))


def save(data_dir: str, store: FeederStore) -> None:
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps({"version": 1, "feeders": store.feeders},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    store.dirty = False


def get(store: FeederStore, tag_id: str) -> dict | None:
    """The feeder record for *tag_id*, or None if unregistered."""
    return store.feeders.get(str(tag_id))


def list_all(store: FeederStore) -> dict[str, dict]:
    """All feeders, keyed by tag_id."""
    return dict(store.feeders)


def register(store: FeederStore, tag_id: str, feeder_type: str,
             family: str = DEFAULT_FAMILY) -> dict:
    """Register a new tag -> feeder binding.

    Raises ValueError if tag_id is already registered — re-registering an
    in-use tag would silently orphan whatever reel is currently bound to it;
    unload() + a fresh register() (or just re-loading a new reel) is the
    explicit path for reassigning a physical feeder slot.
    """
    tag_id = str(tag_id)
    if tag_id in store.feeders:
        raise ValueError(f"Feeder tag {tag_id!r} is already registered")
    store.feeders[tag_id] = {
        "family": family,
        "feeder_type": feeder_type,
        "loaded": None,
    }
    store.dirty = True
    return store.feeders[tag_id]


def load_reel(store: FeederStore, tag_id: str, part_key: str, qty: int,
              loaded_at: str, tape_width_mm: float | None = None) -> dict:
    """Bind a reel (canonical part_key + qty) to a registered feeder.

    Raises KeyError if the feeder isn't registered yet — a physical AprilTag
    must be registered before any reel can be bound to it.
    """
    tag_id = str(tag_id)
    if tag_id not in store.feeders:
        raise KeyError(f"Feeder tag {tag_id!r} is not registered")
    store.feeders[tag_id]["loaded"] = {
        "part_key": part_key,
        "qty": qty,
        "tape_width_mm": tape_width_mm,
        "loaded_at": loaded_at,
    }
    store.dirty = True
    return store.feeders[tag_id]


def unload(store: FeederStore, tag_id: str) -> dict:
    """Clear the loaded reel from a feeder (feeder registration stays intact).

    Raises KeyError if the feeder isn't registered.
    """
    tag_id = str(tag_id)
    if tag_id not in store.feeders:
        raise KeyError(f"Feeder tag {tag_id!r} is not registered")
    store.feeders[tag_id]["loaded"] = None
    store.dirty = True
    return store.feeders[tag_id]
