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

from mirror_serialize import inventory_stats, inventory_to_csv

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


def _freshness(snap):
    if not snap:
        return {"pushed_at": None, "received_at": None, "age_seconds": None,
                "dubis_running": None, "source": None}
    age = None
    received = snap.get("received_at")
    if received:
        try:
            recv_dt = datetime.fromisoformat(received)
            age = max(0, int((datetime.now(timezone.utc) - recv_dt).total_seconds()))
        except ValueError:
            age = None
    return {
        "pushed_at": snap.get("pushed_at"),
        "received_at": received,
        "age_seconds": age,
        "dubis_running": snap.get("dubis_running"),
        "source": snap.get("source"),
    }


class ReadHandler(BaseHTTPRequestHandler):
    """Loopback read endpoint. tailscale serve injects Tailscale-User-Login for
    tailnet requests; those must be allow-listed. Header-absent = trusted loopback."""

    def log_message(self, fmt, *args):
        logger.info("read: " + fmt, *args)

    def _authorized(self):
        login = self.headers.get("Tailscale-User-Login")
        if login is None:
            return True  # direct loopback read
        return login in self.server.allowlist

    def _send_csv(self, text):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", 'attachment; filename="inventory.csv"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            _send_json(self, 403, {"ok": False, "error": "Forbidden"})
            return
        path = self.path.split("?", 1)[0]
        snap = self.server.store.get()
        inv = (snap or {}).get("inventory", [])
        fields = (snap or {}).get("csv_fields")

        if path == "/api/health":
            _send_json(self, 200, {"ok": True, "has_snapshot": snap is not None,
                                   "age_seconds": _freshness(snap)["age_seconds"]})
        elif path == "/api/inventory":
            _send_json(self, 200, {"ok": True, "count": len(inv),
                                   "inventory": inv, "freshness": _freshness(snap)})
        elif path == "/api/inventory.csv":
            self._send_csv(inventory_to_csv(inv, fields=fields))
        elif path == "/api/stats":
            _send_json(self, 200, {"ok": True, **inventory_stats(inv),
                                   "freshness": _freshness(snap)})
        elif path in ("/", "/api"):
            _send_json(self, 200, {"ok": True, "endpoints": [
                "/api/health", "/api/inventory", "/api/inventory.csv", "/api/stats"]})
        else:
            _send_json(self, 404, {"ok": False, "error": "Not found"})


def make_read_server(store, allowlist, host="127.0.0.1", port=DEFAULT_READ_PORT):
    server = HTTPServer((host, port), ReadHandler)
    server.store = store
    server.allowlist = list(allowlist or [])
    return server
