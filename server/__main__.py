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

Two transports, not one. `--host`/`--port` is the usual TCP bind. `--uds
<path>` binds a Unix domain socket instead, and on Linux every request over
it is attributed to the connecting user by the kernel's SO_PEERCRED uid
(server/peercred.py, server/uds.py). That is what makes a shared box usable:
teammates reach the server with `ssh -L <port>:<path>`, sshd connects to the
socket as *them*, and their mutations are stamped with their own username
instead of every session collapsing into `local`. The two transports are
mutually exclusive and saying so is a hard error, never a silent preference.
"""

from __future__ import annotations

import argparse
import atexit
import os
import socket
import sys
import threading

import uvicorn

from distributor_manager import DistributorManager
from dubis_errors import DataDirLockedError
from inventory_api import InventoryApi
from server import peercred, uds
from server.app import create_app
from server.lockfile import acquire_lock
from server.run import (
    _remove_port_file,
    _remove_uds_file,
    _write_port_file,
    _write_uds_file,
    wait_until_started,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7891


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
    server: "uvicorn.Server",
    port_arg: int | None,
    data_dir: str | None = None,
    lock=None,
    uds_path: str | None = None,
) -> None:
    """Print READY:<port> once uvicorn has actually bound its socket, and
    (when data_dir is given) write the bound port to <data_dir>/.v1_port —
    the same discovery signal server/run.py's start_server() writes for the
    in-thread desktop-app path, so a standalone `python -m server` instance
    is equally discoverable by tools/dubis_client/v1client.py. Also updates
    the data-dir lockfile's content with the resolved port, if *lock* is
    given (it was acquired with port=None in main(), before the actual
    port was known).

    Mirrors the tests/e2e-server.py contract that Playwright's global-setup
    parses to learn the port when --port 0 is used. Runs in a daemon thread
    started before server.run() blocks the main thread.

    With *uds_path* the whole port half of that is wrong, not merely absent:
    a Unix-socket server's `sockets[0].getsockname()` is the path *string*,
    so the usual `[1]` would silently yield the path's second character, and
    the lockfile/`.v1_port` would advertise a TCP port nothing is listening
    on. So it writes `<data_dir>/.v1_uds` instead and prints
    `READY:uds:<path>`, leaving the lock's port as the `None` it was
    acquired with — the lock's pid is still what a contention message needs.
    """
    if not wait_until_started(server, timeout=10, poll=0.01):
        return
    if uds_path is not None:
        if data_dir is not None:
            _write_uds_file(data_dir, uds_path)
        print(f"READY:uds:{os.path.abspath(uds_path)}", flush=True)
        return
    port = port_arg
    if port == 0:
        port = server.servers[0].sockets[0].getsockname()[1]
    if data_dir is not None:
        _write_port_file(data_dir, port)
    if lock is not None:
        lock.update_port(port)
    print(f"READY:{port}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone dubIS /v1 server")
    parser.add_argument("--data-dir", default=".", help="Directory with CSV data")
    # --host/--port default to None, not to their values, purely so that
    # "was this flag given?" is answerable below. Resolved to DEFAULT_HOST /
    # DEFAULT_PORT once --uds has been ruled out.
    parser.add_argument("--host", default=None,
                        help=f"Host to bind (default {DEFAULT_HOST}; TCP only)")
    parser.add_argument("--port", type=int, default=None,
                        help=f"Port to bind (default {DEFAULT_PORT}, 0 = auto-assign; TCP only)")
    parser.add_argument("--uds", default=None,
                        help="Bind a Unix domain socket at this path instead of a TCP "
                             "port. On Linux each caller is identified by the kernel's "
                             "SO_PEERCRED uid (see server/peercred.py), so `ssh -L "
                             "<port>:<path>` attributes mutations to the logged-in user.")
    parser.add_argument("--static-dir", default=None, help="Directory to serve as the frontend")
    parser.add_argument("--test-source", default="",
                         help="Tag all adjustments made through this instance with this source")
    parser.add_argument("--rollback-on-exit", action="store_true",
                         help="Roll back all adjustments with --test-source on shutdown")
    args = parser.parse_args()

    if args.rollback_on_exit and not args.test_source:
        parser.error("--rollback-on-exit requires --test-source")

    # --uds and --host/--port are two different transports, and silently
    # honouring one while ignoring the other is precisely the failure this
    # refuses: an operator who typed both would otherwise get a TCP server
    # they believed was a socket, with every caller resolving to `local`
    # again and no sign anything was wrong.
    if args.uds is not None:
        given = [flag for flag, value in (("--host", args.host), ("--port", args.port))
                 if value is not None]
        if given:
            parser.error(f"--uds cannot be combined with {' or '.join(given)} — "
                         "a Unix socket has no host or port")
        if getattr(socket, "AF_UNIX", None) is None:
            parser.error("--uds is not supported on this platform: it has no AF_UNIX sockets")
        if not peercred.available():
            # Not fatal: the socket itself works fine here, callers just
            # resolve to `local` exactly as a loopback TCP caller does today.
            # Said once, loudly, so the degradation is never a mystery.
            print(
                f"[server] warning: --uds peer-credential identity is unavailable "
                f"({peercred.unavailable_reason()}). Unix-socket callers will resolve "
                f"to {peercred.LOCAL_IDENTITY!r}, the same identity a loopback TCP "
                f"caller gets — mutation sources will not name individual users.",
                file=sys.stderr, flush=True,
            )

    host = DEFAULT_HOST if args.host is None else args.host
    port = DEFAULT_PORT if args.port is None else args.port

    data_dir = os.path.abspath(args.data_dir)

    try:
        lock = acquire_lock(data_dir)
    except DataDirLockedError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc

    api = _build_api(data_dir)

    if args.test_source:
        _tag_source(api, args.test_source)

    app = create_app(api, static_dir=args.static_dir)

    if args.test_source:
        _mount_test_routes(app, api, args.test_source)

    def _teardown() -> None:
        """Single atexit hook, run in explicit order, so the data-dir lock
        is held for the entire teardown rather than relying on atexit's
        LIFO registration order to get that implicitly (fragile — the next
        person to add a hook here could easily reorder registrations without
        realizing the lock's release position matters). server.run() below
        blocks the main thread directly (unlike server/run.py's
        start_server(), which runs uvicorn on a background thread), so by
        the time this fires uvicorn has already fully stopped — no thread
        join is needed here the way stop_server() needs one.

        Order: roll back test adjustments (if any) -> remove the discovery
        port file -> commit+close cache.db -> release the lock last, so a
        second process can't acquire it and start writing until this
        process's cache connection is fully closed. Wrapped in try/finally
        (rather than one un-nested sequence) so a failure in an earlier step
        still lets later steps run — in particular, the lock must be
        released even if rollback or port-file removal blows up, or a
        crashed teardown would leave the data dir permanently locked for
        the rest of this process's lifetime (atexit hooks don't get a
        second chance)."""
        try:
            if args.rollback_on_exit:
                _rollback_on_exit(api, args.test_source)
        except Exception as exc:
            print(f"[server] teardown: rollback failed: {exc}", file=sys.stderr, flush=True)
        try:
            if args.uds is None:
                _remove_port_file(data_dir)
            else:
                # Both halves: the discovery file AND the socket file itself.
                # uvicorn creates the socket but never unlinks it, so without
                # this every hard-killed run leaves a file that the next
                # `--uds` start has to reason about (see uds.prepare_socket_path).
                _remove_uds_file(data_dir)
                uds.remove_socket_path(args.uds)
        except Exception as exc:
            print(f"[server] teardown: discovery-file removal failed: {exc}",
                  file=sys.stderr, flush=True)
        try:
            api.shutdown()  # best-effort internally; never raises
        finally:
            lock.release()

    atexit.register(_teardown)

    # timeout_graceful_shutdown: see server/routes/events.py's module docstring
    # -- without a bound, a connected SSE client stalls Server.shutdown() for
    # uvicorn's 30s default on every container rollout. Matches the value
    # tests/python/server/conftest.py's start_live_server() defaults to.
    if args.uds is not None:
        # Raises (SocketPathInUseError / OSError) rather than letting uvicorn
        # fail with a bare EADDRINUSE that says nothing about which of the
        # two cases — live server vs. a crashed one's leftover file — applies.
        uds.prepare_socket_path(args.uds)
        config = uvicorn.Config(
            app, uds=args.uds, log_level="info", timeout_graceful_shutdown=5,
            # The one line that makes the whole feature work: uvicorn's HTTP
            # protocol, subclassed to record the connecting user per
            # connection. `http=` is public uvicorn API; see server/uds.py.
            http=uds.PeerCredHTTPProtocol,
        )
    else:
        config = uvicorn.Config(
            app, host=host, port=port, log_level="info", timeout_graceful_shutdown=5,
        )
    server = uvicorn.Server(config)

    threading.Thread(
        target=_print_ready_when_started,
        args=(server, port, data_dir, lock, args.uds),
        daemon=True,
    ).start()

    server.run()


if __name__ == "__main__":
    main()
