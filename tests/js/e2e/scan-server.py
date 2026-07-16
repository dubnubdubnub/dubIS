"""Standalone launcher for the phone-scan capture server used by the
capture-page Playwright E2E spec (scan-capture.spec.mjs).

Why this exists: the JS E2E harness serves static files only; it cannot serve
the Python-rendered /scan capture page or run the real upload route. To exercise
the REAL mobile capture page over HTTP (real navigation, real <input
type=file>, real submit → real POST → real OCR + UI push), we boot the actual
pnp_server here on an ephemeral port with a fake api, mint one scan session,
and print a machine-readable line the spec parses:

    READY port=<port> sid=<session_id>

The fake api records the OCR call and returns a canned line item. Phase 1b
Task 10 deleted pnp_server.py's window.evaluate_js UI pushes entirely — SSE
(server/events.py) is now the sole push mechanism, so this launcher subscribes
to the SSE broker instead of injecting a fake window. After every upload the
launcher writes the recorded OCR calls + SSE event names as JSON to the path
given by --record so the spec can assert the backend actually ran OCR and
published scan.receiving/scan.received. Prefer throwing over silent failure:
any setup error aborts the process with a non-zero exit.
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# Make the repo root importable (this file lives in tests/js/e2e/).
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

import pnp_server  # noqa: E402
from pnp_server import create_scan_session, start_pnp_server  # noqa: E402
from server import events as sse_events  # noqa: E402


class _FakeApi:
    """Minimal api stand-in: records the OCR call, returns a canned line item."""

    def __init__(self, base_dir):
        # The real upload handler saves the raw photo to <base_dir>/scans before
        # OCR; the spec asserts a file lands there.
        self.base_dir = base_dir
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

    def ocr_overlay_b64(self, file_b64, file_name, template="generic"):
        # The phone upload handler now calls this (one OCR pass producing the
        # overlay payload). Record the call like the old OCR path and return a
        # canned overlay so the capture-page E2E still exercises the real route.
        self.calls.append({"filename": file_name, "template": template,
                           "b64_len": len(file_b64)})
        return {
            "pages": [{
                "image_b64": "AAAA", "width": 10, "height": 10,
                "words": [
                    {"text": "C25744", "x": 1, "y": 1, "w": 4, "h": 2,
                     "conf": 95, "line_id": 0},
                    {"text": "RC0402", "x": 1, "y": 5, "w": 5, "h": 2,
                     "conf": 90, "line_id": 1},
                ],
                "lines": [],
            }],
            "prefill_rows": self.line_items,
            "template": template,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", default="lcsc")
    parser.add_argument("--record", required=True,
                        help="path to write recorded api calls + SSE events as JSON")
    parser.add_argument("--data-dir",
                        help="base dir uploads are saved under (<dir>/scans); "
                             "defaults to a fresh temp dir reported in READY")
    args = parser.parse_args()

    record_path = Path(args.record)
    data_dir = args.data_dir or tempfile.mkdtemp(prefix="dubis-scan-data-")
    os.makedirs(data_dir, exist_ok=True)
    api = _FakeApi(data_dir)
    # SSE (server/events.py) is the sole UI-push mechanism since Phase 1b Task 10
    # deleted pnp_server.py's window.evaluate_js pushes. Subscribe before the
    # server starts so no early scan.receiving/scan.received event is missed.
    sse_names = []
    sse_queue = sse_events.subscribe()

    def _dump():
        # Atomic write: write to a temp file in the same dir then replace, so a
        # concurrent reader (the spec) never sees a truncated/partial file.
        tmp_path = record_path.with_name(record_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps({"ocr_calls": api.calls, "sse_events": sse_names}),
            encoding="utf-8",
        )
        os.replace(tmp_path, record_path)

    def _drain_sse_loop():
        while True:
            name, _data = sse_queue.get()
            sse_names.append(name)
            _dump()

    threading.Thread(target=_drain_sse_loop, daemon=True).start()

    server = start_pnp_server(api, port=0)
    if server is None:
        raise RuntimeError("start_pnp_server returned None — ephemeral port bind failed")

    port = server.server_address[1]
    sid = create_scan_session(server, args.template)
    _dump()  # write an initial (empty) record so the file always exists

    # Machine-readable handshake line the spec waits for.
    print(f"READY port={port} sid={sid} data_dir={data_dir}", flush=True)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        sse_events.unsubscribe(sse_queue)
        pnp_server.stop_pnp_server(server)


if __name__ == "__main__":
    main()
