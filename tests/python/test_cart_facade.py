"""Tests for InventoryApi cart facade methods (Task A7)."""


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
