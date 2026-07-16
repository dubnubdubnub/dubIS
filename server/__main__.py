"""Standalone entry point: run the /v1 server in the foreground.

Constructs a headless InventoryApi pointed at --data-dir exactly the way
tests/pnp-e2e/dubis_headless.py does (no webview, no PnP server — this is
just the /v1 HTTP API), then blocks running uvicorn in the foreground.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from inventory_api import InventoryApi
from server.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone dubIS /v1 server")
    parser.add_argument("--data-dir", default=".", help="Directory with CSV data")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=7891, help="Port to bind")
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir)

    api = InventoryApi()
    api.base_dir = data_dir
    api.input_csv = os.path.join(data_dir, "purchase_ledger.csv")
    api.output_csv = os.path.join(data_dir, "inventory.csv")
    api.adjustments_csv = os.path.join(data_dir, "adjustments.csv")
    api.prefs_json = os.path.join(data_dir, "preferences.json")

    config = uvicorn.Config(create_app(api), host=args.host, port=args.port,
                             log_level="info")
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
