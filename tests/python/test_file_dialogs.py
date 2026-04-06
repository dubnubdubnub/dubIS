"""Tests for file_dialogs module: column detection and file loading."""

import json

from file_dialogs import detect_columns, load_file


class TestDetectColumns:
    """Tests for detect_columns() — auto-detect column mapping from CSV headers."""

    def test_lcsc_order_headers(self):
        headers = [
            "LCSC Part Number", "Manufacture Part Number", "Manufacturer",
            "Customer NO.", "Package", "Description", "RoHS",
            "Quantity", "Unit Price($)", "Ext.Price($)",
        ]
        mapping = detect_columns(headers)
        assert mapping[str(headers.index("LCSC Part Number"))] == "LCSC Part Number"
        assert mapping[str(headers.index("Quantity"))] == "Quantity"
        assert mapping[str(headers.index("Unit Price($)"))] == "Unit Price($)"
        assert mapping[str(headers.index("Description"))] == "Description"

    def test_digikey_order_headers(self):
        headers = [
            "Index", "DigiKey Part Number", "Manufacturer Part Number",
            "Description", "Quantity", "Unit Price", "Extended Price",
        ]
        mapping = detect_columns(headers)
        assert mapping[str(headers.index("DigiKey Part Number"))] == "Digikey Part Number"
        assert mapping[str(headers.index("Manufacturer Part Number"))] == "Manufacture Part Number"
        assert mapping[str(headers.index("Description"))] == "Description"
        assert mapping[str(headers.index("Quantity"))] == "Quantity"
        assert mapping[str(headers.index("Unit Price"))] == "Unit Price($)"
        assert mapping[str(headers.index("Extended Price"))] == "Ext.Price($)"

    def test_mouser_order_headers(self):
        headers = [
            "Mouser No:", "Mfr. Part No.", "Manufacturer",
            "Description", "Quantity Shipped", "Unit Price(USD)",
        ]
        mapping = detect_columns(headers)
        assert mapping[str(headers.index("Mouser No:"))] == "Mouser Part Number"
        assert mapping[str(headers.index("Mfr. Part No."))] == "Manufacture Part Number"
        assert mapping[str(headers.index("Manufacturer"))] == "Manufacturer"
        assert mapping[str(headers.index("Quantity Shipped"))] == "Quantity"

    def test_shipped_qty_preferred_over_ordered(self):
        """'Shipped' quantity should take precedence over generic 'Quantity'."""
        headers = ["Part", "Quantity Ordered", "Quantity Shipped"]
        mapping = detect_columns(headers)
        # Shipped should be mapped
        shipped_idx = str(headers.index("Quantity Shipped"))
        assert mapping.get(shipped_idx) == "Quantity"

    def test_no_duplicate_target_assignments(self):
        """Each target field should be mapped at most once."""
        headers = [
            "LCSC Part Number", "Another LCSC Code", "Quantity", "Qty Shipped",
        ]
        mapping = detect_columns(headers)
        targets = list(mapping.values())
        assert len(targets) == len(set(targets))

    def test_accepts_json_string_input(self):
        headers = ["LCSC Part Number", "Quantity", "Description"]
        mapping = detect_columns(json.dumps(headers))
        assert "0" in mapping
        assert mapping["0"] == "LCSC Part Number"

    def test_empty_headers(self):
        mapping = detect_columns([])
        assert mapping == {}

    def test_unrecognized_headers(self):
        headers = ["Foo", "Bar", "Baz"]
        mapping = detect_columns(headers)
        assert mapping == {}

    def test_case_insensitive_matching(self):
        headers = ["lcsc part number", "QUANTITY", "unit price"]
        mapping = detect_columns(headers)
        assert mapping.get("0") == "LCSC Part Number"
        assert mapping.get("1") == "Quantity"
        assert mapping.get("2") == "Unit Price($)"

    def test_pololu_headers(self):
        headers = ["Pololu Item Number", "Description", "Quantity"]
        mapping = detect_columns(headers)
        assert mapping[str(headers.index("Pololu Item Number"))] == "Pololu Part Number"

    def test_mpn_column_detection(self):
        """Various MPN header formats should be detected."""
        for header in ["MPN", "Manufacturer Part Number", "Mfr Part#"]:
            mapping = detect_columns([header])
            assert mapping.get("0") == "Manufacture Part Number", f"Failed for header: {header}"

    def test_rohs_column(self):
        headers = ["Part", "RoHS Status"]
        mapping = detect_columns(headers)
        assert mapping.get("1") == "RoHS"

    def test_customer_column(self):
        headers = ["Part", "Customer Reference"]
        mapping = detect_columns(headers)
        assert mapping.get("1") == "Customer NO."

    def test_package_column(self):
        headers = ["Part", "Package Type"]
        mapping = detect_columns(headers)
        assert mapping.get("1") == "Package"

    def test_ext_price_column(self):
        headers = ["Part", "Ext. Price (USD)"]
        mapping = detect_columns(headers)
        assert mapping.get("1") == "Ext.Price($)"

    def test_digi_key_hyphenated(self):
        """'Digi-Key' with hyphen should still match."""
        headers = ["Digi-Key Part Number", "Qty"]
        mapping = detect_columns(headers)
        assert mapping.get("0") == "Digikey Part Number"

    def test_real_purchase_ledger_headers(self):
        """Test with actual headers from the fixture purchase_ledger.csv."""
        headers = [
            "Digikey Part Number", "LCSC Part Number", "Manufacture Part Number",
            "Manufacturer", "Customer NO.", "Package", "Description", "RoHS",
            "Quantity", "Unit Price($)", "Ext.Price($)",
            "Estimated lead time (business days)", "Date Code / Lot No.",
        ]
        mapping = detect_columns(headers)
        assert mapping[str(headers.index("Digikey Part Number"))] == "Digikey Part Number"
        assert mapping[str(headers.index("LCSC Part Number"))] == "LCSC Part Number"
        assert mapping[str(headers.index("Manufacture Part Number"))] == "Manufacture Part Number"
        assert mapping[str(headers.index("Manufacturer"))] == "Manufacturer"
        assert mapping[str(headers.index("Quantity"))] == "Quantity"
        assert mapping[str(headers.index("Unit Price($)"))] == "Unit Price($)"
        assert mapping[str(headers.index("Ext.Price($)"))] == "Ext.Price($)"


class TestLoadFile:
    """Tests for load_file() — loading files by path."""

    def test_loads_utf8_csv(self, tmp_path):
        path = str(tmp_path / "test.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("A,B\n1,2\n")
        result = load_file(path)
        assert result is not None
        assert result["name"] == "test.csv"
        assert result["content"] == "A,B\n1,2\n"
        assert result["directory"] == str(tmp_path)
        assert result["path"] == path

    def test_returns_none_for_missing_file(self):
        result = load_file("/nonexistent/path/to/file.csv")
        assert result is None

    def test_returns_none_for_empty_path(self):
        assert load_file("") is None

    def test_returns_none_for_none_path(self):
        assert load_file(None) is None

    def test_loads_sidecar_links(self, tmp_path):
        csv_path = str(tmp_path / "export.csv")
        links_path = str(tmp_path / "export.links.json")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("Col1,Col2\n")
        links_data = [{"url": "https://example.com", "ref": "R1"}]
        with open(links_path, "w", encoding="utf-8") as f:
            json.dump(links_data, f)

        result = load_file(csv_path)
        assert result is not None
        assert "links" in result
        assert result["links"] == links_data

    def test_no_links_key_without_sidecar(self, tmp_path):
        path = str(tmp_path / "plain.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("A\n1\n")
        result = load_file(path)
        assert "links" not in result

    def test_handles_corrupt_sidecar_gracefully(self, tmp_path):
        csv_path = str(tmp_path / "data.csv")
        links_path = str(tmp_path / "data.links.json")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("X\n1\n")
        with open(links_path, "w", encoding="utf-8") as f:
            f.write("{bad json!!")

        result = load_file(csv_path)
        assert result is not None
        # Corrupt sidecar should be silently skipped (with warning logged)
        assert "links" not in result
