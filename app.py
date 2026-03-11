#!/usr/bin/env python3
"""dubIS — desktop app entry point."""

import os
import sys

# Ensure the app directory is on the path
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(APP_DIR, "data", "dubIS.ico")
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
        window.evaluate_js("handleWindowClose()")
        return False

    window.events.closing += on_closing
    webview.start(func=set_icon, debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
