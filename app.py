#!/usr/bin/env python3
"""BOM Inventory Manager — desktop app entry point."""

import os
import sys

# Ensure the app directory is on the path
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import webview
from inventory_api import InventoryApi


def main():
    api = InventoryApi()
    window = webview.create_window(
        "BOM Inventory Manager",
        url=os.path.join(APP_DIR, "index.html"),
        js_api=api,
        width=1600,
        height=900,
        min_size=(1200, 700),
    )
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
