"""Tests for distributor product-preview + credential /v1 routes.

Product fetches are monkeypatched at the `InventoryApi` facade method level
(the same level the routes call `getattr(api, method_name)(code)` against) so
no test here ever touches the network — mirrors the fixture-free mocking
style in `tests/python/test_distributor_manager.py`.
"""


def test_fetch_lcsc_product_happy_path(api, client, monkeypatch):
    monkeypatch.setattr(api, "fetch_lcsc_product", lambda code: {"code": code, "price": 0.01})
    r = client.get("/v1/distributors/lcsc/product/C12345")
    assert r.status_code == 200
    assert r.json() == {"code": "C12345", "price": 0.01}


def test_fetch_digikey_product_happy_path(api, client, monkeypatch):
    monkeypatch.setattr(api, "fetch_digikey_product", lambda pn: {"pn": pn})
    r = client.get("/v1/distributors/digikey/product/296-1234-1-ND")
    assert r.status_code == 200
    assert r.json() == {"pn": "296-1234-1-ND"}


def test_fetch_mouser_product_happy_path(api, client, monkeypatch):
    monkeypatch.setattr(api, "fetch_mouser_product", lambda pn: {"pn": pn})
    r = client.get("/v1/distributors/mouser/product/512-LM358N")
    assert r.status_code == 200
    assert r.json() == {"pn": "512-LM358N"}


def test_fetch_pololu_product_happy_path(api, client, monkeypatch):
    monkeypatch.setattr(api, "fetch_pololu_product", lambda sku: {"sku": sku})
    r = client.get("/v1/distributors/pololu/product/2135")
    assert r.status_code == 200
    assert r.json() == {"sku": "2135"}


def test_fetch_product_unknown_distributor_is_404(client):
    r = client.get("/v1/distributors/not-a-real-vendor/product/ABC")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "unknown_distributor"
    assert body["detail"] is None


def test_fetch_product_none_result_is_404_product_not_found(api, client, monkeypatch):
    monkeypatch.setattr(api, "fetch_lcsc_product", lambda code: None)
    r = client.get("/v1/distributors/lcsc/product/C99999")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "product_not_found"
    assert body["detail"] is None


def test_digikey_session_merges_both_facade_calls(api, client, monkeypatch):
    monkeypatch.setattr(api, "check_digikey_session", lambda: {"has_cookies": True})
    monkeypatch.setattr(api, "get_digikey_login_status", lambda: {"logged_in": False})
    r = client.get("/v1/distributors/digikey/session")
    assert r.status_code == 200
    assert r.json() == {"has_cookies": True, "logged_in": False}


def test_logout_digikey(api, client, monkeypatch):
    monkeypatch.setattr(api, "logout_digikey", lambda: {"status": "logged_out"})
    r = client.delete("/v1/distributors/digikey/session")
    assert r.status_code == 200
    assert r.json() == {"status": "logged_out"}


def test_validate_digikey_session(api, client, monkeypatch):
    monkeypatch.setattr(api, "validate_digikey_session", lambda: {"valid": True})
    r = client.post("/v1/distributors/digikey/session/validate")
    assert r.status_code == 200
    assert r.json() == {"valid": True}


def test_sync_digikey_cookies(api, client, monkeypatch):
    monkeypatch.setattr(api, "sync_digikey_cookies", lambda: {"synced": True})
    r = client.post("/v1/distributors/digikey/cookies/sync")
    assert r.status_code == 200
    assert r.json() == {"synced": True}


def test_mouser_api_key_roundtrip(client):
    r = client.get("/v1/distributors/mouser/key")
    assert r.status_code == 200
    assert r.json()["configured"] is False

    r2 = client.put("/v1/distributors/mouser/key", json={"key": "test-key-123"})
    assert r2.status_code == 200
    assert r2.json()["configured"] is True

    r3 = client.get("/v1/distributors/mouser/key")
    assert r3.json()["configured"] is True

    r4 = client.delete("/v1/distributors/mouser/key")
    assert r4.status_code == 200
    assert r4.json()["configured"] is False

    r5 = client.get("/v1/distributors/mouser/key")
    assert r5.json()["configured"] is False
