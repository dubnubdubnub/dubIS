"""Tests for import/OCR + scan-session /v1 routes."""

import base64


def test_detect_columns(client):
    r = client.post(
        "/v1/import/detect-columns",
        json={"headers": ["LCSC Part Number", "Quantity", "Unit Price($)"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "LCSC Part Number" in body.values() or "LCSC Part Number" in body


def test_match_part_no_match_returns_dict(client):
    r = client.post(
        "/v1/import/match-part",
        json={"mpn": "TOTALLY-UNKNOWN-MPN-XYZ", "manufacturer": "Acme"},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_ocr_available_returns_bool_flag(client):
    r = client.get("/v1/import/ocr/available")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"available"}
    assert isinstance(body["available"], bool)


def test_parse_import_source_b64_csv(client):
    csv_text = "Manufacture Part Number,Quantity\nMPN-100,5\n"
    file_b64 = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    r = client.post(
        "/v1/import/parse",
        json={"file_b64": file_b64, "file_name": "order.csv", "template": "generic"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert rows[0]["mpn"] == "MPN-100"
    assert rows[0]["quantity"] == 5


def test_parse_import_source_path_uses_server_local_file(tmp_path, client):
    csv_path = tmp_path / "local_order.csv"
    csv_path.write_text("Manufacture Part Number,Quantity\nMPN-200,7\n", encoding="utf-8")

    r = client.post(
        "/v1/import/parse",
        json={"path": str(csv_path), "template": "generic"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["mpn"] == "MPN-200"
    assert rows[0]["quantity"] == 7


def test_start_scan_session_returns_409_when_pnp_server_not_running(client):
    r = client.post("/v1/scan/sessions", json={"template": "generic"})
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "pnp_server_unavailable"
    assert body["detail"] is None


def test_start_scan_session_happy_path(api, client):
    from pnp_server import start_pnp_server, stop_pnp_server

    server = start_pnp_server(api, port=0)
    api._pnp_server = server
    try:
        r = client.post("/v1/scan/sessions", json={"template": "generic"})
        assert r.status_code == 200
        body = r.json()
        assert "session_id" in body
        assert body["template"] == "generic"
    finally:
        stop_pnp_server(server)
