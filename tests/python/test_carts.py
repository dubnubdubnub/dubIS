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
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    a = carts.create(conn, data_dir, "A")
    b = carts.create(conn, data_dir, "B")
    assert carts.get_active(data_dir, "local") is None
    carts.set_active(conn, data_dir, "local", a["id"])
    carts.set_active(conn, data_dir, "mcp@ci", b["id"])
    assert carts.get_active(data_dir, "local") == a["id"]
    assert carts.get_active(data_dir, "mcp@ci") == b["id"]


def test_rename_delete_set_active_raise_on_missing_cart(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    with pytest.raises(DubISError):
        carts.rename(conn, data_dir, "cart_nope", "New Name")
    with pytest.raises(DubISError):
        carts.delete(conn, data_dir, "cart_nope")
    with pytest.raises(DubISError):
        carts.set_active(conn, data_dir, "local", "cart_nope")


def test_delete_prunes_active_pointer_entries(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    a = carts.create(conn, data_dir, "A")
    b = carts.create(conn, data_dir, "B")
    carts.set_active(conn, data_dir, "local", a["id"])
    carts.set_active(conn, data_dir, "mcp@ci", a["id"])
    carts.set_active(conn, data_dir, "other", b["id"])

    carts.delete(conn, data_dir, a["id"])

    # Every pointer to the deleted cart is pruned; unrelated pointers survive.
    assert carts.get_active(data_dir, "local") is None
    assert carts.get_active(data_dir, "mcp@ci") is None
    assert carts.get_active(data_dir, "other") == b["id"]


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


# ── board_count ──────────────────────────────────────────────────────────────


def test_a_new_cart_builds_one_board_by_default(tmp_path):
    """Not zero: a cart for zero boards would zero every derived quantity."""
    conn = _mk_conn(tmp_path)
    assert carts.create(conn, str(tmp_path), "Cart")["board_count"] == 1


def test_board_count_survives_the_json_round_trip(tmp_path):
    """carts.json is the source of truth; the count has to be in it, or a
    5,000-piece line becomes unexplainable the moment the cache is deleted."""
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    cart = carts.create(conn, data_dir, "Glasgow revD0")
    carts.set_board_count(conn, data_dir, cart["id"], 25)

    with open(f"{data_dir}/carts.json", encoding="utf-8") as f:
        assert json.load(f)[0]["board_count"] == 25

    fresh = _mk_conn(tmp_path)
    carts.load_into_db(fresh, data_dir)
    assert carts.get(fresh, cart["id"])["board_count"] == 25


def test_set_board_count_returns_the_updated_cart(tmp_path):
    conn = _mk_conn(tmp_path)
    updated = carts.set_board_count(
        conn, str(tmp_path), carts.create(conn, str(tmp_path), "Cart")["id"], 10)
    assert updated["board_count"] == 10


def test_a_missing_board_count_in_json_loads_as_one(tmp_path):
    """Carts written before the column existed must still load."""
    data_dir = str(tmp_path)
    with open(f"{data_dir}/carts.json", "w", encoding="utf-8") as f:
        json.dump([{"id": "cart_legacy", "name": "Old", "created_at": "2026-01-01T00:00:00",
                    "items": []}], f)
    conn = _mk_conn(tmp_path)
    carts.load_into_db(conn, data_dir)
    assert carts.get(conn, "cart_legacy")["board_count"] == 1


@pytest.mark.parametrize("bad", [0, -5, None, "many", ""])
def test_a_nonsensical_stored_board_count_loads_as_one_rather_than_failing(tmp_path, bad):
    """Forgiving on read: a cart that fails to load is worse than a cart that
    quietly builds one board. Input validation lives at the API boundary."""
    assert carts.clean_board_count(bad) == 1


def test_a_fractional_board_count_truncates_rather_than_rounding(tmp_path):
    assert carts.clean_board_count(25.9) == 25


def test_set_board_count_rejects_an_unknown_cart(tmp_path):
    conn = _mk_conn(tmp_path)
    with pytest.raises(DubISError):
        carts.set_board_count(conn, str(tmp_path), "cart_nope", 5)


def test_board_count_reaches_an_existing_cache_by_migration(tmp_path):
    """`carts` is not in create_schema's stale-version drop list, so neither a
    version bump nor CREATE TABLE IF NOT EXISTS would add the column to a cache
    that already exists. Only the ALTER does."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE carts (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO cache_meta VALUES ('schema_version','7');
        INSERT INTO carts VALUES ('cart_old','Glasgow revD0','2026-08-01T00:00:00');
    """)
    conn.commit()

    cache_db.create_schema(conn)
    assert carts.get(conn, "cart_old")["board_count"] == 1

    cache_db.create_schema(conn)  # idempotent: a duplicate-column ALTER is not an error
    assert carts.get(conn, "cart_old")["board_count"] == 1
