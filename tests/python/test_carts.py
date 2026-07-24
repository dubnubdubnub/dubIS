import json
import sqlite3

import pytest

import cache_db
import carts
from dubis_errors import DubISError


def _mk_conn(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cache_db.create_schema(conn)
    return conn


def test_create_list_get_rename_delete_roundtrip(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)

    c = carts.create(conn, data_dir, "My Cart")
    assert c["name"] == "My Cart"
    assert c["items"] == []
    assert c["id"].startswith("cart_")

    assert [x["id"] for x in carts.list_carts(conn)] == [c["id"]]
    assert carts.get(conn, c["id"])["name"] == "My Cart"

    carts.rename(conn, data_dir, c["id"], "Renamed")
    assert carts.get(conn, c["id"])["name"] == "Renamed"

    # JSON is the source of truth and reflects the rename
    with open(f"{data_dir}/carts.json", encoding="utf-8") as f:
        assert json.load(f)[0]["name"] == "Renamed"

    carts.delete(conn, data_dir, c["id"])
    assert carts.list_carts(conn) == []


def test_load_into_db_restores_from_json(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "Persist")

    # Simulate cache drop: fresh in-memory DB, reload from JSON
    conn2 = _mk_conn(tmp_path)
    carts.load_into_db(conn2, data_dir)
    assert carts.get(conn2, c["id"])["name"] == "Persist"


def test_item_add_update_remove_clear(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "C")

    carts.add_item(conn, data_dir, c["id"], part_id="C15742", qty=5, target_distributor="lcsc")
    cart = carts.get(conn, c["id"])
    assert len(cart["items"]) == 1
    it = cart["items"][0]
    assert it["ref"] == "C15742" and it["qty"] == 5 and it["target_distributor"] == "lcsc"

    # re-add same ref => qty is SET, not added
    carts.add_item(conn, data_dir, c["id"], part_id="C15742", qty=8)
    assert carts.get(conn, c["id"])["items"][0]["qty"] == 8

    # raw item gets a hashed ref
    carts.add_item(conn, data_dir, c["id"], raw={"mpn": "X", "description": "d"}, qty=2)
    refs = {i["ref"] for i in carts.get(conn, c["id"])["items"]}
    assert any(r.startswith("raw:") for r in refs)

    carts.update_item(conn, data_dir, c["id"], "C15742", qty=3)
    assert next(i for i in carts.get(conn, c["id"])["items"] if i["ref"] == "C15742")["qty"] == 3

    carts.remove_item(conn, data_dir, c["id"], "C15742")
    assert all(i["ref"] != "C15742" for i in carts.get(conn, c["id"])["items"])

    carts.clear(conn, data_dir, c["id"])
    assert carts.get(conn, c["id"])["items"] == []


def test_item_ops_raise_on_missing_cart(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    with pytest.raises(DubISError):
        carts.add_item(conn, data_dir, "cart_nope", part_id="X", qty=1)
    with pytest.raises(DubISError):
        carts.update_item(conn, data_dir, "cart_nope", "X", qty=2)
    with pytest.raises(DubISError):
        carts.remove_item(conn, data_dir, "cart_nope", "X")
    with pytest.raises(DubISError):
        carts.clear(conn, data_dir, "cart_nope")


def test_active_pointer_is_per_identity(tmp_path):
    data_dir = str(tmp_path)
    assert carts.get_active(data_dir, "local") is None
    carts.set_active(data_dir, "local", "cart_a")
    carts.set_active(data_dir, "mcp@ci", "cart_b")
    assert carts.get_active(data_dir, "local") == "cart_a"
    assert carts.get_active(data_dir, "mcp@ci") == "cart_b"


def _pd(mapping):
    return lambda pid: mapping.get(pid, [])


def test_split_by_distributor_moves_matching_lines(tmp_path):
    conn = _mk_conn(tmp_path); data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "src")
    carts.add_item(conn, data_dir, c["id"], part_id="A", qty=1, target_distributor="lcsc")
    carts.add_item(conn, data_dir, c["id"], part_id="B", qty=1, target_distributor="digikey")
    res = carts.split_by_distributor(conn, data_dir, c["id"], "lcsc", "lcsc cart",
                                     remove_from_source=True, part_distributors=_pd({}))
    assert [i["part_id"] for i in res["new"]["items"]] == ["A"]
    assert [i["part_id"] for i in res["source"]["items"]] == ["B"]


def test_split_moves_target_unset_sourceable_lines(tmp_path):
    conn = _mk_conn(tmp_path); data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "src")
    carts.add_item(conn, data_dir, c["id"], part_id="A", qty=1)
    carts.add_item(conn, data_dir, c["id"], part_id="B", qty=1)
    res = carts.split_by_distributor(conn, data_dir, c["id"], "lcsc", "lcsc cart",
                                     remove_from_source=True,
                                     part_distributors=_pd({"A": ["lcsc", "digikey"], "B": ["mouser"]}))
    assert [i["part_id"] for i in res["new"]["items"]] == ["A"]
    assert [i["part_id"] for i in res["source"]["items"]] == ["B"]


def test_consolidate_sets_target_where_sourceable(tmp_path):
    conn = _mk_conn(tmp_path); data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "c")
    carts.add_item(conn, data_dir, c["id"], part_id="A", qty=1)
    carts.add_item(conn, data_dir, c["id"], part_id="B", qty=1)
    res = carts.consolidate(conn, data_dir, c["id"], "lcsc",
                            part_distributors=_pd({"A": ["lcsc", "digikey"], "B": ["mouser"]}))
    items = {i["part_id"]: i["target_distributor"] for i in res["cart"]["items"]}
    assert items["A"] == "lcsc" and items["B"] != "lcsc"
    assert "B" in [u for u in res["unresolved"]] or any("B" == r for r in res["unresolved"])
