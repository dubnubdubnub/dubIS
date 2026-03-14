#!/usr/bin/env python3
"""dubIS — desktop app entry point."""

import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

# ── Opt-in timing instrumentation (DUBIS_TIMING_LOG=/path/to/file) ──
_TIMING_LOG = os.environ.get("DUBIS_TIMING_LOG")
_T0 = time.perf_counter()


def _tlog(label):
    """Append a timestamped line to the timing log (no-op when disabled)."""
    if _TIMING_LOG:
        with open(_TIMING_LOG, "a") as f:
            f.write(f"{time.perf_counter() - _T0:.4f} {label}\n")
            f.flush()
            os.fsync(f.fileno())

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
from inventory_api import InventoryApi
from pnp_server import start_pnp_server


def _fast_exit():
    """Hide window then terminate process — instant close on Windows.

    TerminateProcess bypasses DLL detach / CLR finalizers entirely,
    avoiding the multi-second hang from .NET/WebView2 cleanup.
    Falls back to os._exit on non-Windows platforms.
    """
    _tlog("fast_exit")
    for w in webview.windows:
        try:
            w.hide()
        except Exception:
            pass
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0)
    else:
        os._exit(0)


def set_icon():
    """Set window icon via native WinForms API (pywebview 6.1 ignores the icon param on Windows)."""
    import time
    if sys.platform == "win32" and os.path.isfile(ICON_PATH):
        from System.Drawing import Icon as DrawingIcon
        from System import Action
        ico = DrawingIcon(ICON_PATH)
        for w in webview.windows:
            while w.native is None or not w.native.IsHandleCreated:
                time.sleep(0.05)
            w.native.Invoke(Action(lambda: setattr(w.native, "Icon", ico)))


def main():
    _tlog("main_start")
    debug = "--debug" in sys.argv
    test_mode = "--test-mode" in sys.argv
    api = InventoryApi(debug=debug)
    window = webview.create_window(
        "dubIS",
        url=os.path.join(APP_DIR, "index.html"),
        js_api=api,
        width=1600,
        height=900,
        min_size=(1200, 700),
    )
    _tlog("create_window_done")

    def on_closing():
        _tlog("on_closing")
        if api._force_close:
            _fast_exit()
        if not api._bom_dirty:
            _fast_exit()  # No unsaved changes — kill process immediately
        # Unsaved changes — show the confirmation modal
        try:
            window.evaluate_js("closeModal.open()")
        except Exception as exc:
            logger.warning("Could not show close modal: %s", exc)
            _fast_exit()
        return False

    window.events.closing += on_closing
    window.events.closed += lambda: _fast_exit()

    def on_ready():
        _tlog("on_ready")
        set_icon()
        start_pnp_server(api, window, test_mode=test_mode)

    start_kwargs = {"func": on_ready, "debug": debug, "private_mode": False}
    if sys.platform != "win32" and os.path.isfile(PNG_ICON_PATH):
        start_kwargs["icon"] = PNG_ICON_PATH
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
