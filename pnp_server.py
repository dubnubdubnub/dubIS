"""PnP consumption server — HTTP API for OpenPnP to report part placements."""

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)

PNP_PORT = 7890


def _load_part_map(base_dir):
    """Load pnp_part_map.json from data directory."""
    path = os.path.join(base_dir, "pnp_part_map.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _resolve_part_id(part_id, part_map, inventory):
    """Resolve an OpenPnP part ID to a dubIS part key.

    Strategy:
    1. Check pnp_part_map.json for explicit mapping
    2. Try direct match against inventory LCSC/MPN/Digikey keys
    3. Return None if unresolved
    """
    # 1. Explicit mapping
    if part_id in part_map:
        return part_map[part_id]

    # 2. Direct match against inventory keys
    for item in inventory:
        if part_id in (item.get("lcsc"), item.get("mpn"), item.get("digikey")):
            return item.get("lcsc") or item.get("mpn") or item.get("digikey")

    return None


class PnPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for PnP consumption events."""

    def log_message(self, format, *args):
        """Route request logging through Python logging instead of stderr."""
        logger.info("PnP server: " + format, *args)

    def _send_json(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        if self.path == "/api/health":
            self._send_json(200, {"ok": True})
        elif self.path == "/api/parts":
            try:
                inventory = self.server.api._load_organized()
                self._send_json(200, {"ok": True, "parts": inventory})
            except Exception as exc:
                logger.error("PnP /api/parts failed: %s", exc)
                self._send_json(500, {"ok": False, "error": str(exc)})
        else:
            self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if self.path != "/api/consume":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": f"Bad JSON: {exc}"})
            return

        try:
            part_id = body.get("part_id", "").strip()
            qty = int(body.get("qty", 1))
        except (ValueError, TypeError, AttributeError) as exc:
            self._send_json(400, {"ok": False, "error": f"Bad request: {exc}"})
            return
        if not part_id:
            self._send_json(400, {"ok": False, "error": "part_id is required"})
            return
        if qty <= 0:
            self._send_json(400, {"ok": False, "error": "qty must be positive"})
            return

        api = self.server.api
        window = self.server.window

        # Resolve part ID
        part_map = _load_part_map(api.base_dir)
        inventory = api._load_organized()
        part_key = _resolve_part_id(part_id, part_map, inventory)

        if not part_key:
            msg = f"Unknown part ID: {part_id}"
            logger.warning("PnP consume: %s", msg)
            self._send_json(404, {"ok": False, "error": msg})
            return

        # Perform the adjustment
        try:
            fresh = api.adjust_part("remove", part_key, qty, "OpenPnP placement")
        except Exception as exc:
            logger.error("PnP consume adjust_part failed: %s", exc)
            self._send_json(500, {"ok": False, "error": str(exc)})
            return

        # Find new quantity for this part
        new_qty = None
        for item in fresh:
            item_key = item.get("lcsc") or item.get("mpn") or item.get("digikey")
            if item_key == part_key:
                new_qty = item.get("qty")
                break

        # Push UI update
        detail = {"part_id": part_id, "part_key": part_key, "qty": qty, "new_qty": new_qty}
        try:
            js_code = "window._pnpConsume({inv}, {detail})".format(
                inv=json.dumps(fresh),
                detail=json.dumps(detail),
            )
            window.evaluate_js(js_code)
        except Exception as exc:
            logger.warning("PnP UI push failed (window may be closed): %s", exc)

        logger.info("PnP consumed %dx %s (key=%s, new_qty=%s)", qty, part_id, part_key, new_qty)
        self._send_json(200, {
            "ok": True,
            "part_key": part_key,
            "new_qty": new_qty,
        })


def start_pnp_server(api, window, port=PNP_PORT):
    """Start the PnP HTTP server in a daemon thread."""
    server = HTTPServer(("0.0.0.0", port), PnPHandler)
    server.api = api
    server.window = window

    thread = threading.Thread(target=server.serve_forever, name="pnp-server", daemon=True)
    thread.start()
    logger.info("PnP server listening on port %d", port)
    return server
