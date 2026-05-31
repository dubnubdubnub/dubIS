#!/usr/bin/env python3
"""dubIS — desktop app entry point."""

import logging
import os
import sys

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
from inventory_api import InventoryApi
from pnp_server import start_pnp_server, stop_pnp_server
from poll_api import POLL_PORT, start_poll_server


def set_icon():
    """Set window icon via native WinForms API (pywebview 6.1 ignores the icon param on Windows)."""
    import time
    if sys.platform == "win32" and os.path.isfile(ICON_PATH):
        from System.Drawing import Icon as DrawingIcon
        from System import Action
        ico = DrawingIcon(ICON_PATH)
        for w in webview.windows:
            deadline = time.time() + 5.0
            while w.native is None or not w.native.IsHandleCreated:
                if time.time() > deadline:
                    logger.warning("set_icon: native window handle not ready after 5s; skipping icon")
                    break
                time.sleep(0.05)
            else:
                w.native.Invoke(Action(lambda: setattr(w.native, "Icon", ico)))


def main():
    debug = "--debug" in sys.argv
    api = InventoryApi(debug=debug)
    window = webview.create_window(
        "dubIS",
        url=os.path.join(APP_DIR, "index.html"),
        js_api=api,
        width=1600,
        height=900,
        min_size=(1200, 700),
    )

    pnp_server = None

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
        try:
            api.shutdown()
        except Exception as exc:
            logger.warning("Cleanup: api.shutdown failed: %s", exc)

    def on_closing():
        if api._force_close:
            _cleanup()
            os._exit(0)
        if not api._bom_dirty:
            _cleanup()
            os._exit(0)  # No unsaved changes — kill process immediately
        # Unsaved changes — show the confirmation modal
        try:
            window.evaluate_js("closeModal.open()")
        except Exception as exc:
            logger.warning("Could not show close modal: %s", exc)
            _cleanup()
            os._exit(0)
        return False

    def on_closed():
        _cleanup()
        os._exit(0)

    window.events.closing += on_closing
    window.events.closed += on_closed
    def on_ready():
        nonlocal pnp_server
        set_icon()
        pnp_server = start_pnp_server(api, window)
        prefs = api.load_preferences()
        configured_port = prefs.get("pollApiPort")
        start_poll_server(api, port=configured_port if configured_port else POLL_PORT)

    start_kwargs = {"func": on_ready, "debug": debug}
    if sys.platform != "win32" and os.path.isfile(PNG_ICON_PATH):
        start_kwargs["icon"] = PNG_ICON_PATH
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
