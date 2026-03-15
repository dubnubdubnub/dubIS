"""Headless dubIS — starts InventoryApi + PnP server without the GUI.

Copies real fixture data into a temp directory, seeds inventory, and
starts the PnP consumption server on localhost:7890.
"""

import argparse
import json
import os
import shutil
import signal
import sys
import types

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from inventory_api import InventoryApi
from pnp_server import start_pnp_server


def main():
    parser = argparse.ArgumentParser(description="Headless dubIS server for E2E tests")
    parser.add_argument("--data-dir", required=True, help="Directory with CSV fixture data")
    parser.add_argument("--port", type=int, default=7890, help="PnP server port")
    parser.add_argument("--part-map", default=None, help="Path to pnp_part_map.json")
    parser.add_argument("--test-source", default="", help="Source tag for all adjustments (e.g. test:session-1)")
    parser.add_argument("--rollback-on-exit", action="store_true", help="Roll back all adjustments with --test-source on shutdown")
    args = parser.parse_args()

    if args.rollback_on_exit and not args.test_source:
        parser.error("--rollback-on-exit requires --test-source")

    data_dir = os.path.abspath(args.data_dir)

    # Set up InventoryApi pointing at the data directory
    api = InventoryApi()
    api.base_dir = data_dir
    api.input_csv = os.path.join(data_dir, "purchase_ledger.csv")
    api.output_csv = os.path.join(data_dir, "inventory.csv")
    api.adjustments_csv = os.path.join(data_dir, "adjustments.csv")
    api.prefs_json = os.path.join(data_dir, "preferences.json")

    # Copy part map if provided
    if args.part_map:
        dest = os.path.join(data_dir, "pnp_part_map.json")
        shutil.copy2(args.part_map, dest)

    # Rebuild inventory from fixtures
    print(f"[dubis] Rebuilding inventory from {data_dir}")
    inv = api.rebuild_inventory()
    print(f"[dubis] Inventory rebuilt: {len(inv)} parts")

    # Start PnP server with a no-op window
    source = args.test_source or "openpnp"
    mock_window = types.SimpleNamespace(evaluate_js=lambda code: None)
    server = start_pnp_server(api, mock_window, port=args.port, source=source)
    print(f"[dubis] PnP server listening on port {args.port} (source={source!r})")

    # Write a ready marker file so the orchestrator knows we're up
    ready_path = os.path.join(data_dir, ".dubis-ready")
    with open(ready_path, "w") as f:
        f.write(str(args.port))

    # Block until SIGTERM/SIGINT
    def shutdown(signum, frame):
        print(f"[dubis] Shutting down (signal {signum})")
        if args.rollback_on_exit and args.test_source:
            removed = api.rollback_source(args.test_source)
            print(f"[dubis] Rolled back {len(removed)} test adjustment(s) with source={args.test_source!r}")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Keep main thread alive
    import threading
    stop = threading.Event()
    try:
        stop.wait()
    except KeyboardInterrupt:
        shutdown(2, None)


if __name__ == "__main__":
    main()
