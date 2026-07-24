import csv
import io
import cart_export


ITEMS = [
    {"ref": "C15742", "part_id": "C15742", "raw": None, "qty": 5, "target_distributor": "lcsc"},
    {"ref": "raw:abc", "part_id": None, "raw": {"mpn": "NOPN", "description": "d"}, "qty": 3, "target_distributor": "lcsc"},
]


def _resolve_pn(part_id, distributor):
    return {"C15742": "C15742"}.get(part_id) if distributor == "lcsc" else None


def _part_meta(part_id):
    return {"mpn": "STM32", "manufacturer": "ST", "package": "LQFP-64", "description": "MCU"} if part_id else {}


def test_lcsc_csv_has_expected_header_and_resolved_rows():
    out = cart_export.build(ITEMS, "lcsc", "csv", _resolve_pn, _part_meta)
    reader = list(csv.reader(io.StringIO(out["content"])))
    assert reader[0] == ["Index", "LCSC#", "MPN", "Manufacturer", "Package", "Customer #",
                         "Description", "RoHS", "Quantity", "MOQ", "Multiple",
                         "Unit Price($)", "Extended Price($)", "Product Link"]
    assert reader[1][1] == "C15742" and reader[1][8] == "5"
    assert len(reader) == 2  # header + 1 resolved row (raw item unresolved)
    assert out["unresolved"][0]["ref"] == "raw:abc"


def test_paste_format():
    out = cart_export.build(ITEMS, "lcsc", "paste", _resolve_pn, _part_meta)
    assert out["content"] == "C15742\t5"


def test_digikey_header():
    out = cart_export.build(ITEMS, "digikey", "csv", lambda p, d: "DK-1" if p else None, _part_meta)
    header = list(csv.reader(io.StringIO(out["content"])))[0]
    assert header[1] == "DigiKey Part #" and header[6] == "Quantity"
