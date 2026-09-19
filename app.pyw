#!/usr/bin/env python3
"""dubIS — desktop app entry point."""

import logging
import os
import shutil
import subprocess
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
import app_launch
import app_restart
import webview_profile

# Seconds to wait for the JS bridge before assuming a corrupt WebView2 profile
# and self-healing (see webview_profile.py). A normal cold start reaches the
# bridge in ~1s; 15s is well clear of that while still recovering a hung launch
# quickly.
WEBVIEW_READY_TIMEOUT = 15.0

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


class Launcher:
    """Owns the full app.pyw boot/close lifecycle as instance state instead
    of the closures this replaces. `main()` below is a thin `Launcher().run()`.

    `run()` answers exactly one question — does this process own the data dir,
    or attach to the dubIS that already does — and is otherwise a straight line:
    pick a port, (own only) start the /v1 server on a background thread, open
    the window on the local splash. That question is about the DATA DIR, never
    about which server's data the user is looking at: the local server is a hub,
    and another dubIS is a source it fetches from rather than somewhere this
    window navigates (docs/plans/2026-09-19-multi-server-hub-design.md). An
    attached window owns nothing — no server, no lock, no PnP port, no profile
    — and `self.attached` gates every teardown step accordingly. Two lifecycle
    traps documented in CLAUDE.md make the ordering load-bearing and must not be
    disturbed by future edits to this class:

    1. pywebview second-origin navigation race: don't change WHEN/WHERE the
       window is created or navigated. splash.html self-navigates only after
       its own /v1/health poll succeeds — app.pyw never touches the window
       object from the boot thread. See the comment block in `run()`, before
       `webview.create_window`, for the full history. There is now only ever
       one origin in play, which is what puts this race out of reach; keep it
       that way.
    2. Close deadlock: `on_closing` runs synchronously on the WinForms UI
       thread (pywebview's `closing` event is should_lock=True) — never call
       the blocking `window.evaluate_js()` from there directly; that's why
       `open_modal` below is a lambda handed to `handle_closing()`, which
       decides whether to invoke it, rather than an eager call.
    """

    def __init__(self):
        self.debug = "--debug" in sys.argv
        self.api = None
        # The URL (if any) the hub should start ACTIVE on — a data-source
        # choice handed to the server at boot, not a mode this class branches
        # on. None means "start on local data".
        self.initial_source_url = None
        # True when another live dubIS owns this data dir and is serving this
        # window (see app_launch's "second launch" section). An attached window
        # owns no server, no lock, no PnP port and no data — it is a second view
        # onto someone else's hub, and every teardown step below is gated on it.
        self.attached = False
        self.shell = None
        # Populated by _boot_server() once the /v1 server thread starts it;
        # None until then (mirrors the old server_state dict's initial state).
        self.server = None
        self.stop_server_fn = None
        self.port = None
        self.window = None
        self.pnp_server = None
        self.webview2_profile = None
        self.persist_profile = None
        self.webview_sentinel = None
        self.already_healed = False
        # Set by the frontend (api.notify_webview_ready, fired once
        # whenPywebviewReady resolves) the moment the JS bridge is live —
        # proof the profile loaded cleanly.
        self.ready_event = threading.Event()

    def run(self):
        api = InventoryApi(debug=self.debug)
        self.api = api
        bench.mark("api_constructed")

        # Which dubIS server's data the user wants to see first (DUBIS_URL env,
        # else preferences.json's server_url, else local — precedence unchanged,
        # see remote_mode.py). This is NO LONGER a boot decision: the local /v1
        # server is the hub and always boots, and this URL is handed to it in
        # _boot_server() as the source to start ACTIVE on. The user can switch
        # sources at runtime from there, which is the whole point — a resolved
        # URL used to mean "don't boot a server, navigate elsewhere", and that is
        # what made changing servers require a restart.
        #
        # load_preferences() is a single small JSON read; InventoryApi.__init__
        # above does no I/O beyond building path strings (cache.db is opened
        # lazily by _get_cache()), so neither costs anything on the path to first
        # paint.
        self.initial_source_url = resolve_remote_base_url(os.environ, api.load_preferences())

        shell = ClientShell(api)
        self.shell = shell

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
        # ── own the hub, or attach to one that's already running ────────────
        # Decided HERE, on the main thread, before the window exists — because
        # the splash URL bakes in the port this window will poll, and an
        # attached window polls the OWNER's port. Discovering the collision
        # later (on the boot thread, where the lock is actually taken) would
        # leave the window polling a port nothing will ever answer, and the only
        # fix from there would be re-navigating the window from Python, which is
        # precisely the bridge-corrupting move CLAUDE.md forbids.
        #
        # The cost on the path to first paint is one lock acquire+release —
        # microseconds, stdlib-only, no uvicorn import — and a health GET only
        # when a live owner actually exists. A normal single-instance launch
        # does no I/O beyond that probe.
        plan = app_launch.resolve_launch(self.api.base_dir)
        self.attached = plan.mode == app_launch.ATTACH

        if plan.mode == app_launch.BROKEN:
            # The lock is held but nothing is serving. There is no window worth
            # opening: it could only poll a dead port and time out 15s later
            # behind this dialog. Say what's wrong and stop.
            logger.error("Launch aborted: %s", plan.message)
            _show_error_dialog("dubIS is not responding", plan.message)
            _hard_exit(1)

        if self.attached:
            # Someone else's hub, someone else's port. No boot thread, no lock,
            # no port file — this process starts nothing it would have to stop.
            self.port = plan.port
            logger.info(
                "Attaching to the dubIS instance already serving this data "
                "directory (pid=%s, port=%s).", plan.owner_pid, plan.port,
            )
        else:
            self.port = int(os.environ.get("DUBIS_SERVER_PORT", 0)) or _free_port()
            # The local server is the hub, so it boots on every launch that owns
            # the data dir. There is no second ORIGIN to navigate to either way —
            # a remote dubIS is a source the hub fetches on the page's behalf,
            # not a place this window goes, which is what keeps the pywebview
            # second-origin navigation race (CLAUDE.md, splash.html) out of reach
            # by construction rather than by timing. Bearer tokens for remote
            # sources (DUBIS_TOKEN) belong to the hub's outbound HTTP, never to
            # this navigation.
            threading.Thread(target=self._boot_server, name="dubis-server-boot", daemon=True).start()

        # Same splash either way, pointed at whichever loopback hub serves this
        # window. It is still exactly one navigation, to one origin.
        splash_url = app_launch.splash_url(SPLASH_PATH, self.port)

        window = webview.create_window(
            "dubIS",
            url=splash_url,
            js_api=shell,
            width=1600,
            height=900,
            min_size=(1200, 700),
            background_color="#0d1117",  # match the dark theme so the shell doesn't flash white before first paint
        )
        self.window = window
        shell._window = window

        self.pnp_server = None

        # ── WebView2 persistent-profile self-heal (see webview_profile.py) ───────
        # The persistent profile (webview2_profile below, PR #296) caches WebView2's
        # HTTP/V8/shader stores for a faster cold start, but the hard-kill close
        # (TerminateProcess, PR #298) never lets WebView2 flush its on-disk LevelDB.
        # Enough unclean shutdowns corrupt the profile, and a corrupt profile makes
        # WebView2 hang during page init: the window paints the splash but the JS
        # bridge never comes up, trapping the user there. A launch sentinel +
        # startup watchdog make that non-fatal. `persist_profile` is decided here so
        # the self-heal setup, on_ready's watchdog, and webview.start() all share it.
        self.webview2_profile = os.path.join(APP_DIR, "data", "webview2")
        # The persistent profile is single-owner for the same reason the data dir
        # is: it's one on-disk store, WebView2 locks its UserDataFolder per
        # process, and the self-heal path here DELETES it wholesale. An attached
        # window sharing it could wipe the owner's live profile mid-session (via
        # prepare_for_launch seeing the owner's in-flight sentinel, or via
        # _ready_watchdog's rmtree). So an attached window runs ephemeral: a
        # colder start for the second window, and no way for it to corrupt the
        # first. This also disables the sentinel + self-heal watchdog below,
        # both of which are gated on persist_profile — correctly, since neither
        # is this window's business.
        self.persist_profile = (
            not self.attached and os.environ.get("DUBIS_WEBVIEW_PROFILE") != "ephemeral"
        )
        self.webview_sentinel = os.path.join(APP_DIR, "data", webview_profile.SENTINEL_FILENAME)
        # A self-heal relaunch sets DUBIS_PROFILE_HEALED=1 so a fresh profile that
        # STILL hangs can't trigger an endless relaunch loop.
        self.already_healed = os.environ.get("DUBIS_PROFILE_HEALED") == "1"

        api._on_webview_ready = self._on_webview_ready

        # A leftover sentinel means the previous launch never reached a live bridge —
        # its profile is suspect, so wipe it before opening the window. Writes a fresh
        # sentinel that _on_webview_ready clears on success.
        if self.persist_profile:
            webview_profile.prepare_for_launch(self.webview2_profile, self.webview_sentinel)

        window.events.closing += self.on_closing
        window.events.closed += self.on_closed

        # Persist the WebView2 profile across launches. pywebview defaults to
        # private_mode=True with no storage_path, which makes it allocate a *fresh*
        # temp UserDataFolder every launch (winforms.init_storage) — so WebView2's
        # HTTP cache, V8 code cache and shader cache are thrown away each time and
        # every start is fully cold. Pinning a stable folder + private_mode=False
        # lets the runtime reuse those caches, cutting cold-start meaningfully.
        # The folder is a deletable cache (like cache.db); it lives under data/ and
        # is gitignored. `webview2_profile` / `persist_profile` are set above
        # (shared with the self-heal setup). DUBIS_WEBVIEW_PROFILE=ephemeral
        # restores pywebview's fresh-temp-folder behavior (scripts/bench-startup.py).
        start_kwargs = {
            "func": self.on_ready,
            "debug": self.debug,
            "private_mode": not self.persist_profile,
        }
        if self.persist_profile:
            start_kwargs["storage_path"] = self.webview2_profile
        if sys.platform != "win32" and os.path.isfile(PNG_ICON_PATH):
            start_kwargs["icon"] = PNG_ICON_PATH
        webview.start(**start_kwargs)

    def _boot_server(self) -> None:
        """Background thread: import + start the /v1 server on the
        already-chosen `self.port`. Started before create_window (see
        run()) so the uvicorn/fastapi import cost never sits on the path to
        first paint. Never touches the window — splash.html's own script
        (started by pywebview once it renders) polls /v1/health and
        navigates itself; this thread's only job is getting uvicorn
        listening.

        The whole body is wrapped in try/except so an exception anywhere in
        here (import failure, port conflict, etc.) is logged instead of
        silently killing this daemon thread with nothing in the log. The
        splash's own 15s health-poll timeout already handles the user-facing
        UX for a boot failure; this only closes the silent-log gap."""
        try:
            from server.run import start_server, stop_server  # deferred: heavy import

            # Hand the hub the source the user asked for BEFORE the app is
            # built, so it comes up with that source already active instead of
            # flashing local data and switching. See app_launch for the seam's
            # contract; a URL that can't be seeded is loud but not fatal, since
            # the hub is perfectly usable on local data and dying here would
            # only leave the user on a splash that times out.
            seeded = app_launch.seed_initial_active_source(self.initial_source_url)
            if seeded == "unavailable":
                logger.error(
                    "Cannot start on %s: the hub's source registry (%s) is missing. "
                    "Starting on local data instead.",
                    self.initial_source_url, app_launch.ACTIVE_SOURCE_SEAM,
                )

            self.stop_server_fn = stop_server
            v1_server = start_server(self.api, static_dir=APP_DIR, port=self.port, data_dir=self.api.base_dir)
            self.server = v1_server
            bench.mark("server_starting")

            start_deadline = time.monotonic() + 15
            while not v1_server.started and time.monotonic() < start_deadline:
                time.sleep(0.02)
            if not v1_server.started:
                # Loud, not silent: splash.html's own poll will also time out
                # (15s) and render its inline error state, but log here too since
                # that's the actionable signal for whoever's watching the logs.
                logger.error(
                    "/v1 loopback server did not start within 15s on port %s", self.port
                )
                return
            bench.mark("server_started")
        except DataDirLockedError as e:
            # Reaching here now means we LOST A RACE, not that a second window
            # is forbidden: run() probed the lock before creating this window,
            # found it free, and another dubIS took it in the milliseconds
            # between that probe and start_server's real acquire. (The ordinary
            # "someone else is already running" case never gets here — run()
            # turns it into an attached window.) There is no recovery from this
            # point: the window exists and is polling a port we'll never bind,
            # and re-pointing it from this thread is the bridge-corrupting move
            # CLAUDE.md forbids. So: say what happened, plainly.
            logger.error("v1 server boot failed: %s", e, exc_info=True)
            _show_error_dialog(
                "dubIS is already running",
                f"Another dubIS instance (pid={e.pid}, port={e.port}) claimed "
                f"this data directory a moment after this one started.\n\n"
                f"Close this window and open dubIS again — it will open as a "
                f"second window onto the instance that's already running.",
            )
        except Exception as e:
            logger.error("v1 server boot failed: %s", e, exc_info=True)

    def _on_webview_ready(self):
        webview_profile.mark_ready(self.webview_sentinel)
        self.ready_event.set()

    def _cleanup(self):
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
        then closed both fire); a second call finds no server on self.server
        (or an already-released LockHandle, itself idempotent) and no-ops."""
        # An attached window owns no server, no lock, no PnP port and no data:
        # it started none of them and must stop none of them, or closing the
        # second window would tear the hub out from under the first. The
        # stop_server/lock steps below are already no-ops in that case (both are
        # None — nothing ever set them), but the mirror push and api.shutdown()
        # would NOT be: they'd write on behalf of a process that never owned the
        # data dir. Return before either.
        if self.attached:
            try:
                stop_pnp_server(self.pnp_server)  # None here; kept for symmetry
            except Exception as exc:
                logger.warning("Cleanup: stopping PnP server failed: %s", exc)
            webview_profile.kill_child_webview_processes(os.getpid())
            bench.mark("cache_closed")
            return

        # Mirror: push current inventory on shutdown (no-op unless enabled).
        # Ungated: this process always owns local CSVs now, and MirrorController
        # already gates on the `mirror_enabled` preference. The old extra gate
        # was also half a gate — inventory_api's on_inventory_changed push never
        # had one — so a session could stream live updates and then never send
        # the dubis_running=False marker that closes them out.
        try:
            self.api._mirror_ctl.push_event(self.api._load_organized(), dubis_running=False, block=True)
        except Exception as exc:
            logger.warning("Mirror shutdown push failed: %s", exc)
        try:
            stop_pnp_server(self.pnp_server)
        except Exception as exc:
            logger.warning("Cleanup: stopping PnP server failed: %s", exc)
        bench.mark("pnp_stopped")
        v1_server = self.server
        lock = getattr(v1_server, "_dubis_lock", None) if v1_server is not None else None
        try:
            stop_server = self.stop_server_fn
            if v1_server is not None and stop_server is not None:
                stop_server(v1_server, data_dir=self.api.base_dir, release_lock=False)
        except Exception as exc:
            logger.warning("Cleanup: stopping /v1 server failed: %s", exc)
        try:
            self.api.shutdown()
        except Exception as exc:
            logger.warning("Cleanup: api.shutdown failed: %s", exc)
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception as exc:
                    logger.warning("Cleanup: releasing data-dir lock failed: %s", exc)
        # Reap our WebView2 child processes: the _hard_exit below TerminateProcesses
        # only THIS process, so msedgewebview2 children would otherwise survive as
        # orphans that keep the persistent profile locked for the next launch.
        webview_profile.kill_child_webview_processes(os.getpid())
        bench.mark("cache_closed")

    def _spawn_replacement(self):
        """Start a fresh instance. Called from _do_exit AFTER _cleanup().

        Ordering is the whole point: _cleanup() releases the data-dir lock last
        precisely so a second instance cannot start writing while this one
        closes, and it also reaps our WebView2 children so the persistent
        profile is unlocked. Spawning any earlier hands the replacement a lock
        and a profile we still hold.

        Failure here is logged and swallowed: we are mid-exit and the user asked
        to restart, so the worst outcome is the app closing without coming back
        — recoverable by launching it again — whereas raising would leave the
        process wedged in teardown.
        """
        try:
            import subprocess
            cmd = app_restart.relaunch_argv(sys.executable, sys.argv)
            env = app_restart.relaunch_env(os.environ)
            subprocess.Popen(cmd, env=env, cwd=os.getcwd(), **app_restart.spawn_kwargs())
            logger.info("Restart: spawned replacement: %s", " ".join(cmd))
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.error("Restart: failed to spawn replacement: %s", exc)

    def _do_exit(self):
        restarting = bool(self.api and getattr(self.api, "_restart_pending", False))
        self._cleanup()
        if restarting:
            self._spawn_replacement()
        bench.mark("pre_exit")
        _hard_exit(0)  # kill process immediately

    def on_closing(self):
        # NOTE: this runs synchronously on the WinForms UI thread (pywebview's
        # `closing` event is should_lock=True). handle_closing() must therefore
        # never call the blocking window.evaluate_js() on this thread — doing so
        # deadlocks the message pump against evaluate_js's completion callback
        # ("Not Responding", window won't close). See window_close.py.
        bench.mark("closing_enter")
        return handle_closing(
            force_close=self.api._force_close,
            bom_dirty=self.api._bom_dirty,
            open_modal=lambda: self.window.evaluate_js("closeModal.open()"),
            do_exit=self._do_exit,
        )

    def on_closed(self):
        bench.mark("closed_enter")
        self._cleanup()
        bench.mark("pre_exit")
        _hard_exit(0)

    def on_ready(self):
        bench.mark("on_ready")  # native window shown; WebView2 runtime up
        set_icon()
        # PnP server + mirror push belong to whichever process owns the data
        # dir. For an owner they're unconditional — they were skipped when a
        # launch spawned no local server, and the hub is always local now, so
        # both always apply, and neither needs to know which source is active:
        # the PnP machine and the mirror daemon talk to `self.api` (this
        # process's CSVs/cache), which is exactly the hub's own "local" source.
        #
        # An attached window starts neither. PnP's port is machine-wide (one
        # OpenPnP, one dubIS to feed it) and the owner holds it — start_pnp_server
        # would log a port-in-use warning and return None, which is merely
        # harmless rather than correct. The mirror is the data dir's snapshot,
        # and this process doesn't own the data dir.
        if self.attached:
            threading.Thread(
                target=self._watch_owner, name="dubis-owner-watch", daemon=True,
            ).start()
        else:
            self.pnp_server = start_pnp_server(self.api)
            # Expose the running server so api.start_scan_session() can mint
            # sessions on it (phone-scan transport). May be None if the port
            # was unavailable — start_pnp_server already logs and returns None
            # rather than raising when another instance holds the port.
            self.api._pnp_server = self.pnp_server
            # Mirror: push current inventory on startup (no-op unless enabled).
            try:
                self.api._mirror_ctl.push_event(self.api._load_organized(), dubis_running=True)
            except Exception as exc:
                logger.warning("Mirror startup push failed: %s", exc)

        # Startup self-heal watchdog. The window is up, but a corrupt persistent
        # profile makes WebView2 hang loading the page so the JS bridge never
        # signals ready — trapping the user on the splash. If ready doesn't fire
        # within the timeout, wipe the profile and relaunch a fresh instance
        # (DUBIS_PROFILE_HEALED guards against a relaunch loop).
        if self.persist_profile and not self.already_healed:
            threading.Thread(target=self._ready_watchdog, name="webview-ready-watchdog", daemon=True).start()
        # Bench harness hook: once the grid is interactive, trigger a close so
        # scripts/bench-close.py can time the teardown. Mirrors the user clicking
        # X (destroy() raises FormClosing → on_closing, like the real path).
        if os.environ.get("DUBIS_BENCH_CLOSE"):
            threading.Thread(target=self._bench_auto_close, name="bench-close", daemon=True).start()

    def _watch_owner(self):
        """Attached windows only: notice when the hub serving us exits.

        The alternative is a window that looks fine and fails every action with
        a 500 — the page itself was served by a process that is now gone, so
        nothing in it can work and nothing in it can say why. This says why.

        Deliberately NOT a reconnect or failover: there is nothing to fail over
        to. The hub held the data-dir lock; when it exits, the honest options
        are "this window is dead, open dubIS again" (which relaunches as a fresh
        owner) or a silent lie. The window is left open rather than closed from
        under the user — they may want to read what's on screen — and the dialog
        is system-modal, so it can't be missed.
        """
        failures = 0
        while True:
            time.sleep(app_launch.OWNER_POLL_SECONDS)
            if app_launch.hub_responds(self.port):
                failures = 0
                continue
            failures += 1
            if not app_launch.owner_has_exited(failures):
                continue
            logger.error(
                "The dubIS instance serving this window (port %s) stopped "
                "answering; this window is now disconnected.", self.port,
            )
            _show_error_dialog(app_launch.OWNER_GONE_TITLE, app_launch.OWNER_GONE_MESSAGE)
            return

    def _ready_watchdog(self):
        ready = self.ready_event.wait(WEBVIEW_READY_TIMEOUT)
        if not webview_profile.should_heal_and_relaunch(
            ready=ready, persist_profile=self.persist_profile, already_healed=self.already_healed,
        ):
            return
        logger.warning(
            "JS bridge did not come up within %.0fs — WebView2 profile is "
            "likely corrupt. Self-healing: wiping profile and relaunching.",
            WEBVIEW_READY_TIMEOUT,
        )
        # Release the data-dir lock and stop the /v1 server + PnP FIRST
        # (via _cleanup, which also reaps our WebView2 children so the
        # profile is unlocked) — otherwise the fresh instance would hit
        # DataDirLockedError. Then wipe the corrupt profile and relaunch
        # flagged so the fresh instance can't loop.
        self._cleanup()
        shutil.rmtree(self.webview2_profile, ignore_errors=True)
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

    def _bench_auto_close(self):
        out = os.environ.get("DUBIS_BENCH_OUT", "")
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                with open(out, encoding="utf-8") as f:
                    if "js_inventory_loaded" in f.read():
                        break
            except OSError:
                pass
            time.sleep(0.05)
        time.sleep(0.3)  # let first render settle
        bench.mark("close_trigger")
        self.window.destroy()


def main():
    Launcher().run()


if __name__ == "__main__":
    main()
