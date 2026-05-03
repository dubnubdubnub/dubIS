"""Tests for purchase_orders module."""
import csv
import io
import os

import pytest

import purchase_orders as po


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def base_dir(tmp_path):
    """A data dir with the canonical layout."""
    d = tmp_path / "data"
    d.mkdir()
    (d / "sources").mkdir()
    return str(d)


@pytest.fixture
def csv_path(base_dir):
    return os.path.join(base_dir, "purchase_orders.csv")


class TestCreatePO:
    def test_create_po_with_source_file(self, base_dir, csv_path):
        new_po = po.create_purchase_order(
            csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_mdt_8a3f",
            source_file_bytes=_png_bytes(),
            source_file_ext=".png",
            purchase_date="2026-04-15",
            notes="apr order",
        )
        assert new_po["po_id"].startswith("po_")
        assert new_po["vendor_id"] == "v_mdt_8a3f"
        assert new_po["source_file_hash"]
        assert new_po["source_file_ext"] == ".png"
        # File exists on disk
        target = os.path.join(base_dir, "sources",
                              new_po["source_file_hash"] + ".png")
        assert os.path.isfile(target)

    def test_create_po_without_source_file(self, base_dir, csv_path):
        new_po = po.create_purchase_order(
            csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_unknown",
            source_file_bytes=None,
            source_file_ext=None,
            purchase_date="2026-04-15",
            notes="manual entry",
        )
        assert new_po["source_file_hash"] == ""
        assert new_po["source_file_ext"] == ""

    def test_create_writes_to_csv(self, base_dir, csv_path):
        po.create_purchase_order(
            csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_unknown",
            source_file_bytes=None,
            source_file_ext=None,
            purchase_date="2026-04-15",
            notes="",
        )
        with open(csv_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["vendor_id"] == "v_unknown"


class TestListPO:
    def test_list_returns_all(self, base_dir, csv_path):
        po.create_purchase_order(csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_mdt", source_file_bytes=None, source_file_ext=None,
            purchase_date="2026-04-15", notes="")
        po.create_purchase_order(csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_other", source_file_bytes=None, source_file_ext=None,
            purchase_date="2026-04-16", notes="")
        rows = po.list_purchase_orders(csv_path)
        assert len(rows) == 2

    def test_list_when_file_missing_returns_empty(self, csv_path):
        assert po.list_purchase_orders(csv_path) == []


class TestUpdateAndDelete:
    def test_update_metadata(self, base_dir, csv_path):
        new_po = po.create_purchase_order(csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_mdt", source_file_bytes=None, source_file_ext=None,
            purchase_date="2026-04-15", notes="")
        po.update_purchase_order(csv_path, new_po["po_id"],
            vendor_id="v_other", purchase_date="2026-04-20", notes="updated")
        rows = po.list_purchase_orders(csv_path)
        assert rows[0]["vendor_id"] == "v_other"
        assert rows[0]["purchase_date"] == "2026-04-20"
        assert rows[0]["notes"] == "updated"

    def test_delete_removes_row_and_orphans_file(self, base_dir, csv_path):
        new_po = po.create_purchase_order(csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_mdt", source_file_bytes=_png_bytes(),
            source_file_ext=".png",
            purchase_date="2026-04-15", notes="")
        target = os.path.join(base_dir, "sources",
                              new_po["source_file_hash"] + ".png")
        assert os.path.isfile(target)
        po.delete_purchase_order(csv_path,
                                 os.path.join(base_dir, "sources"),
                                 new_po["po_id"])
        # File gone (no other PO references this hash)
        assert not os.path.isfile(target)
        # CSV row gone
        assert po.list_purchase_orders(csv_path) == []

    def test_delete_keeps_file_referenced_by_other_po(self, base_dir, csv_path):
        png = _png_bytes()
        po1 = po.create_purchase_order(csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_mdt", source_file_bytes=png, source_file_ext=".png",
            purchase_date="2026-04-15", notes="")
        po2 = po.create_purchase_order(csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_other", source_file_bytes=png, source_file_ext=".png",
            purchase_date="2026-04-16", notes="")
        # Same content → same hash
        assert po1["source_file_hash"] == po2["source_file_hash"]
        target = os.path.join(base_dir, "sources",
                              po1["source_file_hash"] + ".png")
        po.delete_purchase_order(csv_path,
                                 os.path.join(base_dir, "sources"),
                                 po1["po_id"])
        # File still exists (po2 references it)
        assert os.path.isfile(target)


class TestResolveSourcePath:
    def test_resolve_existing(self, base_dir, csv_path):
        new_po = po.create_purchase_order(csv_path=csv_path,
            sources_dir=os.path.join(base_dir, "sources"),
            vendor_id="v_mdt", source_file_bytes=_png_bytes(),
            source_file_ext=".png",
            purchase_date="2026-04-15", notes="")
        path = po.resolve_source_path(
            os.path.join(base_dir, "sources"), new_po["po_id"], csv_path)
        assert path is not None
        assert os.path.isfile(path)

    def test_resolve_missing_returns_none(self, base_dir, csv_path):
        assert po.resolve_source_path(
            os.path.join(base_dir, "sources"), "po_nonexistent", csv_path) is None
