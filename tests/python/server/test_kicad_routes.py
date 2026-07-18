"""`/v1/kicad/*` -- KiCad HTTP Library protocol contract tests.

Design doc: docs/plans/2026-07-17-phase4-kicad-design.md §1.
Gating seams (category resolution = Task 5, eligibility bucket-default =
Task 4) are documented in domain/kicad_view.py; this task exercises the
override-driven paths those seams already support (explicit category_id,
explicit eligible_override).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_KICAD_MAPPING_PATH = REPO_ROOT / "data" / "kicad_mapping.json"

_CATEGORY_1 = {
    "id": "1",
    "name": "Passives/Capacitors/Ceramic",
    "source": "categorize_fallback",
    "categorize_bucket": "Passives - Capacitors",
    "jlcpcb_catalog_name": None,
    "default_symbol": "Device:C",
    "default_footprint_from_package": True,
    "default_reference": "C",
}

# No default_symbol -> any member without a per-SKU kicad_symbol override is
# invisible (design doc §3 point 2) -> zero visible members -> category
# itself absent from categories.json.
_CATEGORY_2 = {
    "id": "2",
    "name": "Development Boards, Kits, Programmers",
    "source": "categorize_fallback",
    "categorize_bucket": "Development Boards, Kits, Programmers",
    "jlcpcb_catalog_name": None,
    "default_symbol": None,
    "default_footprint_from_package": False,
    "default_reference": None,
}


def _mapping():
    return {
        "version": 1,
        "categories": [_CATEGORY_1, _CATEGORY_2],
        "part_overrides": {
            # Visible: category 1 has a default_symbol, no eligibility override.
            "C100000": {
                "category_id": "1", "kicad_symbol": None, "kicad_footprint": None,
                "kicad_datasheet": None, "eligible_override": None,
            },
            # Force-excluded despite otherwise-visible category 1.
            "C100001": {
                "category_id": "1", "kicad_symbol": None, "kicad_footprint": None,
                "kicad_datasheet": None, "eligible_override": False,
            },
            # In category 2, which has no default_symbol and no per-SKU
            # override -> invisible (no symbol).
            "C100002": {
                "category_id": "2", "kicad_symbol": None, "kicad_footprint": None,
                "kicad_datasheet": None, "eligible_override": None,
            },
            # C100003 intentionally has NO entry here -> unresolved category.
        },
        "part_category_cache": {},
    }


def _seed(tmp_path):
    """InventoryApi wired to tmp_path, seeded with a ledger + kicad_mapping.json
    covering: one visible SKU, one eligibility-force-excluded SKU, one
    symbol-unresolved SKU, one category-unresolved SKU."""
    api = make_api(tmp_path)
    write_ledger(api, [
        make_part(
            lcsc="C100000", mpn="CL10B104KB8NNNC", qty=100,
            desc="100nF ±10% 16V X7R 0603 MLCC", pkg="0603",
        ) | {"Manufacturer": "Samsung Electro-Mechanics"},
        make_part(lcsc="C100001", mpn="OTHER-CAP-1", desc="Some other cap", pkg="0603"),
        make_part(lcsc="C100002", mpn="DEV-BOARD-1", desc="A dev board", pkg="THT"),
        make_part(lcsc="C100003", mpn="UNMAPPED-1", desc="Nothing maps to this", pkg="0402"),
    ])
    path = os.path.join(api.base_dir, "kicad_mapping.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_mapping(), f)
    return api


@pytest.fixture
def kicad_api(tmp_path):
    return _seed(tmp_path)


@pytest.fixture
def kicad_client(kicad_api):
    with TestClient(create_app(kicad_api)) as c:
        yield c
    kicad_api.shutdown()


def _assert_all_str_leaves(value):
    """Recursively assert every scalar leaf in a JSON-decoded structure is a str."""
    if isinstance(value, dict):
        for v in value.values():
            _assert_all_str_leaves(v)
    elif isinstance(value, list):
        for v in value:
            _assert_all_str_leaves(v)
    else:
        assert isinstance(value, str), f"non-string leaf: {value!r} ({type(value)})"


# ── Root ─────────────────────────────────────────────────────────────────────


def test_root_shape(kicad_client):
    r = kicad_client.get("/v1/kicad/")
    assert r.status_code == 200
    assert r.json() == {"categories": "", "parts": ""}


# ── Categories ───────────────────────────────────────────────────────────────


def test_categories_all_string_leaves_and_zero_member_category_omitted(kicad_client):
    r = kicad_client.get("/v1/kicad/categories.json")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    _assert_all_str_leaves(body)

    ids = {c["id"] for c in body}
    # Category 1 has a visible member (C100000) -> present.
    assert "1" in ids
    # Category 2's only member (C100002) is symbol-unresolved -> zero
    # visible members -> the category itself is absent, not present-empty.
    assert "2" not in ids


def test_categories_shape_keys(kicad_client):
    r = kicad_client.get("/v1/kicad/categories.json")
    body = r.json()
    cat1 = next(c for c in body if c["id"] == "1")
    assert set(cat1.keys()) == {"id", "name", "description"}
    assert cat1["name"] == "Passives/Capacitors/Ceramic"


# ── Parts by category ───────────────────────────────────────────────────────


def test_parts_by_category_compact_shape_and_all_strings(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/category/1.json")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    _assert_all_str_leaves(body)

    ids = {p["id"] for p in body}
    assert ids == {"C100000"}  # C100001 is force-excluded, absent

    part = body[0]
    assert set(part.keys()) == {"id", "name", "description", "keywords", "footprint_filters"}
    assert "fields" not in part
    assert "symbolIdStr" not in part


def test_parts_by_category_excludes_symbol_unresolved_members(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/category/2.json")
    assert r.status_code == 200
    assert r.json() == []


def test_parts_by_unknown_category_returns_empty_list_not_404(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/category/999.json")
    assert r.status_code == 200
    assert r.json() == []


# ── Part detail ──────────────────────────────────────────────────────────────


def test_part_detail_full_shape_and_string_encoding(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/C100000.json")
    assert r.status_code == 200
    body = r.json()
    _assert_all_str_leaves(body)

    assert body["id"] == "C100000"
    assert body["name"] == "CL10B104KB8NNNC"
    assert body["symbolIdStr"] == "Device:C"
    assert body["description"] == "100nF ±10% 16V X7R 0603 MLCC"
    assert body["exclude_from_bom"] == "False"
    assert body["exclude_from_board"] == "False"
    assert body["exclude_from_sim"] == "False"
    assert isinstance(body["footprint_filters"], list)
    assert set(body.keys()) == {
        "id", "name", "symbolIdStr", "description", "keywords",
        "exclude_from_bom", "exclude_from_board", "exclude_from_sim",
        "footprint_filters", "fields",
    }


def test_part_detail_fixed_visible_field_set(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/C100000.json")
    fields = r.json()["fields"]

    assert fields["Value"]["visible"] == "True"
    assert fields["MPN"]["visible"] == "True"
    assert fields["MPN"]["value"] == "CL10B104KB8NNNC"
    assert fields["LCSC"]["visible"] == "True"
    assert fields["LCSC"]["value"] == "C100000"
    assert fields["datasheet"]["visible"] == "True"

    assert fields["footprint"]["visible"] == "False"
    assert fields["Manufacturer"]["visible"] == "False"
    assert fields["Manufacturer"]["value"] == "Samsung Electro-Mechanics"


def test_part_detail_never_exposes_price_po_qty_or_section(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/C100000.json")
    body = r.json()
    dumped = json.dumps(body)

    leaked_keys = ("unit_price", "ext_price", "primary_vendor_id", "po_history", "qty", "section")
    for key in leaked_keys:
        assert key not in body
        assert key not in body.get("fields", {})
        assert key not in dumped, f"{key!r} leaked somewhere in the response body"


def test_part_detail_eligible_override_false_is_404(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/C100001.json")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"
    assert "error" in body and "detail" in body


def test_part_detail_symbol_unresolved_is_404(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/C100002.json")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_part_detail_unresolved_category_is_404(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/C100003.json")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_part_detail_unknown_id_is_404(kicad_client):
    r = kicad_client.get("/v1/kicad/parts/DOES-NOT-EXIST.json")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


# ── Auth integration (proves Task 1's Token-scheme widening reaches this router) ──


def test_auth_on_mode_no_token_is_401(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "kicad-user:secret123")
    api = _seed(tmp_path)
    with TestClient(create_app(api), client=("100.64.1.2", 51234)) as c:
        r = c.get("/v1/kicad/categories.json")
    assert r.status_code == 401
    api.shutdown()


# ── Task 5: categorize.py-fallback category resolution, full HTTP flow ──────


_RESISTOR_CATEGORY = {
    "id": "10",
    "name": "Passives/Resistors",
    "source": "categorize_fallback",
    "categorize_bucket": "Passives - Resistors > Chip Resistors",
    "jlcpcb_catalog_name": None,
    "default_symbol": "Device:R",
    "default_footprint_from_package": True,
    "default_reference": "R",
}


def test_resistor_with_no_override_resolves_via_categorize_fallback_end_to_end(tmp_path):
    """A SKU with NO explicit kicad_mapping.json part_overrides entry, whose
    description categorize.py buckets as a resistor, resolves a category
    purely via the Task 5 fallback chain, gets a symbol from that category's
    default_symbol, passes the (non-dev-board) eligibility default -- and
    therefore appears in categories.json, parts/category/{id}.json, and
    parts/{id}.json (200, not 404)."""
    api = make_api(tmp_path)
    write_ledger(api, [
        make_part(
            lcsc="C900000", mpn="RC0402FR-0710KL", qty=50,
            desc="RES SMD 10K OHM 1% 1/10W 0402", pkg="0402",
        ) | {"Manufacturer": "Yageo"},
    ])
    mapping = {
        "version": 1,
        "categories": [_RESISTOR_CATEGORY],
        "part_overrides": {},  # No override for C900000 -- pure fallback.
        "part_category_cache": {},
    }
    path = os.path.join(api.base_dir, "kicad_mapping.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f)

    with TestClient(create_app(api)) as c:
        cats = c.get("/v1/kicad/categories.json").json()
        assert {cat["id"] for cat in cats} == {"10"}

        members = c.get("/v1/kicad/parts/category/10.json").json()
        assert {p["id"] for p in members} == {"C900000"}

        detail = c.get("/v1/kicad/parts/C900000.json")
        assert detail.status_code == 200
        body = detail.json()
        assert body["symbolIdStr"] == "Device:R"
        assert body["fields"]["MPN"]["value"] == "RC0402FR-0710KL"
    api.shutdown()


def test_auth_on_mode_token_scheme_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", "kicad-user:secret123")
    api = _seed(tmp_path)
    with TestClient(create_app(api), client=("100.64.1.2", 51234)) as c:
        r = c.get(
            "/v1/kicad/categories.json",
            headers={"Authorization": "Token secret123"},
        )
    assert r.status_code == 200
    api.shutdown()


# ── Task 6 seed fix: real data/kicad_mapping.json now resolves dev-boards ───


def test_real_seed_has_dev_board_category_matching_categorize_bucket_literally():
    """The Task 6 review fix, checked directly against the shipped seed
    file: `data/kicad_mapping.json` must carry a `categorize_fallback`
    category whose `categorize_bucket` is the *literal* string
    `categorize.py`'s CATEGORY_RULES produces for dev boards -- the same
    literal `domain/kicad_view.py::_DEFAULT_EXCLUDED_BUCKET` matches
    against. Without this row, no dev-board SKU can ever resolve a
    category at all (regardless of any per-SKU override), which is exactly
    the gap this fix closes."""
    with open(REAL_KICAD_MAPPING_PATH, encoding="utf-8") as f:
        real_mapping = json.load(f)

    dev_board_cats = [
        cat for cat in real_mapping["categories"]
        if cat.get("categorize_bucket") == "Development Boards, Kits, Programmers"
    ]
    assert len(dev_board_cats) == 1, (
        "expected exactly one seeded category for the "
        "'Development Boards, Kits, Programmers' bucket"
    )
    cat = dev_board_cats[0]
    assert cat["source"] == "categorize_fallback"
    # Opt-in-per-SKU posture (design doc §2.2): the category itself confers
    # no symbol -- only an explicit per-SKU kicad_symbol override does.
    assert cat["default_symbol"] is None


def test_esp32_rescue_against_real_seed_data(tmp_path):
    """The ESP32-solder-down-module rescue (design doc §3 point 3 / binding
    decision 3), proven against the REAL shipped `data/kicad_mapping.json`
    seed -- not a hand-rolled test category. Before the Task 6 seed fix,
    a dev-board SKU resolved category=None (the seed had no row for the
    default-excluded bucket at all), so `eligible_override: true` had
    nothing to force-include *within* and the SKU stayed invisible
    regardless. With the seed fix, the bucket resolves to a real category
    id, and a per-SKU `eligible_override: true` + `kicad_symbol` override
    is enough to make an otherwise-excluded solder-down module visible."""
    with open(REAL_KICAD_MAPPING_PATH, encoding="utf-8") as f:
        real_mapping = json.load(f)

    dev_board_cat_id = next(
        cat["id"] for cat in real_mapping["categories"]
        if cat.get("categorize_bucket") == "Development Boards, Kits, Programmers"
    )

    api = make_api(tmp_path)
    write_ledger(api, [
        # Description/MPN trip categorize.py's dev-board rule (an ESP32
        # module, sold in dev-kit form, gets lumped in with real dev boards
        # by the shelf taxonomy -- exactly the false-positive this override
        # exists to correct).
        make_part(
            lcsc="", mpn="ESP32-WROOM-32E-N4", qty=8,
            desc="ESP32-WROOM-32E-N4 WiFi/BT SoM development board module",
            pkg="SMD",
        ),
    ])
    mapping = dict(real_mapping)
    mapping["part_overrides"] = {
        "ESP32-WROOM-32E-N4": {
            "category_id": None,  # resolves via categorize.py fallback, not an override
            "kicad_symbol": "RF_Module:ESP32-WROOM-32",
            "kicad_footprint": None,
            "kicad_datasheet": None,
            "eligible_override": True,  # force-include despite the excluded bucket
        },
    }
    path = os.path.join(api.base_dir, "kicad_mapping.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f)

    with TestClient(create_app(api)) as c:
        detail = c.get("/v1/kicad/parts/ESP32-WROOM-32E-N4.json")
        assert detail.status_code == 200
        body = detail.json()
        assert body["symbolIdStr"] == "RF_Module:ESP32-WROOM-32"

        members = c.get(f"/v1/kicad/parts/category/{dev_board_cat_id}.json").json()
        assert {m["id"] for m in members} == {"ESP32-WROOM-32E-N4"}

        cats = c.get("/v1/kicad/categories.json").json()
        assert dev_board_cat_id in {cat["id"] for cat in cats}
    api.shutdown()
