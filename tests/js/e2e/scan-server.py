"""Standalone launcher for the phone-scan capture server used by the
capture-page Playwright E2E spec (scan-capture.spec.mjs).

Why this exists: the JS E2E harness serves static files only; it cannot serve
the Python-rendered /scan capture page or run the real upload route. To exercise
the REAL mobile capture page over HTTP (real navigation, real <input
type=file>, real submit → real POST → real OCR + UI push), we boot the actual
pnp_server here on an ephemeral port with a fake api/window, mint one scan
session, and print a machine-readable line the spec parses:

    READY port=<port> sid=<session_id>

The fake api records the OCR call and returns a canned line item; the fake
window records each evaluate_js push. After every upload the launcher writes the
recorded calls as JSON to the path given by --record so the spec can assert the
backend actually ran OCR and fired window._scanReceived. Prefer throwing over
silent failure: any setup error aborts the process with a non-zero exit.
"""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

# Make the repo root importable (this file lives in tests/js/e2e/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

import pnp_server  # noqa: E402
from pnp_server import create_scan_session, start_pnp_server  # noqa: E402


class _FakeApi:
    """Minimal api stand-in: records the OCR call, returns a canned line item."""

    def __init__(self):
        self.calls = []
        self.line_items = [
            {
                "mpn": "RC0402FR-0710KL",
                "manufacturer": "Yageo",
                "package": "0402",
                "quantity": 100,
                "unit_price": 0.01,
                "distributor": "LCSC",
                "distributor_pn": "C25744",
            },
        ]

    def parse_source_file_b64(self, file_b64, file_name, template="generic"):
        self.calls.append({"filename": file_name, "template": template,
                           "b64_len": len(file_b64)})
        return self.line_items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", default="lcsc")
    parser.add_argument("--record", required=True,
                        help="path to write recorded api/window calls as JSON")
    args = parser.parse_args()

    record_path = Path(args.record)
    api = _FakeApi()
    js_calls = []

    class _FakeWindow:
        def evaluate_js(self, code):
            js_calls.append(code)
            _dump()

    def _dump():
        # Atomic write: write to a temp file in the same dir then replace, so a
        # concurrent reader (the spec) never sees a truncated/partial file.
        tmp_path = record_path.with_name(record_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps({"ocr_calls": api.calls, "js_calls": js_calls}),
            encoding="utf-8",
        )
        os.replace(tmp_path, record_path)

    server = start_pnp_server(api, _FakeWindow(), port=0)
    if server is None:
        raise RuntimeError("start_pnp_server returned None — ephemeral port bind failed")

    port = server.server_address[1]
    sid = create_scan_session(server, args.template)
    _dump()  # write an initial (empty) record so the file always exists

    # Machine-readable handshake line the spec waits for.
    print(f"READY port={port} sid={sid}", flush=True)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        pnp_server.stop_pnp_server(server)


if __name__ == "__main__":
    main()
