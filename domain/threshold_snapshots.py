"""The server's threshold profile as an append-only snapshot history.

One profile answers "what thresholds does THIS server say"; this module is
where a server keeps its answer across restarts. The decisions baked in here
are Isaac's, not negotiable at the code level:

* **Snapshot 0 IS the default profile.** It is not a stored entity — it is
  always synthesized from the shipped ``DEFAULT_THRESHOLDS`` the caller passes
  in, so ``GET /v1/meta``'s ``default_thresholds`` and snapshot 0 can never
  disagree, and a fresh server has a complete profile with no migration and
  no seeded file. A server upgrade that moves the shipped table moves
  snapshot 0 with it, which is exactly right: snapshot 0 is "what this build
  ships", not an event in history.
* **Append-only.** Every write creates a new snapshot; nothing is ever
  deleted or edited in place. ``undo`` appends a *copy* of the previous
  snapshot and makes that active — history records the undo as its own event
  rather than erasing the undone one.
* **A snapshot records a profile state, not a diff.** Its ``values`` are the
  sparse per-category table that profile stands for (sparse the same way
  ``DEFAULT_THRESHOLDS`` is: only the categories it names; " > " parent
  inheritance applies at resolution, see domain/thresholds.py). A PUT in
  ``delta`` mode merges its changes onto the active snapshot's values to
  produce the new state; ``full`` mode takes the given profile as-is. A
  delta value of ``None`` removes a category from the new state — which
  means "stop naming it", so that category falls through to snapshot 0 and
  derivation again.

On-disk shape — ``<data_dir>/threshold_snapshots.json``, written atomically
(temp-file + ``os.replace``, the ``csv_io.atomic_write_text`` discipline)::

    {
      "version": 1,
      "snapshots": [
        {
          "id": 1,
          "values": {"Diodes": 7, ...},
          "created_by": "cli@alice",
          "created_at": "2026-10-14T09:12:33+00:00",
          "note": "tuned diodes after the reel purge",
          "active": true
        }
      ]
    }

Snapshot 0 is deliberately NOT in the file: it is derived, never persisted
(see above). The file therefore holds snapshots 1..n in append order, exactly
one of them ``active``; no file (or an empty list) means snapshot 0 is active.
Because it is runtime-written user data, the store must never be bundled into
the container image or the PyInstaller bundle — there is nothing to bundle:
the file is created by the first write in whatever the server's data dir is.

Attribution: ``created_by`` is the composed ``source@identity`` every other
mutation carries (``server/auth.stamp_source`` at the route); snapshot 0's is
the constant ``"server"`` with an empty ``created_at`` — shipped defaults are
a build fact, not an event, and inventing a timestamp for them would let the
UI count down to a moment that never happened.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

import csv_io
from domain import thresholds
from dubis_errors import NotFoundError, NothingToUndoError

#: The store's file name inside the server's data dir.
FILENAME = "threshold_snapshots.json"

#: Bumped only on a breaking change to the file shape; a reader that meets a
#: newer version refuses loudly rather than mis-reading it.
STORE_VERSION = 1

SNAPSHOT_ZERO_ID = 0
SNAPSHOT_ZERO_CREATED_BY = "server"
SNAPSHOT_ZERO_NOTE = "Shipped defaults — snapshot 0 is the default profile itself."

MODE_DELTA = "delta"
MODE_FULL = "full"
MODES = (MODE_DELTA, MODE_FULL)

_MAX_NOTE_CHARS = 300

# Read-modify-write serialised per process: two concurrent PUTs must not both
# read snapshot N and both append an N+1 (last writer silently wins). Cross-
# process there is exactly one writer per data dir — the `.dubis_lock` owner —
# so a process-local lock is the whole story.
_write_lock = threading.Lock()


@dataclass(frozen=True)
class Snapshot:
    """One recorded profile state."""

    id: int
    values: Mapping[str, int]
    created_by: str
    created_at: str
    note: str
    active: bool
    #: True for the synthesized snapshot 0 — the shipped defaults, which no
    #: file ever recorded. Callers may show it, never rewrite it.
    seeded: bool = field(default=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "values": dict(self.values),
            "created_by": self.created_by,
            "created_at": self.created_at,
            "note": self.note,
            "active": self.active,
            "seeded": self.seeded,
        }


@dataclass(frozen=True)
class History:
    """Snapshot 0 plus everything appended after it, in append order."""

    snapshots: tuple[Snapshot, ...]

    @property
    def active(self) -> Snapshot:
        for snapshot in self.snapshots:
            if snapshot.active:
                return snapshot
        # Unreachable through load(): exactly-one-active is validated there.
        # Loud rather than defaulting, because a History built by hand with no
        # active snapshot is a caller bug it must hear about.
        raise ValueError("history has no active snapshot")

    @property
    def previous_of_active(self) -> Snapshot | None:
        """The snapshot immediately before the active one in append order.

        "Undo" is defined against append order, not against causality: the
        state to return to is whatever stood here before the active snapshot
        was created, which for an append-only list is simply its predecessor.
        (An activate-by-id appends a copy, so even after a jump the
        predecessor is the state the user was actually looking at.)
        """
        active = self.active
        index = next(i for i, s in enumerate(self.snapshots) if s.id == active.id)
        return self.snapshots[index - 1] if index > 0 else None


def path_for(data_dir: str) -> str:
    return os.path.join(data_dir, FILENAME)


def snapshot_zero(defaults: Mapping[str, int]) -> Snapshot:
    """The default profile as a snapshot — always derived, never stored."""
    return Snapshot(
        id=SNAPSHOT_ZERO_ID,
        values=dict(defaults),
        created_by=SNAPSHOT_ZERO_CREATED_BY,
        created_at="",
        note=SNAPSHOT_ZERO_NOTE,
        active=False,
        seeded=True,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(data_dir: str, defaults: Mapping[str, int]) -> History:
    """The full history, snapshot 0 prepended. Never writes.

    A missing file is not an error and not something to seed eagerly: the
    history is then exactly snapshot 0, so a fresh server has a profile
    without any migration step. A file that exists but is corrupt, or breaks
    the invariants (ids ascending from 1, exactly one active), raises naming
    the path — a half-written store read as "no history" would silently undo
    every tuned profile on the next write.
    """
    zero = snapshot_zero(defaults)
    path = path_for(data_dir)
    if not os.path.exists(path):
        return History(snapshots=(replace_active(zero, True),))

    with open(path, encoding="utf-8") as handle:
        try:
            raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{path} is not valid JSON ({exc}); refusing to read a corrupt "
                "threshold snapshot store as an empty history"
            ) from exc

    if not isinstance(raw, dict) or raw.get("version") != STORE_VERSION:
        got = raw.get("version") if isinstance(raw, dict) else type(raw).__name__
        raise ValueError(
            f"{path} has version {got!r}, expected {STORE_VERSION}; this build "
            "does not understand that store shape"
        )

    entries = raw.get("snapshots")
    if not isinstance(entries, list):
        raise ValueError(f"{path} has no 'snapshots' array")

    snapshots: list[Snapshot] = []
    last_id = SNAPSHOT_ZERO_ID
    for entry in entries:
        snapshot = _entry_to_snapshot(entry, path)
        if snapshot.id <= last_id:
            raise ValueError(
                f"{path} snapshot ids must ascend from {SNAPSHOT_ZERO_ID + 1}; "
                f"found {snapshot.id} after {last_id}"
            )
        last_id = snapshot.id
        snapshots.append(snapshot)

    active_count = sum(1 for s in snapshots if s.active)
    if active_count > 1:
        raise ValueError(f"{path} marks {active_count} snapshots active; exactly one is allowed")

    if not snapshots:
        return History(snapshots=(replace_active(zero, True),))
    if active_count == 0:
        # No persisted snapshot active means snapshot 0 is — the file simply
        # has not been touched since an undo returned to the defaults... which
        # cannot actually happen (undo appends a copy), so this is only a
        # hand-edited file. Accept it as snapshot 0 rather than bricking the
        # route, but it is reachable only by editing the file directly.
        return History(snapshots=(replace_active(zero, True), *snapshots))
    return History(snapshots=(zero, *snapshots))


def _entry_to_snapshot(entry: Any, path: str) -> Snapshot:
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: snapshot entry {entry!r} is not an object")
    try:
        snapshot_id = int(entry["id"])
        values_raw = entry["values"]
        created_by = str(entry["created_by"])
        created_at = str(entry["created_at"])
        note = str(entry["note"])
        active = bool(entry["active"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}: snapshot entry is malformed ({exc})") from exc
    if not isinstance(values_raw, dict):
        raise ValueError(f"{path}: snapshot {snapshot_id} 'values' is not an object")
    values = {str(k): int(v) for k, v in values_raw.items()}
    return Snapshot(
        id=snapshot_id,
        values=values,
        created_by=created_by,
        created_at=created_at,
        note=note,
        active=active,
    )


def replace_active(snapshot: Snapshot, active: bool) -> Snapshot:
    """The same snapshot with its ``active`` flag set (frozen dataclass)."""
    return Snapshot(
        id=snapshot.id,
        values=snapshot.values,
        created_by=snapshot.created_by,
        created_at=snapshot.created_at,
        note=snapshot.note,
        active=active,
        seeded=snapshot.seeded,
    )


def validate_values(
    values: Mapping[str, Any],
    known_sections: frozenset[str] | set[str],
    *,
    allow_removals: bool,
    context: str,
) -> dict[str, int | None]:
    """Coerce and reject a caller-supplied table — loudly.

    Keys must name categories this server can produce (the same rule
    ``scripts/gen-threshold-defaults.py`` applies to the shipped table: a
    default nothing can ever select is a typo), values must be non-negative
    integers (or numeric strings, which the resolvers already read as
    numbers). ``None`` is accepted only where removals are allowed (a delta),
    and means "drop this category from the new state".
    """
    out: dict[str, int | None] = {}
    for key, raw in values.items():
        section = str(key).strip()
        if not section:
            raise ValueError(f"{context}: empty category name")
        if section not in known_sections:
            raise ValueError(
                f"{context}: {section!r} is not a category this server serves; "
                "a threshold for a category nothing can select is a typo"
            )
        if raw is None:
            if not allow_removals:
                raise ValueError(
                    f"{context}: {section!r} is null, but removals are only "
                    f"allowed in {MODE_DELTA} mode"
                )
            out[section] = None
            continue
        if isinstance(raw, bool):
            raise ValueError(f"{context}: {section!r} threshold must be a number, not a bool")
        if isinstance(raw, float) and not raw.is_integer():
            raise ValueError(
                f"{context}: {section!r} threshold {raw!r} is not an integer "
                "(and truncating it would silently move the green point)"
            )
        try:
            number = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{context}: {section!r} threshold {raw!r} is not an integer"
            ) from exc
        if number < 0:
            raise ValueError(f"{context}: {section!r} threshold must be >= 0")
        out[section] = number
    return out


def _clean_note(note: str) -> str:
    note = (note or "").strip()
    if len(note) > _MAX_NOTE_CHARS:
        raise ValueError(f"note must be at most {_MAX_NOTE_CHARS} characters")
    return note


def _next_id(history: History) -> int:
    return max(s.id for s in history.snapshots) + 1


def _persist(data_dir: str, history: History) -> None:
    """Atomically write the persisted half of *history* (snapshot 0 excluded)."""
    body = {
        "version": STORE_VERSION,
        "snapshots": [
            snapshot.as_dict() for snapshot in history.snapshots if not snapshot.seeded
        ],
    }
    path = path_for(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        path, json.dumps(body, indent=2) + "\n", encoding="utf-8", newline="\n",
    )


def _append(
    data_dir: str,
    defaults: Mapping[str, int],
    *,
    values: Mapping[str, int],
    created_by: str,
    note: str,
) -> tuple[History, Snapshot]:
    """Append one new active snapshot holding *values*; return (history, new)."""
    if not created_by.strip():
        raise ValueError("created_by must name the caller (source@identity)")
    with _write_lock:
        history = load(data_dir, defaults)
        new = Snapshot(
            id=_next_id(history),
            values=dict(values),
            created_by=created_by.strip(),
            created_at=_now(),
            note=_clean_note(note),
            active=True,
        )
        rebuilt = History(snapshots=(
            *(replace_active(s, False) for s in history.snapshots), new,
        ))
        _persist(data_dir, rebuilt)
    return rebuilt, new


def create(
    data_dir: str,
    defaults: Mapping[str, int],
    *,
    values: Mapping[str, Any],
    mode: str,
    created_by: str,
    note: str = "",
    known_sections: frozenset[str] | set[str] = frozenset(),
) -> tuple[History, Snapshot]:
    """Create a snapshot from a full profile or a sparse delta.

    ``delta`` (the default) merges onto the ACTIVE snapshot's values — the
    new state is "what this server says now, plus these moves", with a null
    value removing a category so it falls through to snapshot 0 again.
    ``full`` takes the profile as-is (no nulls: absence already means
    "fall through", a null in a full profile is a caller that confused the
    two modes).
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    cleaned = validate_values(
        values, known_sections, allow_removals=(mode == MODE_DELTA),
        context=f"PUT /v1/thresholds ({mode} mode)",
    )
    if mode == MODE_FULL:
        new_values: dict[str, int] = {k: int(v) for k, v in cleaned.items()}
    else:
        history = load(data_dir, defaults)
        new_values = dict(history.active.values)
        for section, value in cleaned.items():
            if value is None:
                new_values.pop(section, None)
            else:
                new_values[section] = value
    return _append(
        data_dir, defaults, values=new_values, created_by=created_by, note=note,
    )


def undo(
    data_dir: str,
    defaults: Mapping[str, int],
    *,
    created_by: str,
    note: str = "",
) -> tuple[History, Snapshot]:
    """Append a copy of the previous snapshot and make it active.

    Never destructive: the undone snapshot stays in history exactly as it was.
    Undoing while snapshot 0 is active raises NothingToUndoError (409) —
    there is no state before the shipped defaults, and a silent no-op would
    leave the caller believing it had undone something.
    """
    history = load(data_dir, defaults)
    previous = history.previous_of_active
    if previous is None:
        raise NothingToUndoError(
            "snapshot 0 (the shipped defaults) is active — there is no earlier "
            "state to undo to",
            active_id=history.active.id,
        )
    suffix = f" — {note}" if _clean_note(note) else ""
    return _append(
        data_dir,
        defaults,
        values=dict(previous.values),
        created_by=created_by,
        note=f"undo: copy of snapshot {previous.id}{suffix}",
    )


def activate(
    data_dir: str,
    defaults: Mapping[str, int],
    snapshot_id: int,
    *,
    created_by: str,
    note: str = "",
) -> tuple[History, Snapshot]:
    """Make an older snapshot's profile active — by appending a copy of it.

    Same append-a-copy semantics as undo, because the alternative (flipping
    ``active`` back onto an old row) would rewrite history's shape: the
    activation is itself an event and belongs in the record. Activating the
    already-active snapshot raises rather than appending a pointless clone.
    """
    history = load(data_dir, defaults)
    target = next((s for s in history.snapshots if s.id == snapshot_id), None)
    if target is None:
        known = ", ".join(str(s.id) for s in history.snapshots)
        raise NotFoundError(
            f"no threshold snapshot {snapshot_id}; this server has {known}"
        )
    if target.active:
        raise ValueError(
            f"threshold snapshot {snapshot_id} is already active; activating it "
            "again would append a clone that changes nothing"
        )
    suffix = f" — {note}" if _clean_note(note) else ""
    return _append(
        data_dir,
        defaults,
        values=dict(target.values),
        created_by=created_by,
        note=f"activate snapshot {snapshot_id}{suffix}",
    )


def resolved_profile(
    active: Snapshot,
    defaults: Mapping[str, int],
    sections: list[str],
) -> dict[str, int]:
    """The server-side half of the resolution, per served category.

    Active snapshot -> snapshot 0 -> derivation -> base, with no client
    override or preset (the server cannot know either — they are client
    state; see the resolution spec in domain/thresholds.py). This is what
    `GET /v1/thresholds` reports as `resolved`: what every client on this
    server gets for a category unless their own tables name it first.
    """
    return {
        section: thresholds.resolve(
            section, None, defaults, None, server=active.values,
        )
        for section in sections
    }
