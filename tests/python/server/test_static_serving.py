"""Static frontend serving from the /v1 app: StaticFiles mounted at `/`.

Mount must happen AFTER all API routers so `/v1/*` (and any other API route)
takes precedence over a same-path static file. Guarded by only mounting when
`static_dir` is given and exists — no static_dir means `/` stays 404 (today's
behavior), so existing deployments (e.g. the stub-api surface tests) are
unaffected.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def test_root_serves_index_html(api):
    with TestClient(create_app(api, static_dir=str(REPO_ROOT))) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "dubIS" in r.text


def test_js_module_served_with_js_content_type(api):
    with TestClient(create_app(api, static_dir=str(REPO_ROOT))) as c:
        r = c.get("/js/api.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_v1_routes_take_precedence_over_static(api):
    with TestClient(create_app(api, static_dir=str(REPO_ROOT))) as c:
        r = c.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_no_static_dir_root_is_404(api):
    with TestClient(create_app(api)) as c:
        r = c.get("/")
    assert r.status_code == 404
