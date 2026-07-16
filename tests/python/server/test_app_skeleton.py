"""/v1 app factory skeleton: health, meta, error contract."""


def test_health(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_meta_exposes_section_order(client):
    r = client.get("/v1/meta")
    body = r.json()
    assert r.status_code == 200
    assert isinstance(body["section_order"], dict) or isinstance(body["section_order"], list)
    assert body["flat_section_order"]


def test_unknown_route_is_structured_404(client):
    r = client.get("/v1/nope")
    assert r.status_code == 404


def test_value_error_maps_to_400(client):
    # adjust with invalid type triggers ValueError in facade — route added in Task 4;
    # here we register a throwaway route to pin the handler mapping itself.
    from dubis_errors import CacheError
    app = client.app

    @app.get("/v1/_test/valueerror")
    def _raise_ve():
        raise ValueError("bad input")

    @app.get("/v1/_test/cacheerror")
    def _raise_ce():
        raise CacheError("cache broken")

    r = client.get("/v1/_test/valueerror")
    assert r.status_code == 400
    assert r.json()["error"] == "bad input"
    assert r.json()["code"] == "value_error"

    r = client.get("/v1/_test/cacheerror")
    assert r.status_code == 500
    assert r.json()["code"] == "cache_error"
