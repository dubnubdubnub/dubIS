"""Tests for InventoryApi cart facade methods (Task A7)."""

import csv

import domain.pricing


def test_cart_facade_crud(api):
    c = api.create_cart("Facade Cart")
    api.add_cart_item(c["id"], part_id="C15742", qty=5)
    got = api.get_cart(c["id"])
    assert got["items"][0]["qty"] == 5
    api.remove_cart_item(c["id"], "C15742")
    assert api.get_cart(c["id"])["items"] == []


def test_add_cart_item_computes_default_qty_when_absent(api):
    c = api.create_cart("Q")
    # No price observations for this synthetic part => ladder empty => qty defaults to 1
    api.add_cart_item(c["id"], part_id="ZZZNOEXIST", qty=None)
    assert api.get_cart(c["id"])["items"][0]["qty"] == 1


def test_list_carts_and_delete(api):
    c1 = api.create_cart("A")
    c2 = api.create_cart("B")
    ids = {c["id"] for c in api.list_carts()}
    assert ids == {c1["id"], c2["id"]}
    api.delete_cart(c1["id"])
    assert {c["id"] for c in api.list_carts()} == {c2["id"]}


def test_rename_cart(api):
    c = api.create_cart("Old")
    updated = api.rename_cart(c["id"], "New")
    assert updated["name"] == "New"
    assert api.get_cart(c["id"])["name"] == "New"


def test_create_cart_default_name_when_falsy(api):
    c = api.create_cart(None)
    assert c["name"]
    c2 = api.create_cart("")
    assert c2["name"]


def test_active_cart_roundtrip(api):
    c = api.create_cart("Active")
    assert api.get_active_cart("isaac") is None
    result = api.set_active_cart("isaac", c["id"])
    assert result == {"active_cart_id": c["id"]}
    assert api.get_active_cart("isaac") == c["id"]


def test_update_and_clear_cart_item(api):
    c = api.create_cart("Cart")
    api.add_cart_item(c["id"], part_id="C1", qty=2)
    updated = api.update_cart_item(c["id"], "C1", qty=9)
    assert updated["items"][0]["qty"] == 9
    cleared = api.clear_cart(c["id"])
    assert cleared["items"] == []


def test_add_bom_missing_to_cart(api):
    c = api.create_cart("BOM Cart")
    missing = [
        {"part_id": "C1", "shortfall": 3},
        {"raw": {"mpn": "XYZ123"}, "shortfall": 2},
    ]
    result = api.add_bom_missing_to_cart(c["id"], missing)
    assert len(result["items"]) == 2


def test_split_and_consolidate_cart(api):
    c = api.create_cart("Split Source")
    api.add_cart_item(c["id"], part_id="C1", qty=1, target_distributor="lcsc")
    api.add_cart_item(c["id"], part_id="C2", qty=1, target_distributor="digikey")
    result = api.split_cart(c["id"], "lcsc", "LCSC Split", True)
    assert result["new"]["name"] == "LCSC Split"
    assert len(result["new"]["items"]) == 1
    assert len(result["source"]["items"]) == 1

    result2 = api.consolidate_cart(result["new"]["id"], "lcsc")
    assert "cart" in result2
    assert "unresolved" in result2


def test_export_cart_paste_format(api):
    c = api.create_cart("Export Cart")
    api.add_cart_item(c["id"], part_id="C1", qty=4)
    result = api.export_cart(c["id"], "lcsc", "paste")
    assert "content" in result
    assert "unresolved" in result


def test_export_cart_resolves_metadata_for_alias_part_id(api):
    """A cart line added under a distributor-specific alias key (e.g. the LCSC
    PN) rather than the registry's canonical part_id must still export with
    non-blank MPN/Manufacturer/Package/Description — _part_meta needs the same
    alias-aware resolution get_sourced_distributors() uses (Fix 3)."""
    conn = api._get_cache()
    conn.execute(
        "INSERT INTO parts (part_id, lcsc, mpn, manufacturer, package, description) "
        "VALUES ('CANON1', 'C99999', 'STM32F103', 'ST', 'LQFP-64', 'MCU') ",
    )
    conn.commit()

    c = api.create_cart("Alias Cart")
    # add under the alias (lcsc PN), not the canonical part_id
    api.add_cart_item(c["id"], part_id="C99999", qty=2)
    result = api.export_cart(c["id"], "lcsc", "csv")
    assert result["unresolved"] == []
    assert "STM32F103" in result["content"]
    assert "ST" in result["content"]
    assert "LQFP-64" in result["content"]


def test_cart_items_carry_available_distributors(api):
    """get_cart and list_carts attach `available_distributors` per item,
    derived from the part's record PNs."""
    conn = api._get_cache()
    conn.execute(
        "INSERT INTO parts (part_id, lcsc, mpn, description) "
        "VALUES ('P1', 'C111', 'MPN1', 'd1')",
    )
    conn.commit()

    c = api.create_cart("Avail")
    api.add_cart_item(c["id"], part_id="C111", qty=1)

    item = api.get_cart(c["id"])["items"][0]
    assert item["available_distributors"] == ["lcsc"]

    listed = next(x for x in api.list_carts() if x["id"] == c["id"])
    assert listed["items"][0]["available_distributors"] == ["lcsc"]


def test_available_distributors_includes_ledger_only_distributor(api):
    """A distributor a part was PURCHASED from (present only in the ledger, not
    the record PN columns) is still offered — the whole point of resolving
    through the record∪ledger union rather than record fields alone."""
    conn = api._get_cache()
    # record has ONLY lcsc; digikey column is empty
    conn.execute(
        "INSERT INTO parts (part_id, lcsc, mpn, description) "
        "VALUES ('P2', 'C222', 'MPN2', 'd2')",
    )
    conn.commit()
    # ledger row references the LCSC PN (so it matches the part) and also
    # carries a Digikey PN → digikey becomes sourceable via ledger.
    with open(api.input_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["LCSC Part Number", "Digikey Part Number", "Manufacture Part Number"])
        w.writerow(["C222", "296-DK-2-ND", "MPN2"])

    c = api.create_cart("Ledger")
    api.add_cart_item(c["id"], part_id="C222", qty=1)

    avail = api.get_cart(c["id"])["items"][0]["available_distributors"]
    assert "lcsc" in avail and "digikey" in avail


def test_raw_cart_item_has_empty_available_distributors(api):
    c = api.create_cart("Raw")
    api.add_cart_item(c["id"], raw={"mpn": "NOPART"}, qty=1)
    assert api.get_cart(c["id"])["items"][0]["available_distributors"] == []


# ── planning a part that is only in the cart ─────────────────────────────────
# The headline flow (load a BOM, add what's missing to a cart, plan it) is all
# about parts that are NOT on the shelf and have never been bought. Those have
# no record PN and no ledger row, so get_sourced_distributors returns [] for
# them -- and the plan used to report "no observed prices" for a part whose
# ladder was sitting in the observation log.

def _quote(api, part_id, distributor, ladder, packaging="Cut Tape", **extra):
    domain.pricing.record_observations(api.events_dir, [
        {"part_id": part_id, "distributor": distributor, "moq": q,
         "unit_price": p, "currency": "USD", "source": "test",
         "packaging": packaging, **extra}
        for q, p in ladder
    ])


def test_plan_prices_a_part_that_was_only_ever_quoted(api):
    c = api.create_cart("BOM")
    _quote(api, "C52923", "lcsc", [(10, 0.01), (100, 0.005), (1000, 0.002)])
    api.add_cart_item(c["id"], part_id="C52923", qty=100, per_board_qty=100)

    # Nothing catalogued this part and nothing ever bought it...
    assert api.get_sourced_distributors("C52923") == []
    # ...but it was quoted, so it can be planned.
    line = api.plan_cart(c["id"])["lines"][0]
    assert line["required_qty"] == 100
    assert line["candidates"], line["reason"]
    assert line["selected"]["distributor"] == "lcsc"
    assert line["selected"]["qty"] == 100
    assert line["selected"]["unit_price"] == 0.005


def test_plan_still_says_unpriced_when_nothing_quoted_the_part(api):
    c = api.create_cart("BOM")
    api.add_cart_item(c["id"], part_id="NEVER-QUOTED", qty=5, per_board_qty=5)
    line = api.plan_cart(c["id"])["lines"][0]
    assert line["candidates"] == []
    assert line["selected"] is None
    assert api.plan_cart(c["id"])["totals"]["unpriced"] == 1


def test_quoted_only_distributor_is_offered_on_the_line(api):
    """The plan and the line's own dropdown must agree on what is available.

    Recommending `lcsc` while `available_distributors` is empty would render a
    recommendation the row claims it cannot act on.
    """
    c = api.create_cart("BOM")
    _quote(api, "C52923", "lcsc", [(10, 0.01)])
    api.add_cart_item(c["id"], part_id="C52923", qty=10, per_board_qty=10)
    item = api.list_carts()[0]["items"][0]
    assert item["available_distributors"] == ["lcsc"]


def test_a_quoted_distributor_does_not_displace_a_sourced_one(api):
    """Sourced distributors keep coming first; quoted-only ones are appended."""
    c = api.create_cart("BOM")
    _quote(api, "C52923", "mouser", [(1, 0.05)])
    api.add_cart_item(c["id"], part_id="C52923", qty=1, per_board_qty=1)
    # Fake a record PN so the part is "sourced" from lcsc as well.
    conn = api._get_cache()
    conn.execute(
        "INSERT OR REPLACE INTO parts (part_id, lcsc, mpn) VALUES (?, ?, ?)",
        ("C52923", "C52923", "CL05A105KA5NQNC"),
    )
    conn.commit()
    assert api._cart._quotable_distributors("C52923", "C52923") == ["lcsc", "mouser"]
