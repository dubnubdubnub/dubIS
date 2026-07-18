"""End-to-end integration test for the KiCad HTTP Library protocol
(Phase 4, Task 6) -- walks the full root -> categories -> parts-by-category
-> part-detail chain using the committed `tests/fixtures/dubis.kicad_httplib`
fixture's `root_url`/`token`, against a composed server in both
`DUBIS_AUTH_MODE=off` (today's default) and `on` (using the fixture's
token via the `Token` scheme, Task 1's scheme-widening). Proves Tasks 1-5
compose correctly end to end -- not just unit-by-unit, as the rest of
`tests/python/server/test_kicad_routes.py` and `tests/python/domain/
test_kicad_view.py` already do.

Auth "on" mode is driven through `TestClient(..., client=REMOTE)` (the
established pattern in `tests/python/server/test_auth.py`) rather than a
real uvicorn socket: a real socket bound to 127.0.0.1 would have every
request resolve as the trusted `local` identity via loopback trust
(`server/auth.py`'s resolution order, step 1, ahead of the token check),
which would make a Token-scheme assertion vacuous -- exactly the
peer-spoofing knob `TestClient`'s `client=` kwarg exists to control.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "dubis.kicad_httplib"

REMOTE = ("100.64.1.9", 51234)

# Mirrors the real `data/kicad_mapping.json` seed's bucket strings exactly
# (categorize.py's parent+subcategory concatenation, `" > "`-joined) so this
# test proves composition against realistic seed shape, not a synthetic one.
_CATEGORIES = [
    {
        "id": "1", "name": "Passives/Capacitors/Ceramic", "source": "categorize_fallback",
        "categorize_bucket": "Passives - Capacitors > MLCC", "jlcpcb_catalog_name": None,
        "default_symbol": "Device:C", "default_footprint_from_package": True,
        "default_reference": "C",
    },
    {
        "id": "2", "name": "Passives/Resistors", "source": "categorize_fallback",
        "categorize_bucket": "Passives - Resistors > Chip Resistors",
        "jlcpcb_catalog_name": None, "default_symbol": "Device:R",
        "default_footprint_from_package": True, "default_reference": "R",
    },
    {
        # Task 6 seed fix: the default-excluded bucket must itself resolve
        # to a category row (design doc §1.2/§3 point 3) -- otherwise an
        # eligible_override:true on a member SKU has no category to
        # force-include *within*; the SKU stays unresolved (§3 point 1)
        # regardless of the override. `default_symbol: null` keeps the
        # opt-in-per-SKU posture: category membership alone never confers
        # visibility here, only an explicit per-SKU kicad_symbol does.
        "id": "3", "name": "Development Boards, Kits, Programmers",
        "source": "categorize_fallback",
        "categorize_bucket": "Development Boards, Kits, Programmers",
        "jlcpcb_catalog_name": None, "default_symbol": None,
        "default_footprint_from_package": False, "default_reference": None,
    },
]


def _load_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _kicad_path(root_url: str, suffix: str = "") -> str:
    """Path portion of the fixture's `root_url`, plus an optional suffix --
    proves the fixture's advertised URL shape (host/port aside) is exactly
    what `server/routes/kicad.py`'s router prefix serves."""
    return urlparse(root_url).path.rstrip("/") + suffix


def _seed(tmp_path):
    """InventoryApi wired to tmp_path, seeded with: two eligible SKUs that
    resolve a category + symbol purely via the categorize.py fallback (no
    per-SKU override needed), one excluded dev-board (default-excluded
    bucket, no override), and one unmapped SKU (categorize.py has no rule
    for it at all)."""
    api = make_api(tmp_path)
    write_ledger(api, [
        # Eligible: categorize.py -> "Passives - Capacitors > MLCC" -> category 1.
        make_part(
            lcsc="C200000", mpn="CL10B104KB8NNNC", qty=25,
            desc="100nF ±10% 16V X7R 0603 MLCC Ceramic Capacitor", pkg="0603",
        ) | {"Manufacturer": "Samsung Electro-Mechanics"},
        # Eligible: categorize.py -> "Passives - Resistors > Chip Resistors" -> category 2.
        make_part(
            lcsc="C200001", mpn="RC0402FR-0710KL", qty=100,
            desc="RES SMD 10K OHM 1% 1/10W 0402 Resistor", pkg="0402",
        ) | {"Manufacturer": "Yageo"},
        # Excluded dev-board: categorize.py -> "Development Boards, Kits,
        # Programmers" -> category 3, default-excluded, no override -> hidden.
        make_part(
            lcsc="", mpn="NUCLEO-F411RE", qty=2,
            desc="STM32 Nucleo-64 development board", pkg="THT",
        ) | {"Manufacturer": "STMicroelectronics"},
        # Unmapped: nothing in categorize.py's CATEGORY_RULES matches this
        # description -> bucket "Other" -> no kicad_mapping category carries
        # that bucket -> unresolved -> hidden.
        make_part(
            lcsc="C200002", mpn="ZZZ-MYSTERY-1", qty=5,
            desc="Unclassifiable widget thingamajig", pkg="SOT-23",
        ),
    ])
    mapping = {
        "version": 1,
        "categories": _CATEGORIES,
        "part_overrides": {},
        "part_category_cache": {},
    }
    path = os.path.join(api.base_dir, "kicad_mapping.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f)
    return api


def _walk_full_chain(client, kicad_path, headers=None):
    """Root -> categories -> parts-by-category -> part-detail, asserting
    every level is internally consistent with the level below it:
    every category id returned by categories.json is queryable via
    parts/category/{id}.json, and every part id returned there is
    queryable (200, not 404) via parts/{id}.json.

    Returns (categories, all_part_ids) for the caller's own assertions.
    """
    headers = headers or {}

    root = client.get(kicad_path("/"), headers=headers)
    assert root.status_code == 200
    assert root.json() == {"categories": "", "parts": ""}

    categories = client.get(kicad_path("/categories.json"), headers=headers).json()
    assert isinstance(categories, list) and categories, "expected >=1 visible category"

    all_part_ids: list[str] = []
    for cat in categories:
        members = client.get(
            kicad_path(f"/parts/category/{cat['id']}.json"), headers=headers,
        ).json()
        assert isinstance(members, list) and members, (
            f"category {cat['id']} listed in categories.json but has zero members"
        )
        for member in members:
            detail = client.get(kicad_path(f"/parts/{member['id']}.json"), headers=headers)
            assert detail.status_code == 200, (
                f"part {member['id']} listed under category {cat['id']} "
                f"but its own detail endpoint 404'd"
            )
            body = detail.json()
            assert body["id"] == member["id"]
            assert isinstance(body["symbolIdStr"], str) and body["symbolIdStr"]
            all_part_ids.append(member["id"])

    return categories, all_part_ids


def test_full_chain_auth_off_no_token_needed(tmp_path):
    """DUBIS_AUTH_MODE=off (today's default) -- no credentials required at
    all. Eligible SKUs appear everywhere the chain implies they should; the
    excluded dev-board and the unmapped SKU appear nowhere."""
    fixture = _load_fixture()
    api = _seed(tmp_path)

    def kicad_path(suffix=""):
        return _kicad_path(fixture["source"]["root_url"], suffix)

    with TestClient(create_app(api)) as c:
        categories, part_ids = _walk_full_chain(c, kicad_path)

        assert {cat["id"] for cat in categories} == {"1", "2"}
        assert set(part_ids) == {"C200000", "C200001"}
        assert "NUCLEO-F411RE" not in part_ids
        assert "C200002" not in part_ids

        # Independently confirm the excluded / unmapped SKUs 404 by id too.
        assert c.get(kicad_path("/parts/NUCLEO-F411RE.json")).status_code == 404
        assert c.get(kicad_path("/parts/C200002.json")).status_code == 404
    api.shutdown()


def test_full_chain_auth_on_with_fixture_token(tmp_path, monkeypatch):
    """DUBIS_AUTH_MODE=on -- the exact `.kicad_httplib` fixture's token,
    sent with the `Token` scheme, must resolve the whole chain identically
    to the off-mode walk above; no credentials at all must 401."""
    fixture = _load_fixture()
    token = fixture["source"]["token"]
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TOKENS", f"kicad-test:{token}")
    api = _seed(tmp_path)
    headers = {"Authorization": f"Token {token}"}

    def kicad_path(suffix=""):
        return _kicad_path(fixture["source"]["root_url"], suffix)

    with TestClient(create_app(api), client=REMOTE) as c:
        assert c.get(kicad_path("/categories.json")).status_code == 401

        categories, part_ids = _walk_full_chain(c, kicad_path, headers=headers)
        assert {cat["id"] for cat in categories} == {"1", "2"}
        assert set(part_ids) == {"C200000", "C200001"}
    api.shutdown()
