#!/usr/bin/env python3
"""dubIS — desktop app entry point."""

import logging
import os
import shutil
import subprocess
import sys
import threading

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

import webview_profile
from inventory_api import InventoryApi
from pnp_server import start_pnp_server, stop_pnp_server
from poll_api import POLL_PORT, start_poll_server

bench.mark("imports_done")

# Persistent WebView2 profile (see the storage_path block in main()). A hard-kill
# close can corrupt it; webview_profile self-heals via a launch sentinel + a
# startup watchdog so a corrupt profile never traps the user on the skeleton.
WEBVIEW_PROFILE_DIR = os.path.join(APP_DIR, "data", "webview2")
WEBVIEW_SENTINEL = os.path.join(APP_DIR, "data", webview_profile.SENTINEL_FILENAME)
# Seconds to wait for the JS bridge before assuming a corrupt profile and
# self-healing. A normal cold start reaches the bridge in ~1s; 15s is well clear
# of that while still recovering a hung launch quickly.
WEBVIEW_READY_TIMEOUT = 15.0


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
    window = webview.create_window(
        "dubIS",
        url=os.path.join(APP_DIR, "index.html"),
        js_api=api,
        width=1600,
        height=900,
        min_size=(1200, 700),
        background_color="#0d1117",  # match the dark theme so the shell doesn't flash white before first paint
    )

    pnp_server = None

    # DUBIS_WEBVIEW_PROFILE=ephemeral restores pywebview's old fresh-temp-folder
    # behavior — used by scripts/bench-startup.py to A/B the persistent profile.
    persist_profile = os.environ.get("DUBIS_WEBVIEW_PROFILE") != "ephemeral"
    # A self-heal relaunch sets this so a fresh profile that *still* hangs can't
    # trigger an endless relaunch loop (see the ready watchdog below).
    already_healed = os.environ.get("DUBIS_PROFILE_HEALED") == "1"
    # Set by the frontend (via api.notify_webview_ready) the moment the JS bridge
    # is live — proof the WebView2 profile loaded cleanly. The watchdog waits on it.
    ready_event = threading.Event()

    def _on_webview_ready():
        webview_profile.mark_ready(WEBVIEW_SENTINEL)
        ready_event.set()

    api._on_webview_ready = _on_webview_ready

    # If a sentinel from a prior launch is still present, that launch never
    # reached a live bridge — its profile is suspect, so wipe it before we open
    # the window. Writes a fresh sentinel that _on_webview_ready clears on success.
    if persist_profile:
        webview_profile.prepare_for_launch(WEBVIEW_PROFILE_DIR, WEBVIEW_SENTINEL)

    def _cleanup():
        """Best-effort teardown before os._exit. Order matters: stop the PnP
        server FIRST (no new requests; in-flight ones finish) so a mid-flight
        adjust_part can't write to a connection we're about to close, THEN
        commit+close the cache. Both steps log rather than raise so a cleanup
        failure can't block process exit. Idempotent — safe to call repeatedly
        (e.g. closing then closed both fire)."""
        try:
            stop_pnp_server(pnp_server)
        except Exception as exc:
            logger.warning("Cleanup: stopping PnP server failed: %s", exc)
        bench.mark("pnp_stopped")
        try:
            api.shutdown()
        except Exception as exc:
            logger.warning("Cleanup: api.shutdown failed: %s", exc)
        bench.mark("cache_closed")
        # The _hard_exit below TerminateProcesses *this* process only; WebView2's
        # child processes would otherwise survive as orphans, keeping the
        # persistent profile locked for the next launch. Reap them here so the
        # profile is always free (and stray msedgewebview2 processes don't pile up).
        webview_profile.kill_child_webview_processes(os.getpid())

    def on_closing():
        bench.mark("closing_enter")
        if api._force_close:
            _cleanup()
            bench.mark("pre_exit")
            _hard_exit(0)
        if not api._bom_dirty:
            _cleanup()
            bench.mark("pre_exit")
            _hard_exit(0)  # No unsaved changes — kill process immediately
        # Unsaved changes — show the confirmation modal
        try:
            window.evaluate_js("closeModal.open()")
        except Exception as exc:
            logger.warning("Could not show close modal: %s", exc)
            _cleanup()
            bench.mark("pre_exit")
            _hard_exit(0)
        return False

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
        pnp_server = start_pnp_server(api, window)
        # Expose the running server so api.start_scan_session() can mint sessions
        # on it (phone-scan transport). May be None if the port was unavailable.
        api._pnp_server = pnp_server
        prefs = api.load_preferences()
        configured_port = prefs.get("pollApiPort")
        start_poll_server(api, port=configured_port if configured_port else POLL_PORT)

        # Startup self-heal watchdog. The window is up, but a corrupt persistent
        # profile makes WebView2 hang loading the page so the JS bridge never
        # signals ready — trapping the user on the skeleton. If ready doesn't fire
        # within the timeout, wipe the profile and relaunch a fresh instance.
        if persist_profile and not already_healed:
            def _ready_watchdog():
                ready = ready_event.wait(WEBVIEW_READY_TIMEOUT)
                if not webview_profile.should_heal_and_relaunch(
                    ready=ready, persist_profile=persist_profile, already_healed=already_healed,
                ):
                    return
                logger.warning(
                    "JS bridge did not come up within %.0fs — WebView2 profile is "
                    "likely corrupt. Self-healing: wiping profile and relaunching.",
                    WEBVIEW_READY_TIMEOUT,
                )
                # Free the profile lock (our WebView2 children), wipe the corrupt
                # profile, then relaunch flagged so the fresh instance won't loop.
                webview_profile.kill_child_webview_processes(os.getpid())
                shutil.rmtree(WEBVIEW_PROFILE_DIR, ignore_errors=True)
                try:
                    subprocess.Popen(
                        [sys.executable, os.path.join(APP_DIR, "app.pyw")],
                        cwd=APP_DIR,
                        env=dict(os.environ, DUBIS_PROFILE_HEALED="1"),
                        close_fds=True,
                    )
                except OSError as exc:
                    logger.error("Self-heal relaunch failed: %s", exc)
                _hard_exit(0)

            threading.Thread(target=_ready_watchdog, name="webview-ready-watchdog", daemon=True).start()

        # Bench harness hook: once the grid is interactive, trigger a close so
        # scripts/bench-close.py can time the teardown. Mirrors the user clicking
        # X (destroy() raises FormClosing → on_closing, like the real path).
        if os.environ.get("DUBIS_BENCH_CLOSE"):
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
    # is gitignored. `persist_profile` is decided at the top of main() so the
    # self-heal setup can share it.
    start_kwargs = {
        "func": on_ready,
        "debug": debug,
        "private_mode": not persist_profile,
    }
    if persist_profile:
        start_kwargs["storage_path"] = WEBVIEW_PROFILE_DIR
    if sys.platform != "win32" and os.path.isfile(PNG_ICON_PATH):
        start_kwargs["icon"] = PNG_ICON_PATH
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
