"""Local poll API — read-only HTTP server for inspecting inventory state.

Bound to 127.0.0.1 only (loopback). Exposes JSON and CSV dumps of the
current inventory so external tools (e.g. Claude) can poll the running
desktop app without going through the JS bridge.
"""

import csv
import io
import json
import logging
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)

POLL_PORT = 7891

INVENTORY_CSV_FIELDS = [
    "section", "lcsc", "mpn", "digikey", "pololu", "mouser",
    "manufacturer", "package", "description", "qty",
    "unit_price", "ext_price", "primary_vendor_id",
]


def _inventory_to_csv(inventory):
    """Render an inventory list as CSV text."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=INVENTORY_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for item in inventory:
        writer.writerow(item)
    return buf.getvalue()


def _inventory_stats(inventory):
    """Compute summary stats for an inventory list."""
    sections = Counter(item.get("section") or "" for item in inventory)
    total_qty = sum(int(item.get("qty") or 0) for item in inventory)
    return {
        "part_count": len(inventory),
        "total_qty": total_qty,
        "section_counts": dict(sections),
    }


class PollHandler(BaseHTTPRequestHandler):
    """HTTP request handler for read-only inventory polling."""

    def log_message(self, format, *args):
        logger.info("Poll API: " + format, *args)

    def _send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_csv(self, status, text, filename):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Content-Disposition", f'attachment; filename="{filename}"',
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/health":
            self._send_json(200, {"ok": True})
            return

        if path == "/api/inventory":
            try:
                inventory = self.server.api._load_organized()
            except Exception as exc:
                logger.error("Poll /api/inventory failed: %s", exc)
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, {
                "ok": True,
                "count": len(inventory),
                "inventory": inventory,
            })
            return

        if path == "/api/inventory.csv":
            try:
                inventory = self.server.api._load_organized()
            except Exception as exc:
                logger.error("Poll /api/inventory.csv failed: %s", exc)
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_csv(200, _inventory_to_csv(inventory), "inventory.csv")
            return

        if path == "/api/stats":
            try:
                inventory = self.server.api._load_organized()
            except Exception as exc:
                logger.error("Poll /api/stats failed: %s", exc)
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, {"ok": True, **_inventory_stats(inventory)})
            return

        if path == "/" or path == "/api":
            self._send_json(200, {
                "ok": True,
                "endpoints": [
                    "/api/health",
                    "/api/inventory",
                    "/api/inventory.csv",
                    "/api/stats",
                ],
            })
            return

        self._send_json(404, {"ok": False, "error": "Not found"})


class _FastHTTPServer(HTTPServer):
    """HTTPServer that skips the slow FQDN reverse-DNS lookup in server_bind()."""

    def server_bind(self):
        if self.allow_reuse_address and hasattr(self.socket, "setsockopt"):
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


def start_poll_server(api, port=POLL_PORT):
    """Start the poll API server in a daemon thread.

    Bound to 127.0.0.1 — local-only by design. Stores the server on
    `api._poll_server` so it can be restarted via `restart_poll_server`.
    """
    server = _FastHTTPServer(("127.0.0.1", port), PollHandler)
    server.api = api

    thread = threading.Thread(
        target=server.serve_forever, name="poll-api", daemon=True,
    )
    thread.start()
    api._poll_server = server
    logger.info("Poll API listening on 127.0.0.1:%d", server.server_address[1])
    return server


def restart_poll_server(api, port):
    """Shut down the existing poll server (if any) and start a new one on `port`."""
    old = getattr(api, "_poll_server", None)
    if old is not None:
        try:
            old.shutdown()
            old.server_close()
        except Exception as exc:
            logger.warning("Poll API shutdown failed: %s", exc)
    return start_poll_server(api, port=port)
