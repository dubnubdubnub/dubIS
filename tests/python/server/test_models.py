"""server.models: InventoryItemModel derived from domain.schema.INVENTORY_FIELDS."""

from domain.schema import INVENTORY_FIELDS
from server.models import InventoryEnvelope, InventoryItemModel


def test_field_set_matches_to_js_keys():
    expected = {f.py_key for f in INVENTORY_FIELDS if f.to_js}
    assert set(InventoryItemModel.model_fields) == expected


def test_qty_is_int():
    assert InventoryItemModel.model_fields["qty"].annotation is int


def test_unit_price_is_float():
    assert InventoryItemModel.model_fields["unit_price"].annotation is float


def test_po_history_is_list_of_str():
    assert InventoryItemModel.model_fields["po_history"].annotation == list[str]


def test_envelope_wraps_list_of_items():
    item = InventoryItemModel(
        section="", lcsc="C1", mpn="", digikey="", pololu="", mouser="",
        manufacturer="", package="", description="", qty=1, unit_price=0.0,
        ext_price=0.0, primary_vendor_id="", po_history=[],
    )
    env = InventoryEnvelope(inventory=[item])
    assert env.inventory[0].lcsc == "C1"
