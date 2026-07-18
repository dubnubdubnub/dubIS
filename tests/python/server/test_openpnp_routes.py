"""Tests for GET /v1/openpnp/part/{part_key} — the dubIS -> OpenPnP bridge's
first increment (Tier-1 standard-package family table + attrs projection)."""

from __future__ import annotations

from domain import part_registry
from tests.python.helpers import make_api, make_part, write_ledger


def test_standard_passive_resolves_via_tier1_family_table(client):
    """A 100nF 0402 cap (already seeded via the `client`/`api` fixtures'
    default 0402 resistor won't do — seed our own capacitor row) hits the
    Tier-1 family table and gets full package/body-dims/kicad_footprint."""
    r = client.post(
        "/v1/purchases/import",
        json={"rows": [make_part(lcsc="C200000", desc="Capacitor 100nF 0402", pkg="0402")]},
    )
    assert r.status_code == 200

    r = client.get("/v1/openpnp/part/C200000")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "C200000"
    assert body["tier"] == "standard"
    assert body["package_id"] == "C0402"
    assert body["kicad_footprint"] == "Capacitor_SMD:C_0402_1005Metric"
    assert body["package"]["body_width_mm"] == 1.0
    assert body["package"]["body_height_mm"] == 0.5
    assert body["height_mm"] == 0.5
    assert body["speed"] == 1.0


def test_seeded_resistor_returns_openpnp_attrs(client):
    """The `client`/`api` fixtures' default seed (C100000, 'Resistor 10kΩ',
    pkg 0402) resolves through the same endpoint end to end."""
    r = client.get("/v1/openpnp/part/C100000")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "C100000"
    assert body["tier"] == "standard"
    assert body["package_id"] == "R0402"
    assert body["kicad_footprint"] == "Resistor_SMD:R_0402_1005Metric"


def test_alias_distributor_pn_resolves_to_canonical_part(api, client):
    """Guard against the #354 class of bug: a distributor PN registered as an
    *alias* of a canonical part (e.g. recorded before an MPN-precedence
    enrichment changed the row's derived key) must resolve to the SAME part
    as a direct lookup by the canonical key — via the shared
    domain.part_registry alias_index, not a second parallel derivation."""
    registry = part_registry.load(api.base_dir)
    registry.parts["C100000"] = ["C100000", "OLD-ALIAS-PN"]
    part_registry.save(api.base_dir, registry)

    r_alias = client.get("/v1/openpnp/part/OLD-ALIAS-PN")
    r_canonical = client.get("/v1/openpnp/part/C100000")
    assert r_alias.status_code == 200
    assert r_alias.json() == r_canonical.json()
    assert r_alias.json()["id"] == "C100000"


def test_unknown_part_key_is_404(client):
    r = client.get("/v1/openpnp/part/NO-SUCH-PART")
    assert r.status_code == 404
    body = r.json()
    assert set(body.keys()) == {"error", "code", "detail"}
    assert body["code"] == "not_found"


def test_ic_returns_unmapped_tier_with_known_package_id_only(tmp_path):
    """An IC (no Tier-1 family match) gets tier:"unmapped": package_id comes
    straight from the raw `package` field, but body dims/kicad_footprint stay
    null since there's no standard-passive geometry to derive them from —
    that's Tier-2 (STEP-based generation), out of scope for this increment."""
    from fastapi.testclient import TestClient

    from server.app import create_app

    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(
        lcsc="C300000", desc="Microcontroller STM32F103", pkg="LQFP48",
    )])
    with TestClient(create_app(inst)) as c:
        r = c.get("/v1/openpnp/part/C300000")
        assert r.status_code == 200
        body = r.json()
        assert body["tier"] == "unmapped"
        assert body["package_id"] == "LQFP48"
        assert body["package"]["body_width_mm"] is None
        assert body["package"]["body_height_mm"] is None
        assert body["kicad_footprint"] is None
    inst.shutdown()


def test_no_credentials_401_in_on_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app import create_app

    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    REMOTE = ("100.64.1.2", 51234)
    with TestClient(create_app(inst), client=REMOTE) as c:
        r = c.get("/v1/openpnp/part/C100000")
    assert r.status_code == 401
    inst.shutdown()
