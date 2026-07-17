"""Standalone entry point: run the /v1 server in the foreground.

Constructs a headless InventoryApi pointed at --data-dir exactly the way
tests/pnp-e2e/dubis_headless.py used to (before it was deleted in Phase 1b —
see git history for the original), then blocks running uvicorn in the
foreground. Also carries the --test-source/--rollback-on-exit test-harness
semantics that used to live in dubis_headless.py, generalized to cover every
mutation route (not just PnP consume): every adjustment made through this
server instance is tagged with --test-source (overriding whatever source the
caller supplies), so a single `rollback_source` call — run on exit, or via
the test-only /v1/_test/reset route — cleans up everything a test session
touched.
"""

from __future__ import annotations

import argparse
import atexit
import os
import threading

import uvicorn

from distributor_manager import DistributorManager
from inventory_api import InventoryApi
from server.app import create_app
from server.run import _remove_port_file, _write_port_file, wait_until_started


def _build_api(data_dir: str) -> InventoryApi:
    """Construct an InventoryApi pointed at ``data_dir`` instead of the repo.

    InventoryApi.__init__ derives ALL of base_dir, input_csv, output_csv,
    adjustments_csv, prefs_json, cache_db_path, and events_dir from base_dir
    at construction time — every one of those must be repointed here, or a
    standalone server run against --data-dir X silently writes its SQLite
    cache and price/part events into the repo's own data/ and events/ dirs
    instead of X.
    """
    api = InventoryApi()
    api.base_dir = data_dir
    api.input_csv = os.path.join(data_dir, "purchase_ledger.csv")
    api.output_csv = os.path.join(data_dir, "inventory.csv")
    api.adjustments_csv = os.path.join(data_dir, "adjustments.csv")
    api.prefs_json = os.path.join(data_dir, "preferences.json")
    api.cache_db_path = os.path.join(data_dir, "cache.db")
    api.events_dir = os.path.join(data_dir, "events")
    # InventoryApi.__init__ constructs self._distributors = DistributorManager
    # bound to the DEFAULT base_dir (the real repo data/) before this function
    # ever gets a chance to repoint api.base_dir above — DistributorManager
    # captures a plain string at construction time, not a live reference, so
    # it doesn't follow the reassignment. Without this, a standalone server
    # (or a live/pnp-e2e test session) that touches distributor credentials
    # (set_mouser_api_key, digikey cookies, etc.) silently reads and writes
    # the REAL repo's data/ directory instead of --data-dir. Found the hard
    # way during Phase 1b Task 9: a live E2E run polluted the real repo's
    # data/mouser_credentials.json.
    api._distributors = DistributorManager(api.base_dir, api._get_cache)
    return api


def _tag_source(api: InventoryApi, test_source: str) -> None:
    """Force every adjustment made through ``api`` to carry ``test_source``.

    Wraps the two methods that append rows to adjustments.csv
    (adjust_part, consume_bom) so the tag wins regardless of what source the
    caller passes — including PnP's `/v1/pnp/consume` route, which hardcodes
    source="openpnp". Without overriding an explicit caller source too, a
    --rollback-on-exit session couldn't clean up PnP-consumed test rows, and
    the whole point of --test-source is that ALL adjustments this instance
    makes are cleanable with one `rollback_source` call.
    """
    orig_adjust = api.adjust_part
    orig_consume = api.consume_bom

    def adjust_part(adj_type, part_key, quantity, note="", source=""):
        return orig_adjust(adj_type, part_key, quantity, note, test_source)

    def consume_bom(matches_json, board_qty, bom_name, note="", source=""):
        return orig_consume(matches_json, board_qty, bom_name, note, test_source)

    api.adjust_part = adjust_part
    api.consume_bom = consume_bom


def _rollback_on_exit(api: InventoryApi, test_source: str) -> None:
    """The --rollback-on-exit shutdown action: remove every tagged adjustment.

    A plain function (not a signal handler) so tests can simulate "shutdown"
    by calling it directly. Registered via atexit.register in main() —
    uvicorn's own SIGINT/SIGTERM handling triggers a graceful stop and normal
    interpreter exit, which runs atexit hooks, so no separate signal.signal
    wiring is needed (dubis_headless.py installed its own SIGTERM/SIGINT
    handlers because it had no framework doing graceful shutdown for it;
    uvicorn already does).
    """
    removed = api.rollback_source(test_source)
    print(f"[server] Rolled back {len(removed)} test adjustment(s) with source={test_source!r}", flush=True)


def _mount_test_routes(app, api: InventoryApi, test_source: str) -> None:
    """Mount the test-only reset route. NOT part of server/app.py's
    production surface — only called from here, when --test-source is set.

    Lighter than the deleted tests/e2e-server.py's full-fixture recopy: it
    truncates adjustments tagged with this session's source and rebuilds.
    Direct purchase_ledger.csv writes (import_purchases, update_part_price,
    update_part_fields, delete_part) are NOT undone by this — those routes
    don't take a source and never wrote adjustment rows. Live specs
    exercising them must use distinct part keys per test/file rather than
    relying on --test-source cleanup.
    """

    @app.post("/v1/_test/reset")
    def _test_reset() -> dict:
        removed = api.rollback_source(test_source)
        return {"ok": True, "removed": removed}

    # If a static_dir was given, create_app mounted StaticFiles at "/" — a
    # catch-all Mount that Starlette tries in registration order. Our route
    # above was appended AFTER that mount, so the Mount would intercept
    # POST /v1/_test/reset first (StaticFiles 405s any non-GET/HEAD method
    # rather than falling through). Move it to the front of the route table
    # so it's tried before any catch-all mount.
    app.router.routes.insert(0, app.router.routes.pop())


def _print_ready_when_started(
    server: "uvicorn.Server", port_arg: int, data_dir: str | None = None,
) -> None:
    """Print READY:<port> once uvicorn has actually bound its socket, and
    (when data_dir is given) write the bound port to <data_dir>/.v1_port —
    the same discovery signal server/run.py's start_server() writes for the
    in-thread desktop-app path, so a standalone `python -m server` instance
    is equally discoverable by tools/dubis-mcp/v1client.py.

    Mirrors the tests/e2e-server.py contract that Playwright's global-setup
    parses to learn the port when --port 0 is used. Runs in a daemon thread
    started before server.run() blocks the main thread.
    """
    if not wait_until_started(server, timeout=10, poll=0.01):
        return
    port = port_arg
    if port == 0:
        port = server.servers[0].sockets[0].getsockname()[1]
    if data_dir is not None:
        _write_port_file(data_dir, port)
    print(f"READY:{port}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone dubIS /v1 server")
    parser.add_argument("--data-dir", default=".", help="Directory with CSV data")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=7891, help="Port to bind (0 = auto-assign)")
    parser.add_argument("--static-dir", default=None, help="Directory to serve as the frontend")
    parser.add_argument("--test-source", default="",
                         help="Tag all adjustments made through this instance with this source")
    parser.add_argument("--rollback-on-exit", action="store_true",
                         help="Roll back all adjustments with --test-source on shutdown")
    args = parser.parse_args()

    if args.rollback_on_exit and not args.test_source:
        parser.error("--rollback-on-exit requires --test-source")

    data_dir = os.path.abspath(args.data_dir)
    api = _build_api(data_dir)

    if args.test_source:
        _tag_source(api, args.test_source)

    app = create_app(api, static_dir=args.static_dir)

    if args.test_source:
        _mount_test_routes(app, api, args.test_source)

    if args.rollback_on_exit:
        atexit.register(_rollback_on_exit, api, args.test_source)

    atexit.register(_remove_port_file, data_dir)

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)

    threading.Thread(
        target=_print_ready_when_started, args=(server, args.port, data_dir), daemon=True,
    ).start()

    server.run()


if __name__ == "__main__":
    main()
