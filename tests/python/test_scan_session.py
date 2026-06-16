"""Tests for the phone-scan transport: pnp_server scan routes +
inventory_api.start_scan_session."""

import base64
import json
import os
import socket
import types
import urllib.error
import urllib.request

import pytest

import pnp_server
from pnp_server import start_pnp_server, stop_pnp_server

# A tiny 1x1 PNG so uploads carry a real (valid-extension) image payload.
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWg"
    "mWQ0AAAAASUVORK5CYII="
)


# ── HTTP helpers ──


def _get(url):
    """GET → (status, bytes, content_type)."""
    try:
        resp = urllib.request.urlopen(url)
        return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


def _post_json(url, data):
    """POST JSON → (status, parsed_json)."""
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ── Fixtures ──


class _FakeApi:
    """Minimal api stand-in: records the OCR call, returns a canned overlay."""

    def __init__(self, base_dir, prefill_rows=None):
        self.base_dir = base_dir
        self.calls = []
        self._prefill_rows = prefill_rows if prefill_rows is not None else [
            {"mpn": "RC0402FR-0710KL", "manufacturer": "Yageo", "package": "0402",
             "quantity": 100, "unit_price": 0.01, "distributor": "LCSC",
             "distributor_pn": "C25744"},
        ]
        self._pages = [
            {"image_b64": "AAAA", "width": 10, "height": 10, "words": [], "lines": []},
        ]

    def ocr_overlay_b64(self, file_b64, file_name, template="generic"):
        self.calls.append((file_b64, file_name, template))
        return {
            "pages": self._pages,
            "prefill_rows": self._prefill_rows,
            "template": template,
        }


@pytest.fixture
def scan_server(tmp_path):
    """A running pnp server backed by a fake api + window capturing evaluate_js."""
    api = _FakeApi(str(tmp_path))
    js_calls = []
    window = types.SimpleNamespace(evaluate_js=lambda code: js_calls.append(code))
    server = start_pnp_server(api, window, port=0)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    yield types.SimpleNamespace(
        server=server, base_url=base_url, api=api, window=window, js_calls=js_calls,
    )
    stop_pnp_server(server)


# ── Scan session registry ──


class TestScanSessionRegistry:
    def test_create_and_lookup(self):
        srv = types.SimpleNamespace()
        sid = pnp_server.create_scan_session(srv, "lcsc")
        assert sid
        assert pnp_server._get_scan_session(srv, sid)["template"] == "lcsc"

    def test_unknown_session_is_none(self):
        srv = types.SimpleNamespace()
        assert pnp_server._get_scan_session(srv, "nope") is None

    def test_expired_session_pruned(self):
        srv = types.SimpleNamespace()
        sid = pnp_server.create_scan_session(srv, "generic")
        # Backdate creation beyond the TTL.
        srv._scan_sessions[sid]["created"] -= pnp_server.SCAN_SESSION_TTL + 1
        assert pnp_server._get_scan_session(srv, sid) is None
        assert sid not in srv._scan_sessions


# ── GET routes ──


class TestScanHealth:
    def test_scan_health_ok(self, scan_server):
        status, raw, _ = _get(f"{scan_server.base_url}/api/scan/health")
        assert status == 200
        assert json.loads(raw) == {"ok": True}


class TestServerConcurrency:
    """The server must handle connections concurrently. iOS Safari opens
    speculative preconnect sockets that send no request; on a single-threaded
    server one such idle socket head-of-line blocks the page load for seconds
    (or, with no read timeout, indefinitely)."""

    def test_idle_connection_does_not_block_other_requests(self, scan_server):
        host = "127.0.0.1"
        port = scan_server.server.server_address[1]

        # Open a connection and send NOTHING — mimics a browser preconnect.
        idle = socket.create_connection((host, port), timeout=5)
        try:
            # A concurrent request must still complete promptly. On the old
            # single-threaded server this urlopen would hang until the idle
            # socket's read timed out.
            resp = urllib.request.urlopen(
                f"{scan_server.base_url}/api/scan/health", timeout=5
            )
            assert resp.status == 200
            assert json.loads(resp.read()) == {"ok": True}
        finally:
            idle.close()

    def test_server_is_threaded(self):
        from socketserver import ThreadingMixIn
        assert issubclass(pnp_server._FastHTTPServer, ThreadingMixIn)


class TestScanPage:
    def test_valid_session_returns_html_with_camera_input(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "mouser")
        status, raw, ctype = _get(f"{scan_server.base_url}/scan?s={sid}")
        assert status == 200
        assert "text/html" in ctype
        page = raw.decode("utf-8")
        assert 'type="file"' in page
        assert 'capture="environment"' in page
        assert 'accept="image/*"' in page
        # Template name injected into the page.
        assert "mouser" in page

    def test_unknown_session_returns_expired_404(self, scan_server):
        status, raw, ctype = _get(f"{scan_server.base_url}/scan?s=bogus")
        assert status == 404
        assert "text/html" in ctype
        assert "expired" in raw.decode("utf-8").lower()

    def test_missing_session_param_expired(self, scan_server):
        status, raw, _ = _get(f"{scan_server.base_url}/scan")
        assert status == 404


# ── Upload route ──


class TestScanUpload:
    def test_valid_upload_runs_ocr_and_pushes_ui(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "lcsc")
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"image_b64": _PNG_1X1_B64, "filename": "po.png"},
        )
        assert status == 200
        assert body["ok"] is True
        assert body["count"] == 1

        # OCR was invoked with the session's template.
        assert scan_server.api.calls == [(_PNG_1X1_B64, "po.png", "lcsc")]

        # Two UI pushes fire: an instant "receiving" ack, then the OCR result.
        assert len(scan_server.js_calls) == 2
        code = next(c for c in scan_server.js_calls if "_scanReceived(" in c)
        payload = json.loads(code[code.index("window._scanReceived(")
                                   + len("window._scanReceived("):-1])
        assert payload["template"] == "lcsc"
        assert payload["filename"] == "po.png"
        assert payload["image_b64"] == _PNG_1X1_B64
        assert payload["line_items"] == scan_server.api._prefill_rows
        # Overlay payload present so the frontend opens the overlay modal.
        assert payload["prefill_rows"] == scan_server.api._prefill_rows
        assert isinstance(payload["pages"], list) and payload["pages"]
        assert all(isinstance(p, dict) for p in payload["pages"])
        assert payload["pages"] == scan_server.api._pages

    def test_instant_receiving_push_fires_before_ocr_result(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "lcsc")
        status, _ = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"image_b64": _PNG_1X1_B64, "filename": "po.png"},
        )
        assert status == 200
        calls = scan_server.js_calls
        # The instant "receiving" ack is pushed first, the OCR'd result second.
        assert "_scanReceiving(" in calls[0]
        assert "_scanReceived(" in calls[1]
        # The ack carries enough for the UI to show a "reading <template>" hint.
        start = calls[0].index("window._scanReceiving(") + len("window._scanReceiving(")
        ack = json.loads(calls[0][start:-1])
        assert ack == {"filename": "po.png", "template": "lcsc", "count": 1}

    def test_multi_image_upload_concats_and_saves_all(self, scan_server, tmp_path):
        sid = pnp_server.create_scan_session(scan_server.server, "lcsc")
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"images": [
                {"image_b64": _PNG_1X1_B64, "filename": "po1.png"},
                {"image_b64": _PNG_1X1_B64, "filename": "po2.png"},
            ]},
        )
        assert status == 200
        assert body["ok"] is True
        assert body["images"] == 2
        # OCR ran once per image; rows + pages concatenated across both.
        assert [c[1] for c in scan_server.api.calls] == ["po1.png", "po2.png"]
        assert body["count"] == 2
        assert len(body["pages"]) == 2
        # Both photos persisted to <base_dir>/scans.
        assert len(list((tmp_path / "scans").glob("scan_*.png"))) == 2
        # The merged desktop push carries both pages.
        received = next(c for c in scan_server.js_calls if "_scanReceived(" in c)
        payload = json.loads(received[received.index("window._scanReceived(")
                                      + len("window._scanReceived("):-1])
        assert len(payload["pages"]) == 2
        assert payload["image_count"] == 2

    def test_single_image_back_compat_still_works(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "lcsc")
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"image_b64": _PNG_1X1_B64, "filename": "po.png"},
        )
        assert status == 200
        assert body["images"] == 1
        assert body["count"] == 1

    def test_too_many_images_rejected(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "generic")
        imgs = [{"image_b64": _PNG_1X1_B64, "filename": f"p{i}.png"}
                for i in range(pnp_server.SCAN_MAX_IMAGES + 1)]
        status, _ = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"images": imgs},
        )
        assert status == 413
        assert not scan_server.api.calls

    def test_multi_image_invalid_member_rejected_without_ocr(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "generic")
        status, _ = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"images": [
                {"image_b64": _PNG_1X1_B64, "filename": "ok.png"},
                {"image_b64": _PNG_1X1_B64, "filename": "bad.exe"},
            ]},
        )
        assert status == 400
        # Validation happens before any OCR/save work.
        assert not scan_server.api.calls

    def test_upload_response_returns_overlay_for_phone(self, scan_server):
        # The phone overlays the detected tokens on its captured image, so the
        # upload RESPONSE (not just the desktop push) must carry the OCR pages and
        # prefill rows. The heavy per-page image_b64 is dropped — the phone already
        # holds the photo it captured.
        sid = pnp_server.create_scan_session(scan_server.server, "lcsc")
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"image_b64": _PNG_1X1_B64, "filename": "po.png"},
        )
        assert status == 200
        assert body["prefill_rows"] == scan_server.api._prefill_rows
        assert isinstance(body["pages"], list) and body["pages"]
        page = body["pages"][0]
        assert "image_b64" not in page          # stripped to keep the response lean
        for key in ("width", "height", "words", "lines"):
            assert key in page

    def test_unknown_session_rejected(self, scan_server):
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s=nope",
            {"image_b64": _PNG_1X1_B64, "filename": "po.png"},
        )
        assert status == 404
        assert body["ok"] is False
        assert not scan_server.api.calls

    def test_non_image_filename_rejected(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "generic")
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"image_b64": _PNG_1X1_B64, "filename": "po.exe"},
        )
        assert status == 400
        assert not scan_server.api.calls

    def test_oversized_payload_rejected(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "generic")
        # base64 string whose decoded length exceeds the cap.
        big = "A" * ((pnp_server.SCAN_MAX_IMAGE_BYTES + 1024) * 4 // 3)
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"image_b64": big, "filename": "po.jpg"},
        )
        assert status == 413
        assert not scan_server.api.calls

    def test_malformed_base64_rejected_400_without_ocr(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "generic")
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"image_b64": "!!!not base64!!!", "filename": "po.jpg"},
        )
        assert status == 400
        assert body["ok"] is False
        # Malformed base64 is a client error — OCR must not be invoked.
        assert not scan_server.api.calls

    def test_oversized_content_length_header_rejected_413(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "generic")
        # Spoof a Content-Length larger than the early-reject bound while
        # sending only a tiny body: the server must respond 413 from the header
        # alone, before reading the (claimed) huge body into memory.
        host, port = scan_server.server.server_address
        if not host or host == "0.0.0.0":
            host = "127.0.0.1"
        spoof_len = pnp_server.SCAN_MAX_IMAGES * pnp_server.SCAN_MAX_IMAGE_BYTES * 2 + 1
        path = f"/api/scan/upload?s={sid}"
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {spoof_len}\r\n"
            "\r\n"
        ).encode("ascii")

        sock = socket.create_connection((host, port), timeout=5)
        try:
            sock.sendall(request)  # headers only; never send the huge body
            sock.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        finally:
            sock.close()

        status_line = data.split(b"\r\n", 1)[0].decode("ascii", "replace")
        assert "413" in status_line
        assert not scan_server.api.calls

    def test_missing_image_rejected(self, scan_server):
        sid = pnp_server.create_scan_session(scan_server.server, "generic")
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"filename": "po.png"},
        )
        assert status == 400
        assert not scan_server.api.calls

    def test_upload_saves_photo_to_scans_dir(self, scan_server, tmp_path):
        sid = pnp_server.create_scan_session(scan_server.server, "lcsc")
        status, body = _post_json(
            f"{scan_server.base_url}/api/scan/upload?s={sid}",
            {"image_b64": _PNG_1X1_B64, "filename": "po.png"},
        )
        assert status == 200
        assert body["ok"] is True
        assert body["saved"] is True

        # The raw photo landed in <base_dir>/scans with the original bytes.
        scans_dir = tmp_path / "scans"
        saved = list(scans_dir.glob("scan_*.png"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == base64.b64decode(_PNG_1X1_B64)

    def test_ui_push_failure_still_200(self, tmp_path):
        api = _FakeApi(str(tmp_path))

        def exploding(code):
            raise RuntimeError("window closed")

        window = types.SimpleNamespace(evaluate_js=exploding)
        server = start_pnp_server(api, window, port=0)
        port = server.server_address[1]
        try:
            sid = pnp_server.create_scan_session(server, "generic")
            status, body = _post_json(
                f"http://127.0.0.1:{port}/api/scan/upload?s={sid}",
                {"image_b64": _PNG_1X1_B64, "filename": "po.png"},
            )
            assert status == 200
            assert body["ok"] is True
        finally:
            stop_pnp_server(server)


class TestSaveScanImage:
    def test_creates_scans_dir_and_writes_bytes(self, tmp_path):
        path = pnp_server._save_scan_image(str(tmp_path), b"\x89PNG\r\n", ".png")
        assert os.path.dirname(path) == str(tmp_path / "scans")
        assert os.path.basename(path).startswith("scan_")
        assert path.endswith(".png")
        with open(path, "rb") as f:
            assert f.read() == b"\x89PNG\r\n"

    def test_two_saves_do_not_collide(self, tmp_path):
        p1 = pnp_server._save_scan_image(str(tmp_path), b"a", ".jpg")
        p2 = pnp_server._save_scan_image(str(tmp_path), b"b", ".jpg")
        assert p1 != p2
        assert len(list((tmp_path / "scans").glob("*.jpg"))) == 2


# ── inventory_api.start_scan_session ──


class TestStartScanSession:
    def test_returns_session_and_urls(self, api):
        window = types.SimpleNamespace(evaluate_js=lambda code: None)
        server = start_pnp_server(api, window, port=0)
        api._pnp_server = server
        try:
            result = api.start_scan_session("lcsc")
            assert result["template"] == "lcsc"
            assert result["session_id"]
            assert result["port"] == server.server_address[1]
            # Session registered on the server.
            assert pnp_server._get_scan_session(server, result["session_id"])
            # At least one URL pointing at /scan with the session id (LAN IP may
            # be empty on isolated CI, so only assert shape when present).
            for url in result["urls"]:
                assert url.endswith(f"/scan?s={result['session_id']}")
                assert f":{result['port']}/scan" in url
        finally:
            stop_pnp_server(server)

    def test_unknown_template_raises(self, api):
        window = types.SimpleNamespace(evaluate_js=lambda code: None)
        server = start_pnp_server(api, window, port=0)
        api._pnp_server = server
        try:
            with pytest.raises(ValueError):
                api.start_scan_session("not-a-template")
        finally:
            stop_pnp_server(server)

    def test_no_server_raises(self, api):
        # No _pnp_server attribute set.
        with pytest.raises(RuntimeError):
            api.start_scan_session("generic")
