"""dubIS inventory mirror daemon.

Standalone, stdlib-only. Receives inventory snapshots pushed by dubIS over loopback
(7892) and serves the last snapshot on loopback (7893) — locally and, via
`tailscale serve`, over Tailscale, independent of whether dubIS is running.

Run: python inventory_mirror.py --token-file data/mirror_token [--push-port 7892]
     [--read-port 7893] [--snapshot-file data/inventory_mirror.json]
     [--allowlist alice@example.com,bob@example.com]
"""

import json
import logging
import os
import threading

logger = logging.getLogger("inventory_mirror")

DEFAULT_PUSH_PORT = 7892
DEFAULT_READ_PORT = 7893


class SnapshotStore:
    """Thread-safe holder for the last pushed inventory snapshot, persisted to disk."""

    def __init__(self, path):
        self._path = path
        self._snapshot = None
        self._lock = threading.Lock()

    def load(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                self._snapshot = json.load(f)
        except FileNotFoundError:
            self._snapshot = None
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Mirror snapshot load failed (%s); starting empty", exc)
            self._snapshot = None

    def update(self, payload, received_at):
        snap = {k: v for k, v in payload.items() if k != "token"}
        snap["received_at"] = received_at
        with self._lock:
            self._snapshot = snap
            self._persist(snap)

    def get(self):
        with self._lock:
            return self._snapshot

    def _persist(self, snap):
        tmp = self._path + ".tmp"
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f)
        os.replace(tmp, self._path)
