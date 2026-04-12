"""Generic HTTP bridge that wraps InventoryApi for live-backend E2E tests.

Playwright tests start this server via child_process.spawn, then inject a JS
bridge that translates window.pywebview.api.method() calls into fetch() requests.

Usage:
    python tests/e2e-server.py --fixture-dir tests/js/e2e/fixtures/e2e-seed --port 0
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

# Add project root so `from inventory_api import InventoryApi` works.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from inventory_api import InventoryApi  # noqa: E402


def _copy_fixtures(fixture_dir: str, data_dir: str) -> None:
    """Copy all files from fixture_dir into data_dir (flat copy)."""
    for entry in os.listdir(fixture_dir):
        src = os.path.join(fixture_dir, entry)
        dst = os.path.join(data_dir, entry)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
        elif os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)


def _init_api(data_dir: str) -> InventoryApi:
    """Create and configure an InventoryApi pointing at data_dir."""
    api = InventoryApi()
    api.base_dir = data_dir
    api.input_csv = os.path.join(data_dir, "purchase_ledger.csv")
    api.output_csv = os.path.join(data_dir, "inventory.csv")
    api.adjustments_csv = os.path.join(data_dir, "adjustments.csv")
    api.prefs_json = os.path.join(data_dir, "preferences.json")
    api.cache_db_path = os.path.join(data_dir, "cache.db")
    api.events_dir = os.path.join(data_dir, "events")
    return api


def _delete_cache_files(data_dir: str) -> None:
    """Delete cache.db and its WAL/SHM sidecar files."""
    for suffix in ("", "-wal", "-shm"):
        path = os.path.join(data_dir, f"cache.db{suffix}")
        if os.path.exists(path):
            os.remove(path)


class _SilentHandler(BaseHTTPRequestHandler):
    """HTTP request handler that suppresses default request logging."""

    # Shared state set by the factory — avoids subclass gymnastics.
    api: InventoryApi
    fixture_dir: str
    data_dir: str

    # Suppress default stderr logging for clean test output.
    def log_message(self, format, *args):  # noqa: A002
        pass

    # ── CORS ──────────────────────────────────────────────────────────────

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    # ── OPTIONS (CORS preflight) ──────────────────────────────────────────

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── GET /api/health ───────────────────────────────────────────────────

    def do_GET(self):  # noqa: N802
        if self.path == "/api/health":
            self._json_response({"ok": True})
        else:
            self._json_response({"ok": False, "error": "Not found"}, 404)

    # ── POST /api/<method> ────────────────────────────────────────────────

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/api/"):
            self._json_response({"ok": False, "error": "Not found"}, 404)
            return

        method_name = self.path[len("/api/"):]

        # Special reset endpoint.
        if method_name == "_reset":
            self._handle_reset()
            return

        # Block private methods.
        if method_name.startswith("_"):
            self._json_response(
                {"ok": False, "error": f"Private method not allowed: {method_name}"},
                403,
            )
            return

        # Resolve method on the API instance.
        fn = getattr(self.api, method_name, None)
        if fn is None or not callable(fn):
            self._json_response(
                {"ok": False, "error": f"Unknown method: {method_name}"},
                404,
            )
            return

        # Parse request body.
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length:
            raw = self.rfile.read(content_length)
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._json_response(
                    {"ok": False, "error": f"Invalid JSON: {exc}"},
                    400,
                )
                return
        else:
            body = {}

        args = body.get("args", [])

        try:
            result = fn(*args)
            self._json_response({"ok": True, "result": result})
        except Exception as exc:
            self._json_response(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                500,
            )

    # ── _reset handler ────────────────────────────────────────────────────

    def _handle_reset(self) -> None:
        """Re-copy fixtures, nuke cache, rebuild inventory."""
        api = self.api

        # 1. Close existing cache connection.
        if api._cache_conn is not None:
            try:
                api._cache_conn.close()
            except Exception:
                pass
            api._cache_conn = None

        # 2. Re-copy all fixture files.
        _copy_fixtures(self.fixture_dir, self.data_dir)

        # 3. Delete cache.db and sidecars.
        _delete_cache_files(self.data_dir)

        # 4. Rebuild inventory.
        try:
            result = api.rebuild_inventory()
            self._json_response({"ok": True, "result": result})
        except Exception as exc:
            self._json_response(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                500,
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HTTP bridge wrapping InventoryApi for E2E tests",
    )
    parser.add_argument(
        "--fixture-dir", required=True,
        help="Directory containing seed CSV files",
    )
    parser.add_argument(
        "--port", type=int, default=0,
        help="Port to listen on (0 = auto-assign)",
    )
    args = parser.parse_args()

    fixture_dir = os.path.abspath(args.fixture_dir)
    if not os.path.isdir(fixture_dir):
        print(f"ERROR: fixture directory not found: {fixture_dir}", file=sys.stderr)
        sys.exit(1)

    # Create temp data directory and copy fixtures into it.
    data_dir = tempfile.mkdtemp(prefix="dubis-e2e-")
    _copy_fixtures(fixture_dir, data_dir)

    # Ensure events/ subdirectory exists.
    os.makedirs(os.path.join(data_dir, "events"), exist_ok=True)

    # Initialize InventoryApi.
    api = _init_api(data_dir)

    # Inject shared state into the handler class.
    _SilentHandler.api = api
    _SilentHandler.fixture_dir = fixture_dir
    _SilentHandler.data_dir = data_dir

    # Start HTTP server.
    server = HTTPServer(("127.0.0.1", args.port), _SilentHandler)
    actual_port = server.server_address[1]

    # Signal readiness — Playwright helpers parse this line.
    print(f"READY:{actual_port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Close cache connection if open.
        if api._cache_conn is not None:
            try:
                api._cache_conn.close()
            except Exception:
                pass
        # Clean up temp directory.
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
