#!/usr/bin/env python3
"""dubIS — desktop app entry point."""

import os
import sys

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
    api = InventoryApi()
    window = webview.create_window(
        "dubIS",
        url=os.path.join(APP_DIR, "index.html"),
        js_api=api,
        width=1600,
        height=900,
        min_size=(1200, 700),
    )

    def on_closing():
        if api._force_close:
            return True
        if not api._bom_dirty:
            return True  # No unsaved changes — close immediately
        # Unsaved changes — show the confirmation modal
        try:
            window.evaluate_js("closeModal.open()")
        except Exception:
            return True  # Can't show modal — allow close as fallback
        return False

    window.events.closing += on_closing
    def on_closed():
        import time
        time.sleep(0.2)  # Let WebView2 finish teardown before force-exit
        os._exit(0)

    window.events.closed += on_closed
    start_kwargs = {"func": set_icon, "debug": "--debug" in sys.argv}
    if sys.platform != "win32" and os.path.isfile(PNG_ICON_PATH):
        start_kwargs["icon"] = PNG_ICON_PATH
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
