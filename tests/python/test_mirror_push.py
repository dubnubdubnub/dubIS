import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mirror_push import MirrorController, build_payload, push_snapshot


def test_build_payload_shape():
    p = build_payload([{"lcsc": "C1"}], dubis_running=True, token="t", now_iso="T")
    assert p["inventory"] == [{"lcsc": "C1"}]
    assert p["source"] == "dubis" and p["dubis_running"] is True
    assert p["token"] == "t" and p["pushed_at"] == "T"
    assert isinstance(p["csv_fields"], list) and "lcsc" in p["csv_fields"]


def test_push_snapshot_returns_false_on_refused():
    # Nothing listening on this port → must return False, not raise.
    assert push_snapshot({"token": "t"}, port=9, timeout=0.5) is False


@pytest.fixture
def capture_server():
    received = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            received.append(json.loads(self.rfile.read(n)))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield received, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_push_snapshot_posts_payload(capture_server):
    received, port = capture_server
    assert push_snapshot({"token": "t", "x": 1}, port=port, timeout=3) is True
    assert received[0]["x"] == 1


def test_controller_skips_when_disabled(capture_server):
    received, port = capture_server
    ctrl = MirrorController(is_enabled=lambda: False, read_token=lambda: "t", port=port)
    ctrl.push_event([{"lcsc": "C1"}], dubis_running=True, block=True)
    assert received == []


def test_controller_pushes_when_enabled(capture_server):
    received, port = capture_server
    ctrl = MirrorController(is_enabled=lambda: True, read_token=lambda: "t", port=port)
    ctrl.push_event([{"lcsc": "C1"}], dubis_running=False, block=True)
    assert received[0]["dubis_running"] is False
    assert received[0]["inventory"] == [{"lcsc": "C1"}]
