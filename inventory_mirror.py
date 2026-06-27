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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

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


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _send_json(handler, status, data):
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class PushHandler(BaseHTTPRequestHandler):
    """Loopback-only push endpoint. Requires the shared token."""

    def log_message(self, fmt, *args):
        logger.info("push: " + fmt, *args)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/push":
            _send_json(self, 404, {"ok": False, "error": "Not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            _send_json(self, 400, {"ok": False, "error": "Invalid JSON"})
            return
        if payload.get("token") != self.server.token:
            _send_json(self, 403, {"ok": False, "error": "Forbidden"})
            return
        self.server.store.update(payload, received_at=_now_iso())
        _send_json(self, 200, {"ok": True})


def make_push_server(store, token, host="127.0.0.1", port=DEFAULT_PUSH_PORT):
    server = HTTPServer((host, port), PushHandler)
    server.store = store
    server.token = token
    return server
