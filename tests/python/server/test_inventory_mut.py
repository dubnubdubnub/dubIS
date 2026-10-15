"""Inventory mutation routes: adjust/update/delete/import/consume + rollback."""

import csv

from fastapi.testclient import TestClient

from inventory_api import InventoryApi
from server import events
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

# A simulated non-loopback peer, so `AuthMiddleware` resolves a NAMED identity
# and `stamp_source` suffixes the mutation tag — the exact condition that made
# the documented undo command a silent no-op on every remote server.
REMOTE = ("100.64.1.2", 51234)


def test_adjust_part_set_happy_path(client):
    r = client.post("/v1/parts/C100000/adjust", json={"adj_type": "set", "quantity": 5})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 5


def test_adjust_part_add_happy_path(client):
    client.post("/v1/parts/C100000/adjust", json={"adj_type": "add", "quantity": 3})
    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 13


def test_adjust_part_remove_happy_path(client):
    client.post("/v1/parts/C100000/adjust", json={"adj_type": "remove", "quantity": 4})
    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 6


def test_adjust_part_publishes_inventory_updated(client):
    q = events.subscribe()
    try:
        client.post("/v1/parts/C100000/adjust", json={"adj_type": "set", "quantity": 1})
        name, data = q.get(timeout=2)
        assert name == "inventory.updated"
        assert data["reason"] == "adjust"
    finally:
        events.unsubscribe(q)


def test_adjust_part_response_never_carries_inventory(client):
    # Phase 1b Task 10 removed the `?include=inventory` echo entirely — the
    # query param is now just an unknown/ignored param (FastAPI silently
    # drops it), and the response is always the plain `{"ok", "detail"}`
    # envelope, never an `inventory` key, whether or not the (now-inert)
    # query param is present. Frontend refresh is SSE-driven.
    r_with_include = client.post(
        "/v1/parts/C100000/adjust?include=inventory",
        json={"adj_type": "set", "quantity": 7},
    )
    assert r_with_include.status_code == 200
    body_with_include = r_with_include.json()
    assert "inventory" not in body_with_include
    assert body_with_include["detail"]["part_key"] == "C100000"

    r_without_include = client.post(
        "/v1/parts/C100000/adjust", json={"adj_type": "set", "quantity": 1},
    )
    assert "inventory" not in r_without_include.json()


def test_update_part_fields_roundtrip(client):
    r = client.patch(
        "/v1/parts/C100000",
        json={"fields": {"description": "Updated Resistor"}},
    )
    assert r.status_code == 200

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["description"] == "Updated Resistor"


def test_delete_part_with_purchase_history_is_400(client):
    r = client.delete("/v1/parts/C100000")
    assert r.status_code == 400


def test_delete_part_without_purchase_history_succeeds(api, client):
    api.adjust_part("set", "ADJUSTONLY-1", 5, note="manual add", source="test")
    r = client.delete("/v1/parts/ADJUSTONLY-1")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_consume_bom_end_to_end(client):
    r = client.post(
        "/v1/bom/consume",
        json={
            "matches": [{"part_key": "C100000", "bom_qty": 2}],
            "board_qty": 3,
            "bom_name": "test.csv",
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 4  # 10 - (2*3)


def test_rollback_source_removes_tagged_adjustments(api, client):
    api.adjust_part("add", "C100000", 5, note="test bump", source="test:session-1")

    r2 = client.get("/v1/parts")
    item = next(i for i in r2.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 15

    r = client.delete("/v1/adjustments/by-source/test:session-1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["detail"]["removed"]) == 1

    r3 = client.get("/v1/parts")
    item = next(i for i in r3.json()["inventory"] if i["lcsc"] == "C100000")
    assert item["qty"] == 10


def test_update_part_price(client):
    r = client.put("/v1/parts/C100000/price", json={"unit_price": 0.05, "ext_price": 0.5})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_import_purchases(client):
    r = client.post(
        "/v1/purchases/import",
        json={"rows": [make_part(lcsc="C200000", qty=20)]},
    )
    assert r.status_code == 200
    r2 = client.get("/v1/parts")
    lcscs = {i["lcsc"] for i in r2.json()["inventory"]}
    assert "C200000" in lcscs


def test_remove_last_purchases_requires_count(client):
    r = client.delete("/v1/purchases/last")
    assert r.status_code == 422


def test_remove_last_purchases(api, client):
    write_ledger(api, [make_part(lcsc="C100000", qty=10), make_part(lcsc="C300000", qty=1)])
    r = client.delete("/v1/purchases/last?count=1")
    assert r.status_code == 200


def test_remove_last_adjustments(api, client):
    api.adjust_part("add", "C100000", 1, note="bump", source="test")
    r = client.delete("/v1/adjustments/last?count=1")
    assert r.status_code == 200


def test_fetch_missing_descriptions(client):
    r = client.post("/v1/parts/fetch-missing-descriptions")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_record_fetched_prices(client):
    r = client.post(
        "/v1/parts/C100000/fetched-prices",
        json={"distributor": "lcsc", "price_tiers": [{"qty": 1, "price": 0.01}]},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_resolve_bom_spec_no_match_returns_null(client):
    r = client.post(
        "/v1/bom/resolve-spec",
        json={"part_type": "resistor", "value": 10000.0, "package": "0402"},
    )
    assert r.status_code == 200
    assert r.json() == {"match": None}


def test_extract_spec_from_value(client):
    r = client.post(
        "/v1/spec/extract",
        json={"part_type": "resistor", "value_str": "10k", "package_str": "0402"},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


# ── undo a whole session by its mutation tag ─────────────────────────────────
#
# `stamp_source` (server/auth.py) stores `{tag}@{identity}` for any non-local
# caller, so the tag an operator remembers and the tag on disk are different
# strings. Comparing them exactly made `dubis adjustments rollback-source
# xfer-to-aspen-20261014` remove nothing on fremont / reality-labs / aspen while
# still answering 200 — three suffixes (`@…@gmail.com`, `@isaac`, `@root`) for
# one session, and stale stock behind a green result.


def _authed(tmp_path, monkeypatch):
    """A /v1 client whose every request resolves to a named identity."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "alice:tok-a,bob:tok-b")
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return TestClient(create_app(inst), client=REMOTE), inst


def _qty(client, part_key, headers=None):
    items = client.get("/v1/parts", headers=headers or {}).json()["inventory"]
    return next(i["qty"] for i in items if i["lcsc"] == part_key)


def _ledger_rows(api):
    with open(api.input_csv, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_rollback_source_matches_the_identity_suffixed_tag(tmp_path, monkeypatch):
    """The production repro, end to end: stored `xfer-2026@alice`, undone by
    the bare tag the operator actually typed."""
    client, _api = _authed(tmp_path, monkeypatch)
    with client:
        headers = {"Authorization": "Bearer tok-a"}
        r = client.post(
            "/v1/parts/C100000/adjust",
            json={"adj_type": "add", "quantity": 5, "source": "xfer-2026"},
            headers=headers,
        )
        assert r.status_code == 200
        history = client.get("/v1/parts/C100000/history", headers=headers).json()
        assert history[-1]["source"] == "xfer-2026@alice"
        assert _qty(client, "C100000", headers) == 15

        r = client.delete("/v1/adjustments/by-source/xfer-2026", headers=headers)
        assert r.status_code == 200, r.text
        assert len(r.json()["detail"]["removed"]) == 1
        assert _qty(client, "C100000", headers) == 10


def test_rollback_source_removes_every_identity_that_used_the_tag(tmp_path,
                                                                  monkeypatch):
    """Two humans, one tag, one server: both roll back. That is the intent of
    naming a session rather than a row, and the reason the match is a suffix
    boundary rather than an exact compare."""
    client, _api = _authed(tmp_path, monkeypatch)
    with client:
        for token in ("tok-a", "tok-b"):
            r = client.post(
                "/v1/parts/C100000/adjust",
                json={"adj_type": "add", "quantity": 5, "source": "xfer-2026"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200
        assert _qty(client, "C100000", {"Authorization": "Bearer tok-a"}) == 20

        r = client.delete("/v1/adjustments/by-source/xfer-2026",
                          headers={"Authorization": "Bearer tok-a"})
        assert r.status_code == 200, r.text
        removed = r.json()["detail"]["removed"]
        assert {row["source"] for row in removed} == {"xfer-2026@alice",
                                                      "xfer-2026@bob"}
        assert _qty(client, "C100000", {"Authorization": "Bearer tok-a"}) == 10


def test_rollback_source_does_not_match_a_longer_tag_sharing_a_prefix(api, client):
    """`@` is the only wildcard allowed. `xfer-2026` must never eat the rows of
    the `xfer-2026-12-31` session that happens to start with the same text."""
    api.adjust_part("add", "C100000", 5, source="xfer-2026")
    api.adjust_part("add", "C100000", 7, source="xfer-2026-12-31@bob")
    assert _qty(client, "C100000") == 22

    r = client.delete("/v1/adjustments/by-source/xfer-2026")
    assert r.status_code == 200, r.text
    assert len(r.json()["detail"]["removed"]) == 1
    assert _qty(client, "C100000") == 17  # the -12-31 session survives intact


def test_rollback_source_that_removes_nothing_is_an_error(api, client):
    """A zero-count undo is a failed undo: 404, naming both the tag typed and
    the tags the server actually holds."""
    api.adjust_part("add", "C100000", 5, source="xfer-2026@alice")

    r = client.delete("/v1/adjustments/by-source/xfer-202")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"
    assert "xfer-202" in body["error"]
    assert "xfer-2026@alice" in body["error"]
    assert _qty(client, "C100000") == 15


def test_import_purchases_tags_rows_and_rollback_removes_them(api, client):
    """The other half of the bug: imported ledger rows had no provenance at all,
    so the only undo was "delete the last N rows" — order-dependent, and it
    forces anything imported afterwards to be deleted first."""
    r = client.post(
        "/v1/purchases/import",
        json={"rows": [make_part(lcsc="C200000", qty=20)], "source": "xfer-2026"},
    )
    assert r.status_code == 200
    # The stored tag is echoed: on a remote server it is NOT what the caller
    # sent, and the undo has to name the stored one.
    assert r.json()["detail"]["source"] == "xfer-2026"
    assert [row["source"] for row in _ledger_rows(api)] == ["", "xfer-2026"]

    r = client.delete("/v1/purchases/by-source/xfer-2026")
    assert r.status_code == 200, r.text
    assert len(r.json()["detail"]["removed"]) == 1
    assert [row["source"] for row in _ledger_rows(api)] == [""]
    lcscs = {i["lcsc"] for i in client.get("/v1/parts").json()["inventory"]}
    assert "C200000" not in lcscs


def test_import_purchases_without_a_source_stays_untagged(client):
    """No tag means unknown provenance, never a guessed one: a default like
    "import" would make every untagged import roll back with every other."""
    client.post("/v1/purchases/import", json={"rows": [make_part(lcsc="C200000", qty=20)]})
    r = client.delete("/v1/purchases/by-source/import")
    assert r.status_code == 404
    body = r.json()
    # Asserted on the message, not just the status: a route that does not exist
    # also answers 404/not_found, and that is not this behaviour.
    assert body["code"] == "not_found"
    assert "import" in body["error"] and "nothing was undone" in body["error"]


def test_purchases_rollback_matches_the_identity_suffixed_tag(tmp_path, monkeypatch):
    client, _api = _authed(tmp_path, monkeypatch)
    with client:
        headers = {"Authorization": "Bearer tok-a"}
        r = client.post(
            "/v1/purchases/import",
            json={"rows": [make_part(lcsc="C200000", qty=20)], "source": "xfer-2026"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["detail"]["source"] == "xfer-2026@alice"

        r = client.delete("/v1/purchases/by-source/xfer-2026", headers=headers)
        assert r.status_code == 200, r.text
        removed = r.json()["detail"]["removed"]
        assert [row["source"] for row in removed] == ["xfer-2026@alice"]
        lcscs = {i["lcsc"] for i in client.get("/v1/parts", headers=headers).json()["inventory"]}
        assert "C200000" not in lcscs


def test_purchases_rollback_that_removes_nothing_is_an_error(api, client):
    """Rows written before the `source` column existed have no tag, so they are
    matchable by nothing — the undo says so loudly and leaves them alone."""
    before = _ledger_rows(api)
    r = client.delete("/v1/purchases/by-source/xfer-2026")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"
    assert "xfer-2026" in body["error"] and "nothing was undone" in body["error"]
    assert _ledger_rows(api) == before


def test_import_migrates_a_ledger_that_predates_the_source_column(api, client):
    """An existing deployment's ledger has a shorter header than FIELDNAMES.
    The import migrates it in place, keeps every old row (untagged = unknown),
    and tags only what it appends."""
    legacy = [fn for fn in InventoryApi.FIELDNAMES if fn != "source"]
    with open(api.input_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=legacy)
        writer.writeheader()
        row = {fn: "" for fn in legacy}
        row.update({"LCSC Part Number": "C100000", "Quantity": "10"})
        writer.writerow(row)

    r = client.post(
        "/v1/purchases/import",
        json={"rows": [make_part(lcsc="C200000", qty=20)], "source": "xfer-2026"},
    )
    assert r.status_code == 200

    with open(api.input_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == InventoryApi.FIELDNAMES
        rows = list(reader)
    assert [row["source"] for row in rows] == ["", "xfer-2026"]
    assert [row["LCSC Part Number"] for row in rows] == ["C100000", "C200000"]
    assert _qty(client, "C100000") == 10
