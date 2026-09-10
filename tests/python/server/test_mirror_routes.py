"""Tests for the inventory-mirror /v1 routes.

Regression origin: `js/preferences-modal.js` called `api(...)` for all three
mirror methods, but none of them had a /v1 route and none of them were on the
`ClientShell` pywebview bridge either — so every call fell through
`js/api.js`'s bridge fallback to `window.pywebview.api[method]`, which had no
such method. The "Inventory Mirror (Tailscale)" row therefore failed to
populate in *both* clients: a plain browser threw "Cannot read properties of
undefined (reading 'api')" (no `window.pywebview` at all) and the desktop app
would have thrown "is not a function" against the 11-method shell.
`tests/python/test_api_surface.py::test_js_api_call_sites_resolve_to_a_real_surface`
guards the bug class; these tests guard the routes themselves.

Facade behavior is already covered by `tests/python/test_api_mirror.py` — this
module only asserts the HTTP layer: the routes exist, they dispatch to the
right `InventoryApi` method, and a host that cannot mirror reports why in the
standard `{error, code, detail}` body rather than an opaque 500.
"""

from __future__ import annotations

import pytest

_INFO = {
    "enabled": True,
    "installed": True,
    "running": True,
    "serve_url": "https://host.ts.net",
    "read_port": 7893,
    "allowlist": ["alice@example.com"],
}


def test_get_mirror_info_returns_facade_shape(api, client, monkeypatch):
    monkeypatch.setattr(api, "get_inventory_mirror_info", lambda: _INFO)
    r = client.get("/v1/inventory-mirror")
    assert r.status_code == 200
    assert r.json() == _INFO


def test_get_mirror_info_works_unmocked(client):
    """The real facade must answer the route on a stock temp data dir.

    The route the modal calls on every open has to work without a mirror ever
    having been enabled — the default is `enabled: false`, not an error.
    """
    r = client.get("/v1/inventory-mirror")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert set(body) >= {"enabled", "installed", "running", "serve_url",
                         "read_port", "allowlist"}


def test_enable_dispatches_and_returns_info(api, client, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "enable_inventory_mirror",
                        lambda: (calls.append("enable"), _INFO)[1])
    r = client.post("/v1/inventory-mirror/enable")
    assert r.status_code == 200
    assert calls == ["enable"]
    assert r.json() == _INFO


def test_disable_dispatches_and_returns_info(api, client, monkeypatch):
    calls = []
    disabled = {**_INFO, "enabled": False}
    monkeypatch.setattr(api, "disable_inventory_mirror",
                        lambda: (calls.append("disable"), disabled)[1])
    r = client.post("/v1/inventory-mirror/disable")
    assert r.status_code == 200
    assert calls == ["disable"]
    assert r.json()["enabled"] is False


@pytest.mark.parametrize("exc", [
    RuntimeError("tailscale is not logged in"),
    RuntimeError("systemctl daemon-reload failed: Failed to connect to bus"),
    NotImplementedError("mirror autostart not yet implemented for sunos5"),
])
def test_enable_failure_keeps_the_actionable_message(api, client, monkeypatch, exc):
    """Every way a host says "I can't mirror" carries a message written for the
    user: `tailscale.enable_serve`'s RuntimeError, an installer's own
    RuntimeError (this is the container's case — it is Linux, so it gets
    `LinuxInstaller` and fails at `systemctl --user`, not at dispatch), and
    `get_installer`'s NotImplementedError on a platform with no installer at
    all. Unhandled, `server/errors.py` maps none of them, so each would surface
    as an opaque 500 with no body contract and the reason lost."""
    def boom():
        raise exc
    monkeypatch.setattr(api, "enable_inventory_mirror", boom)
    r = client.post("/v1/inventory-mirror/enable")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "dubis_error"
    assert body["error"] == str(exc)
    assert body["detail"] is None


def test_disable_failure_keeps_the_actionable_message(api, client, monkeypatch):
    def boom():
        raise RuntimeError("tailscale serve reset failed")
    monkeypatch.setattr(api, "disable_inventory_mirror", boom)
    r = client.post("/v1/inventory-mirror/disable")
    assert r.status_code == 500
    assert r.json()["error"] == "tailscale serve reset failed"

# Enable/disable deliberately publish no `inventory.updated` SSE event — mirror
# state is not inventory-derived. That is asserted centrally, by their EXEMPT
# entries in `tests/python/server/test_mutation_publishes.py`, rather than
# re-tested here.
