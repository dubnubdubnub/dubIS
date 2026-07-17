"""Uniform error contract for framework-generated 404/422 + loopback-only
filesystem-path routes (design doc `docs/plans/2026-07-16-phase1c-remote-deploy-design.md`
§3, Phase 1c Task 3).

Every hand-written error already emits `{error, code, detail}` via
`server/errors.py::register_handlers`. FastAPI/Starlette's own framework
responses (unknown route -> 404, pydantic validation failure -> 422) bypassed
that contract until now; this module locks them down.

## Filesystem-path audit (Task 3)

Grepped `server/routes/*.py` for body fields that plausibly name a
server-local filesystem path (`path`, `file_path`, `dir`, `_path` suffixes):

- `server/routes/import_scan.py::ParseImportBody.path` -- **GATED**. Read
  directly off disk by `InventoryApi.parse_source_file` (via
  `mfg_direct_import.parse_source_file`) with no path confinement. This is
  the one the design doc calls out by name. Gated with `require_loopback`
  below, but only on the `body.path` branch -- the b64-upload branch
  (`file_b64`/`file_name`) is the browser-upload path remote callers are
  expected to use and stays open to any authenticated identity.
- `server/routes/vendors_pos.py::UpdateVendorBody.favicon_path` -- **CLEAN**.
  Write-only: stored verbatim as vendor metadata (`domain/api_vendors.py`),
  never opened/read by the server. Client sets it from a prior
  `fetch_favicon` response (a server-chosen relative path under
  `data/sources/favicons/`), not an attacker-controlled read target.
- `server/routes/vendors_pos.py::get_po_source` (`GET
  /v1/purchase-orders/{po_id}/source`) -- **CLEAN**. Takes a `po_id` path
  param, not a body-supplied filesystem path; the real path comes from
  `purchase_orders.resolve_source_path(api._sources_dir, po_id, api._po_csv)`,
  a server-controlled lookup with no attacker-supplied path component.
- `server/routes/vendors_pos.py::FetchFaviconBody.url` -- CLEAN (network URL,
  not a filesystem path; not in scope for this gate).
- All other route bodies checked (`generic_parts.py`, `distributors.py`,
  `pnp.py`, `preferences.py`, `meta.py`, `events.py`,
  `inventory_mut.py`) -- no `path`/`file_path`/`dir`-shaped fields at all.

Conclusion: only `/v1/import/parse`'s `path` field needed gating.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

LOOPBACK = ("127.0.0.1", 51234)
REMOTE = ("100.64.1.2", 51234)


def _api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


# ── 404: unknown route ───────────────────────────────────────────────────────


def test_unknown_route_404_body_shape(tmp_path):
    api = _api(tmp_path)
    with TestClient(create_app(api)) as c:
        r = c.get("/v1/this-route-does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"
    assert "error" in body
    assert "detail" in body


# ── 422: validation error ────────────────────────────────────────────────────


def test_validation_error_422_body_shape(tmp_path):
    api = _api(tmp_path)
    with TestClient(create_app(api)) as c:
        # adj_type/quantity are required + typed on AdjustPartBody; send a
        # wrong-typed quantity (string that isn't a number) to trigger a
        # pydantic validation failure rather than a missing-field one, so
        # this also proves type coercion errors get the contract shape.
        r = c.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": "not-a-number", "note": ""},
        )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "error" in body
    assert isinstance(body["detail"], list)
    assert body["detail"], "detail should carry the pydantic field errors"
    assert any("quantity" in str(e.get("loc", [])) for e in body["detail"])


# ── loopback-only: /v1/import/parse ─────────────────────────────────────────


def test_import_parse_path_403_for_remote_bearer_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    src = tmp_path / "order.csv"
    src.write_text("mpn,quantity\nABC123,5\n", encoding="utf-8")
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post(
            "/v1/import/parse",
            json={"path": str(src)},
            headers={"Authorization": "Bearer abc123"},
        )
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "loopback_only"
    assert "error" in body
    assert "detail" in body


def test_import_parse_path_works_for_loopback_in_on_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    api = _api(tmp_path)
    src = tmp_path / "order.csv"
    src.write_text("mpn,quantity\nABC123,5\n", encoding="utf-8")
    with TestClient(create_app(api), client=LOOPBACK) as c:
        r = c.post("/v1/import/parse", json={"path": str(src)})
    assert r.status_code == 200


def test_import_parse_path_works_in_off_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    api = _api(tmp_path)
    src = tmp_path / "order.csv"
    src.write_text("mpn,quantity\nABC123,5\n", encoding="utf-8")
    # Off mode: no middleware installed at all -- even a simulated remote
    # peer is unaffected, matching every other route's off-mode behavior.
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post("/v1/import/parse", json={"path": str(src)})
    assert r.status_code == 200


def test_import_parse_b64_still_works_for_remote_bearer_caller(tmp_path, monkeypatch):
    """The gate only applies to the server-filesystem-path branch -- the
    browser-upload (b64) branch, which is how remote callers are expected to
    submit files, must stay open to any authenticated identity."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "ci:abc123")
    api = _api(tmp_path)
    import base64

    file_b64 = base64.b64encode(b"mpn,quantity\nABC123,5\n").decode("ascii")
    with TestClient(create_app(api), client=REMOTE) as c:
        r = c.post(
            "/v1/import/parse",
            json={"file_b64": file_b64, "file_name": "order.csv"},
            headers={"Authorization": "Bearer abc123"},
        )
    assert r.status_code == 200
