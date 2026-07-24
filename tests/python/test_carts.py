import json
import sqlite3

import cache_db
import carts


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
