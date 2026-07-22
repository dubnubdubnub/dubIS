#!/usr/bin/env python3
"""dubIS — desktop app entry point."""

import logging
import os
import sys
import threading
import time

import bench  # fixes t0 at first import; no-op unless DUBIS_BENCH_OUT is set

bench.mark("py_start")

logger = logging.getLogger(__name__)

# Ensure the app directory is on the path
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(APP_DIR, "data", "dubIS.ico")
PNG_ICON_PATH = os.path.join(APP_DIR, "data", "dubIS.png")
sys.path.insert(0, APP_DIR)

import ctypes

# Give the app its own taskbar identity so Windows uses our icon instead of python.exe's
if sys.platform == "win32":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("gehub.dubIS")

import webview
from client_shell import ClientShell
from dubis_errors import DataDirLockedError
from inventory_api import InventoryApi
from pnp_server import start_pnp_server, stop_pnp_server
from remote_mode import resolve_remote_base_url
from window_close import handle_closing

# NOTE: server.run (uvicorn + fastapi, ~300-400ms to import) is deliberately
# NOT imported here. It's imported lazily inside main()'s _boot_server(),
# which runs on a background thread started BEFORE webview.create_window() —
# see main() for the full boot sequence. Importing it at module level here
# would put that cost back on the path to first paint, which is exactly the
# Phase 1b Task 8 regression this file fixes.

bench.mark("imports_done")

SPLASH_PATH = os.path.join(APP_DIR, "splash.html")


def _free_port() -> int:
    """Bind an ephemeral loopback port and release it immediately for uvicorn
    to reuse. Small TOCTOU risk (another process could grab it in between) —
    matches the pattern already used by scripts/spike-webview-loopback.py and
    tests/python/server/test_lifecycle.py."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _show_error_dialog(title: str, message: str) -> None:
    """Show a native, blocking error dialog. Called from the server-boot
    background thread (never the pywebview UI thread) when startup fails in
    a way the user needs to know about immediately rather than discovering
    via splash.html's silent 15s health-poll timeout — e.g. DataDirLockedError.

    Windows-only MessageBoxW (this app only ships on Windows — see the
    win32-gated AppUserModelID call above); falls back to stderr elsewhere
    so this never crashes the boot thread on a dev machine running a
    non-Windows platform."""
    if sys.platform == "win32":
        MB_OK = 0x0
        MB_ICONERROR = 0x10
        MB_SYSTEMMODAL = 0x1000
        ctypes.windll.user32.MessageBoxW(None, message, title, MB_OK | MB_ICONERROR | MB_SYSTEMMODAL)
    else:
        print(f"{title}: {message}", file=sys.stderr, flush=True)


def _hard_exit(code: int = 0) -> None:
    """Terminate the process immediately, skipping the ~2s teardown a normal exit
    incurs. Even os._exit() runs DLL_PROCESS_DETACH for the in-process Chromium/
    WebView2 runtime and the .NET CLR as the process unwinds — that detach is what
    makes closing take seconds (see scripts/bench-close.py: ~2s of the ~2.3s close
    is spent here). We've already flushed everything we own in _cleanup() (PnP
    server stopped, SQLite committed + closed), so there is nothing left to clean
    up gracefully. TerminateProcess skips the detach entirely; orphaned WebView2
    child processes are reaped by the OS. Falls back to os._exit off Windows."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        # Set signatures explicitly: GetCurrentProcess returns the pseudo-handle
        # (HANDLE)-1; without restype=HANDLE ctypes truncates it to a 32-bit int,
        # producing an invalid handle so TerminateProcess fails and we fall through
        # to the slow os._exit. That truncation is exactly what made the first
        # attempt no-op.
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.TerminateProcess(k.GetCurrentProcess(), code)
    os._exit(code)


def set_icon():
    """Set window icon via native WinForms API (pywebview 6.1 ignores the icon param on Windows)."""
    import time
    if sys.platform == "win32" and os.path.isfile(ICON_PATH):
        from System.Drawing import Icon as DrawingIcon
        from System import Action
        ico = DrawingIcon(ICON_PATH)
        for w in webview.windows:
            deadline = time.monotonic() + 5.0
            while w.native is None or not w.native.IsHandleCreated:
                if time.monotonic() > deadline:
                    logger.warning("set_icon: native window handle not ready after 5s; skipping icon")
                    break
                time.sleep(0.05)
            else:
                # loop finished without timing out -> the native handle is ready
                w.native.Invoke(Action(lambda: setattr(w.native, "Icon", ico)))


def main():
    debug = "--debug" in sys.argv
    api = InventoryApi(debug=debug)
    bench.mark("api_constructed")

    # Remote-server mode (Phase 1c Task 7): DUBIS_URL env or preferences.json's
    # server_url point this desktop client at an already-deployed dubis-server
    # instead of spawning one locally. Constructing InventoryApi above is
    # still fine to do unconditionally even in remote mode — __init__ does no
    # I/O beyond building path strings (the SQLite cache connection is opened
    # lazily by _get_cache() on first access, which nothing here triggers),
    # and load_preferences() below is a single small JSON read. Neither
    # touches cache.db, so there's no wasted work building a local cache that
    # a remote-mode session will never use.
    #
    # remote_base is None for today's local path — byte-identical: same free
    # port, same _boot_server thread, same splash.html?port=<port> URL, same
    # PnP server + mirror push wiring in on_ready/_cleanup below.
    remote_base = resolve_remote_base_url(os.environ, api.load_preferences())
    is_remote = remote_base is not None

    shell = ClientShell(api)

    # ── Splash-first parallel boot (Phase 1b Task 8, fix round 1) ───────────
    # The regression: app.pyw used to import server.run (uvicorn+fastapi,
    # ~300-400ms) and block on server.started BEFORE create_window, so the
    # window didn't appear until the server was already up. Fix: create the
    # window immediately against a local, JS-free-except-a-health-poll
    # splash.html (same first paint as baseline), and boot the /v1 server on
    # a background thread that starts BEFORE create_window so the heavy
    # import runs fully off the UI path.
    #
    # The port is picked HERE, on the main thread, before create_window —
    # binding+releasing a socket (_free_port()) is a cheap stdlib-only call,
    # nothing like importing uvicorn/fastapi — so the window can be created
    # with the target port already baked into the splash URL
    # (splash.html?port=<port>), and the boot thread just starts the server
    # on that same port.
    #
    # Splash.html's own inline script polls http://127.0.0.1:<port>/v1/health
    # and navigates itself there once it answers — a normal page-initiated
    # top-level navigation, and app.pyw never touches the window object from
    # the boot thread at all (not even for the failure path — splash.html's
    # own script renders an inline error state after its poll times out).
    #
    # This is the second design tried here, not the first. Earlier, app.pyw
    # itself called window.load_url() from the boot thread once the server
    # was ready; that reproduced a real, ~50%-reliable pywebview
    # bridge-corruption crash (JavascriptException:
    # "...bench_mark.<id> is not a function") on the first bridge call the
    # real page made after navigating — reproducible even after marshaling
    # the call onto the native WinForms UI thread via Control.Invoke (the
    # same pattern set_icon() uses below). Switching to a page-initiated
    # navigation reduced but did NOT eliminate that crash on its own — it
    # turned out to be a broader timing race (navigating this window at all
    # too soon after creation corrupts the bridge, regardless of who
    # triggers it). splash.html's script carries a short empirically-tuned
    # delay before its first poll to work around that; see splash.html for
    # the full writeup and the bisection results that picked the delay.
    server_state: dict = {"server": None, "stop_server": None}
    port = int(os.environ.get("DUBIS_SERVER_PORT", 0)) or _free_port()

    def _boot_server() -> None:
        """Background thread: import + start the /v1 server on the
        already-chosen `port`. Started before create_window (see below) so
        the uvicorn/fastapi import cost never sits on the path to first
        paint. Never touches the window — splash.html's own script (started
        by pywebview once it renders) polls /v1/health and navigates itself;
        this thread's only job is getting uvicorn listening.

        The whole body is wrapped in try/except so an exception anywhere in
        here (import failure, port conflict, etc.) is logged instead of
        silently killing this daemon thread with nothing in the log. The
        splash's own 15s health-poll timeout already handles the user-facing
        UX for a boot failure; this only closes the silent-log gap."""
        try:
            from server.run import start_server, stop_server  # deferred: heavy import

            server_state["stop_server"] = stop_server
            v1_server = start_server(api, static_dir=APP_DIR, port=port, data_dir=api.base_dir)
            server_state["server"] = v1_server
            bench.mark("server_starting")

            start_deadline = time.monotonic() + 15
            while not v1_server.started and time.monotonic() < start_deadline:
                time.sleep(0.02)
            if not v1_server.started:
                # Loud, not silent: splash.html's own poll will also time out
                # (15s) and render its inline error state, but log here too since
                # that's the actionable signal for whoever's watching the logs.
                logger.error(
                    "/v1 loopback server did not start within 15s on port %s", port
                )
                return
            bench.mark("server_started")
        except DataDirLockedError as e:
            # Distinct from the generic except below: this is a common,
            # user-actionable case (another dubIS instance is already
            # running against this data dir — e.g. launched twice by
            # accident) rather than a boot bug, so it gets a real dialog
            # instead of just a log line splash.html's timeout silently
            # papers over.
            logger.error("v1 server boot failed: %s", e, exc_info=True)
            _show_error_dialog(
                "dubIS is already running",
                f"Another dubIS server is already running against this data "
                f"directory (pid={e.pid}, port={e.port}).\n\n"
                f"Close the other instance before opening a new one.",
            )
        except Exception as e:
            logger.error("v1 server boot failed: %s", e, exc_info=True)

    # Remote mode: no local server to spawn, so skip the boot thread entirely
    # (no port bound either — port is meaningless when is_remote, only used
    # for the splash.html?port= URL below and DUBIS_SERVER_PORT lookups, both
    # skipped). splash.html?base=<url> polls the REMOTE /v1/health instead
    # and navigates the webview there once it answers.
    #
    # Browser auth in remote mode: per the design (§7), humans reach the
    # tailnet-fronted server via tailnet identity — the webview just performs
    # a normal top-level navigation to remote_base, with no Authorization
    # header involved (pywebview navigation can't attach custom headers
    # cleanly, and the tailnet path doesn't need one). Bearer tokens
    # (DUBIS_TOKEN) are for headless clients only — see tools/dubis-mcp/v1client.py
    # — never injected into this webview navigation. Out of scope: a
    # non-tailnet/token browser auth story (e.g. a `?token=` cookie
    # bootstrap) — see the design doc's open question in §7.
    if not is_remote:
        threading.Thread(target=_boot_server, name="dubis-server-boot", daemon=True).start()

    if is_remote:
        import urllib.parse
        splash_url = f"{SPLASH_PATH}?base={urllib.parse.quote(remote_base, safe='')}"
    else:
        splash_url = f"{SPLASH_PATH}?port={port}"

    window = webview.create_window(
        "dubIS",
        url=splash_url,
        js_api=shell,
        width=1600,
        height=900,
        min_size=(1200, 700),
        background_color="#0d1117",  # match the dark theme so the shell doesn't flash white before first paint
    )
    shell._window = window

    pnp_server = None

    def _cleanup():
        """Best-effort teardown before os._exit. Order matters: stop the PnP
        server FIRST (no new requests; in-flight ones finish) so a mid-flight
        adjust_part can't write to a connection we're about to close, THEN
        commit+close the cache, THEN release the data-dir lock LAST.

        The lock must outlive both the /v1 server and the cache close: if it
        were released as soon as stop_server() is called (the old behavior),
        a second dubIS instance could acquire it and start writing to the
        same CSVs/cache.db while this process's uvicorn thread was still
        mid-shutdown or api.shutdown() hadn't committed/closed cache.db yet.
        stop_server() is called with release_lock=False here so it only
        joins the server thread (bounded by its own timeout) and removes the
        port file; the lock itself is released in the `finally` below, after
        api.shutdown() has run — whether or not api.shutdown() raised.

        All steps log rather than raise so a cleanup failure can't block
        process exit. Idempotent — safe to call repeatedly (e.g. closing
        then closed both fire); a second call finds no lock on server_state
        (or an already-released LockHandle, itself idempotent) and no-ops."""
        # Mirror: push current inventory on shutdown (no-op unless enabled).
        # Remote mode has no local inventory to mirror — the deployed server
        # is the source of truth there, not this thin client's local CSVs.
        if not is_remote:
            try:
                api._mirror_ctl.push_event(api._load_organized(), dubis_running=False, block=True)
            except Exception as exc:
                logger.warning("Mirror shutdown push failed: %s", exc)
        try:
            stop_pnp_server(pnp_server)
        except Exception as exc:
            logger.warning("Cleanup: stopping PnP server failed: %s", exc)
        bench.mark("pnp_stopped")
        v1_server = server_state.get("server")
        lock = getattr(v1_server, "_dubis_lock", None) if v1_server is not None else None
        try:
            stop_server = server_state.get("stop_server")
            if v1_server is not None and stop_server is not None:
                stop_server(v1_server, data_dir=api.base_dir, release_lock=False)
        except Exception as exc:
            logger.warning("Cleanup: stopping /v1 server failed: %s", exc)
        try:
            api.shutdown()
        except Exception as exc:
            logger.warning("Cleanup: api.shutdown failed: %s", exc)
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception as exc:
                    logger.warning("Cleanup: releasing data-dir lock failed: %s", exc)
        bench.mark("cache_closed")

    def _do_exit():
        _cleanup()
        bench.mark("pre_exit")
        _hard_exit(0)  # kill process immediately

    def on_closing():
        # NOTE: this runs synchronously on the WinForms UI thread (pywebview's
        # `closing` event is should_lock=True). handle_closing() must therefore
        # never call the blocking window.evaluate_js() on this thread — doing so
        # deadlocks the message pump against evaluate_js's completion callback
        # ("Not Responding", window won't close). See window_close.py.
        bench.mark("closing_enter")
        return handle_closing(
            force_close=api._force_close,
            bom_dirty=api._bom_dirty,
            open_modal=lambda: window.evaluate_js("closeModal.open()"),
            do_exit=_do_exit,
        )

    def on_closed():
        bench.mark("closed_enter")
        _cleanup()
        bench.mark("pre_exit")
        _hard_exit(0)

    window.events.closing += on_closing
    window.events.closed += on_closed
    def on_ready():
        nonlocal pnp_server
        bench.mark("on_ready")  # native window shown; WebView2 runtime up
        set_icon()
        # PnP server + mirror are local concerns (they operate on this
        # process's local CSVs/cache); in remote mode the desktop is a thin
        # client of the deployed server, so both are skipped entirely rather
        # than started against a local api that isn't the source of truth.
        if not is_remote:
            pnp_server = start_pnp_server(api)
            # Expose the running server so api.start_scan_session() can mint
            # sessions on it (phone-scan transport). May be None if the port
            # was unavailable.
            api._pnp_server = pnp_server
            # Mirror: push current inventory on startup (no-op unless enabled).
            try:
                api._mirror_ctl.push_event(api._load_organized(), dubis_running=True)
            except Exception as exc:
                logger.warning("Mirror startup push failed: %s", exc)
        # Bench harness hook: once the grid is interactive, trigger a close so
        # scripts/bench-close.py can time the teardown. Mirrors the user clicking
        # X (destroy() raises FormClosing → on_closing, like the real path).
        if os.environ.get("DUBIS_BENCH_CLOSE"):
            import threading
            import time as _t

            def _auto_close():
                out = os.environ.get("DUBIS_BENCH_OUT", "")
                deadline = _t.monotonic() + 30.0
                while _t.monotonic() < deadline:
                    try:
                        with open(out, encoding="utf-8") as f:
                            if "js_inventory_loaded" in f.read():
                                break
                    except OSError:
                        pass
                    _t.sleep(0.05)
                _t.sleep(0.3)  # let first render settle
                bench.mark("close_trigger")
                window.destroy()

            threading.Thread(target=_auto_close, name="bench-close", daemon=True).start()

    # Persist the WebView2 profile across launches. pywebview defaults to
    # private_mode=True with no storage_path, which makes it allocate a *fresh*
    # temp UserDataFolder every launch (winforms.init_storage) — so WebView2's
    # HTTP cache, V8 code cache and shader cache are thrown away each time and
    # every start is fully cold. Pinning a stable folder + private_mode=False
    # lets the runtime reuse those caches, cutting cold-start meaningfully.
    # The folder is a deletable cache (like cache.db); it lives under data/ and
    # is gitignored.
    webview2_profile = os.path.join(APP_DIR, "data", "webview2")
    # DUBIS_WEBVIEW_PROFILE=ephemeral restores pywebview's old fresh-temp-folder
    # behavior — used by scripts/bench-startup.py to A/B the persistent profile.
    persist_profile = os.environ.get("DUBIS_WEBVIEW_PROFILE") != "ephemeral"
    start_kwargs = {
        "func": on_ready,
        "debug": debug,
        "private_mode": not persist_profile,
    }
    if persist_profile:
        start_kwargs["storage_path"] = webview2_profile
    if sys.platform != "win32" and os.path.isfile(PNG_ICON_PATH):
        start_kwargs["icon"] = PNG_ICON_PATH
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
