"""`POST /v1/parts` — a part with identity and no purchase.

The gap, in the order a person hits it: `parts adjust --adj-type add` refuses a
part the store does not know (deliberately), `--adj-type set` creates one with
no MPN, no manufacturer, no package, no description, and `PATCH /v1/parts/{key}`
then cannot fix it because `update_part_fields` only edits rows that are already
in the purchase ledger. The anonymous part is anonymous forever.

The only route that ever accepted identity was `POST /v1/purchases/import`,
which records a purchase that never happened — a ledger row *and* a price
observation. So moving stock between two dubIS servers meant choosing between an
anonymous part and a fabricated purchase. These tests pin the third option, and
the one property that makes it the third option rather than a rename of the
second: **nothing is written to `events/`**.

The price-observation assertions are the point of the file. A test that only
checked "the part has a description now" would pass against `import_purchases`,
which is exactly the behaviour being replaced.
"""

from __future__ import annotations

import csv
import os

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

ANON = "TMR2615F-AAC-1.500-500"
OBSERVATIONS = "price_observations.csv"


@pytest.fixture
def api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C1000", qty=10)])
    return inst


@pytest.fixture
def client(api):
    with TestClient(create_app(api)) as c:
        c.api = api
        yield c
    api.shutdown()


def rows(client) -> list[dict]:
    return client.get("/v1/parts").json()["inventory"]


def row_for(client, key: str) -> dict:
    for item in rows(client):
        if key in {item.get(f) for f in ("lcsc", "mpn", "digikey", "mouser", "pololu")}:
            return item
    raise AssertionError(f"no inventory row names {key!r}")


def ledger_rows(api) -> list[dict]:
    with open(api.input_csv, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def observations_path(api) -> str:
    return os.path.join(api.events_dir, OBSERVATIONS)


# ── identity ─────────────────────────────────────────────────────────────────


def test_create_carries_every_identity_column(client):
    response = client.post("/v1/parts", json={
        "lcsc": "C99999", "mpn": ANON, "digikey": "628-1234-ND",
        "mouser": "949-TMR2615F", "pololu": "4598",
        "manufacturer": "MultiDimension", "package": "SOIC-8",
        "description": "Linear Hall-Effect Sensor",
    })
    assert response.status_code == 200, response.text
    detail = response.json()["detail"]
    assert detail["created"] is True
    # Keyed by the LCSC number: the identity derives its own key, the same
    # precedence every other caller uses.
    assert detail["part_key"] == "C99999"

    row = row_for(client, "C99999")
    assert row["mpn"] == ANON
    assert row["digikey"] == "628-1234-ND"
    assert row["mouser"] == "949-TMR2615F"
    assert row["pololu"] == "4598"
    assert row["manufacturer"] == "MultiDimension"
    assert row["package"] == "SOIC-8"
    assert row["description"] == "Linear Hall-Effect Sensor"


def test_create_leaves_the_part_at_zero_stock(client):
    client.post("/v1/parts", json={"mpn": "BRAND-999", "description": "nothing bought"})
    assert row_for(client, "BRAND-999")["qty"] == 0


def test_create_leaves_the_part_at_what_the_adjustments_say(client, api):
    """The stock is the adjustments', not the created row's.

    `read_and_merge` starts from the new row's Quantity 0 and
    `apply_adjustments` replays on top, so a part someone had already adjusted
    keeps exactly that quantity — which is what makes this safe to run on a
    store that already has movements for the part.
    """
    api.adjust_part("set", ANON, 5, "counted on the shelf", "cli")
    before = row_for(client, ANON)["qty"]
    assert before == 5, "the anonymous part exists with the adjustment's stock"

    response = client.post("/v1/parts", json={"part_key": ANON, "mpn": ANON,
                                              "manufacturer": "MultiDimension"})
    assert response.status_code == 200, response.text
    assert response.json()["detail"]["qty"] == 5
    assert row_for(client, ANON)["qty"] == 5


def test_an_add_adjustment_lands_on_a_created_part(client):
    """The consequence the CLI depends on: `add` was a no-op on a part with no
    ledger row, and after this route has run it is a real movement."""
    client.post("/v1/parts", json={"mpn": "NEW-1", "description": "now it is a part"})
    assert client.post("/v1/parts/NEW-1/adjust",
                       json={"adj_type": "add", "quantity": 7, "source": "t"}).status_code == 200
    assert row_for(client, "NEW-1")["qty"] == 7


# ── no purchase, and nothing that implies one ────────────────────────────────


def test_create_writes_no_price_observation(client, api):
    """The whole point of the route, asserted where a fabricated purchase shows.

    `import_purchases` -> `record_import_prices` appends to
    events/price_observations.csv for any row with a unit price. This route
    takes a unit price too — a transferred part has a known cost — and must
    still leave the events directory alone, because a price observation is the
    claim "this distributor quoted this price on this date", and nobody did.
    """
    response = client.post("/v1/parts", json={
        "mpn": "PRICED-1", "description": "moved from another server",
        "unit_price": 0.42,
    })
    assert response.status_code == 200, response.text
    assert not os.path.exists(observations_path(api)), (
        f"{OBSERVATIONS} was written: the part now looks like somebody sold it"
    )
    assert row_for(client, "PRICED-1")["unit_price"] == pytest.approx(0.42), (
        "the cost still travels — it is the ledger row's, not an observation"
    )
    assert client.get("/v1/parts/PRICED-1/prices").json() == {}


def test_importing_the_same_part_does_write_one__the_assertion_has_teeth(
    client, api,
):
    """The control for the test above.

    Without it, `test_create_writes_no_price_observation` would pass for the
    wrong reason — a broken fixture, a wrong path, an events dir nothing ever
    writes to. Same server, same unit price, the route that *does* record a
    purchase: the file appears.
    """
    assert not os.path.exists(observations_path(api))
    client.post("/v1/purchases/import", json={"rows": [{
        "Manufacture Part Number": "BOUGHT-1", "Quantity": "10",
        "Unit Price($)": "0.42", "Ext.Price($)": "4.20",
    }]})
    assert os.path.exists(observations_path(api)), (
        "the control failed: this fixture never records an observation for a "
        "real purchase, so the create test above proves nothing"
    )


def test_create_implies_no_purchase(client, api):
    client.post("/v1/parts", json={"mpn": "FREE-1", "unit_price": 1.5})

    created = [r for r in ledger_rows(api) if r.get("Manufacture Part Number") == "FREE-1"]
    assert len(created) == 1
    assert created[0]["Quantity"] == "0", "a created part bought zero of itself"
    assert created[0]["po_id"] == "", "it belongs to no purchase order"
    assert created[0]["Ext.Price($)"] == "", "no money was spent"

    assert client.get("/v1/purchase-orders").json() == []
    assert client.get("/v1/parts/FREE-1/last-po-quantity").json()["quantity"] == 0


# ── completing a part the store already has ──────────────────────────────────


def test_completing_an_anonymous_part_keeps_its_key_and_its_stock(client, api):
    """The repair case, and the trap it has to step over.

    The part exists only as a `set` adjustment, so it was never registered (the
    registry is built from ledger rows). Adding a ledger row that also carries
    an LCSC number would derive `C99999` as the key, while
    `apply_adjustments` — which keys off the raw adjustment string, with no
    registry lookup — re-materializes the OLD key as a second row and overwrites
    the parts row with it. The identity just written disappears and the stock
    ends up under a key no PN explains. Pinning the existing key is what keeps
    this to one row.
    """
    api.adjust_part("set", ANON, 5, "counted", "cli")
    assert client.get(f"/v1/parts/{ANON}/purchase-history").json()[
        "has_purchase_history"] is False

    response = client.post("/v1/parts", json={
        "part_key": ANON, "lcsc": "C99999", "mpn": ANON,
        "manufacturer": "MultiDimension", "description": "Hall sensor",
    })
    assert response.status_code == 200, response.text
    detail = response.json()["detail"]
    assert detail["part_key"] == ANON, "the part was renamed out from under its adjustments"
    assert detail["pinned_key"] is True

    matching = [r for r in rows(client) if ANON in {r.get("mpn"), r.get("lcsc")}]
    assert len(matching) == 1, f"the part split in two: {matching!r}"
    assert matching[0]["qty"] == 5
    assert matching[0]["lcsc"] == "C99999"
    assert matching[0]["description"] == "Hall sensor"


def test_create_is_idempotent_and_never_blanks_a_field(client, api):
    body = {"mpn": "DUP-1", "manufacturer": "Acme", "package": "0805",
            "description": "first words"}
    client.post("/v1/parts", json=body)
    second = client.post("/v1/parts", json={"mpn": "DUP-2", "description": "second words"})
    assert second.status_code == 200

    first = row_for(client, "DUP-2")
    assert first["manufacturer"] == ""  # never sent
    client.post("/v1/parts", json={"mpn": "DUP-2"})  # sends nothing new
    assert row_for(client, "DUP-2")["description"] == "second words"

    assert len([r for r in ledger_rows(api)
                if r.get("Manufacture Part Number") == "DUP-2"]) == 1


def test_completing_an_existing_part_does_not_move_its_price(client, api):
    write_ledger(api, [make_part(lcsc="C1000", qty=10, unit_price="0.25",
                                 ext_price="2.50")])
    api.rebuild_inventory()
    client.post("/v1/parts", json={"lcsc": "C1000", "description": "added words"})
    row = row_for(client, "C1000")
    assert row["unit_price"] == pytest.approx(0.25), (
        "a price change is PUT /v1/parts/{key}/price, which also records the "
        "observation this route exists to avoid"
    )
    assert row["description"] == "added words"


# ── refusals ─────────────────────────────────────────────────────────────────


def test_create_refuses_an_identity_with_no_part_number(client):
    response = client.post("/v1/parts", json={"description": "a part with no name"})
    assert response.status_code == 400
    assert "part number" in response.json()["error"]
    assert rows(client) and len(rows(client)) == 1, "nothing was created"


def test_create_refuses_a_key_the_identity_cannot_justify(client):
    """Naming a key is only for completing a part the store already knows.

    Inventing a part whose canonical key is a lower-precedence PN than one of
    its own numbers would make it disagree with every `derive_key` caller — the
    CLI among them — about what it is called.
    """
    response = client.post("/v1/parts", json={"part_key": "NOPE", "lcsc": "C7", "mpn": "M7"})
    assert response.status_code == 400
    assert "C7" in response.json()["error"]
    assert all(r["lcsc"] != "C7" for r in rows(client))


def test_create_refuses_a_negative_unit_price(client):
    assert client.post("/v1/parts",
                       json={"mpn": "X-1", "unit_price": -1}).status_code == 400


def test_create_reaches_the_part_by_any_of_its_numbers(client):
    client.post("/v1/parts", json={"mpn": "ANY-1", "digikey": "DK-ANY-1"})
    # The alias resolves to the same row on the next read, because the ledger
    # row registers both numbers.
    assert row_for(client, "DK-ANY-1")["mpn"] == "ANY-1"
