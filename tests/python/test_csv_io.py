"""Tests for csv_io module: CSV reading, writing, migration, encoding fixes."""

import csv

from csv_io import append_csv_rows, convert_xls_to_csv, fix_double_utf8, migrate_csv_header, read_text


class TestAppendCsvRows:
    """Tests for append_csv_rows()."""

    def test_creates_new_file_with_header(self, tmp_path):
        path = str(tmp_path / "out.csv")
        fields = ["A", "B", "C"]
        rows = [{"A": "1", "B": "2", "C": "3"}]
        append_csv_rows(path, fields, rows)

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == fields
            data = list(reader)
        assert len(data) == 1
        assert data[0] == {"A": "1", "B": "2", "C": "3"}

    def test_appends_to_existing_file(self, tmp_path):
        path = str(tmp_path / "out.csv")
        fields = ["X", "Y"]
        append_csv_rows(path, fields, [{"X": "a", "Y": "b"}])
        append_csv_rows(path, fields, [{"X": "c", "Y": "d"}])

        with open(path, newline="", encoding="utf-8") as f:
            data = list(csv.DictReader(f))
        assert len(data) == 2
        assert data[0] == {"X": "a", "Y": "b"}
        assert data[1] == {"X": "c", "Y": "d"}

    def test_header_written_only_once(self, tmp_path):
        path = str(tmp_path / "out.csv")
        fields = ["Col"]
        append_csv_rows(path, fields, [{"Col": "v1"}])
        append_csv_rows(path, fields, [{"Col": "v2"}])

        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        header_count = sum(1 for line in lines if line.strip() == "Col")
        assert header_count == 1

    def test_multiple_rows_at_once(self, tmp_path):
        path = str(tmp_path / "out.csv")
        fields = ["Name", "Value"]
        rows = [
            {"Name": "R1", "Value": "10k"},
            {"Name": "R2", "Value": "4.7k"},
            {"Name": "R3", "Value": "100"},
        ]
        append_csv_rows(path, fields, rows)

        with open(path, newline="", encoding="utf-8") as f:
            data = list(csv.DictReader(f))
        assert len(data) == 3
        assert data[2]["Name"] == "R3"

    def test_empty_rows_list(self, tmp_path):
        path = str(tmp_path / "out.csv")
        fields = ["A", "B"]
        append_csv_rows(path, fields, [])

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == fields
            assert list(reader) == []

    def test_migrates_header_when_columns_added(self, tmp_path):
        path = str(tmp_path / "out.csv")
        # Write initial file with 2 columns
        old_fields = ["A", "B"]
        append_csv_rows(path, old_fields, [{"A": "1", "B": "2"}])

        # Append with a 3rd column — should trigger migration
        new_fields = ["A", "B", "C"]
        append_csv_rows(path, new_fields, [{"A": "3", "B": "4", "C": "5"}])

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {"A", "B", "C"}
            data = list(reader)
        assert len(data) == 2
        # Old row gets empty string for missing column
        assert data[0]["C"] == ""
        assert data[1] == {"A": "3", "B": "4", "C": "5"}


class TestMigrateCsvHeader:
    """Tests for migrate_csv_header()."""

    def test_noop_when_headers_match(self, tmp_path):
        path = str(tmp_path / "data.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["A", "B"])
            writer.writeheader()
            writer.writerow({"A": "1", "B": "2"})

        migrate_csv_header(path, ["A", "B"])
        # File should be unchanged (same set of headers)
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {"A", "B"}
            data = list(reader)
        assert data[0] == {"A": "1", "B": "2"}

    def test_adds_new_column(self, tmp_path):
        path = str(tmp_path / "data.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["A", "B"])
            writer.writeheader()
            writer.writerow({"A": "x", "B": "y"})

        migrate_csv_header(path, ["A", "B", "C"])
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert "C" in reader.fieldnames
            data = list(reader)
        assert data[0]["C"] == ""
        assert data[0]["A"] == "x"

    def test_removes_old_column(self, tmp_path):
        path = str(tmp_path / "data.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["A", "B", "Old"])
            writer.writeheader()
            writer.writerow({"A": "1", "B": "2", "Old": "gone"})

        migrate_csv_header(path, ["A", "B"])
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == ["A", "B"]
            data = list(reader)
        assert data[0] == {"A": "1", "B": "2"}

    def test_preserves_existing_data_across_migration(self, tmp_path):
        path = str(tmp_path / "data.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Name", "Qty"])
            writer.writeheader()
            writer.writerow({"Name": "Cap", "Qty": "100"})
            writer.writerow({"Name": "Res", "Qty": "200"})

        migrate_csv_header(path, ["Name", "Qty", "Price"])
        with open(path, newline="", encoding="utf-8") as f:
            data = list(csv.DictReader(f))
        assert len(data) == 2
        assert data[0]["Name"] == "Cap"
        assert data[1]["Qty"] == "200"

    def test_handles_bom_in_existing_file(self, tmp_path):
        """UTF-8 BOM in existing file should not break migration."""
        path = str(tmp_path / "bom.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["A"])
            writer.writeheader()
            writer.writerow({"A": "val"})

        migrate_csv_header(path, ["A", "B"])
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {"A", "B"}
            data = list(reader)
        assert data[0]["A"] == "val"
        assert data[0]["B"] == ""


class TestFixDoubleUtf8:
    """Tests for fix_double_utf8()."""

    def test_fixes_double_encoded_omega(self):
        # Ω (U+2126) encoded as UTF-8 bytes [0xE2, 0x84, 0xA6], then
        # those bytes interpreted as CP1252 produce â„¢-like mojibake
        original = "Ω"
        double_encoded = original.encode("utf-8").decode("cp1252")
        assert fix_double_utf8(double_encoded) == original

    def test_fixes_double_encoded_degree(self):
        original = "℃"
        double_encoded = original.encode("utf-8").decode("cp1252")
        assert fix_double_utf8(double_encoded) == original

    def test_plain_ascii_passthrough(self):
        assert fix_double_utf8("hello world") == "hello world"

    def test_already_correct_utf8_passthrough(self):
        assert fix_double_utf8("100µF") == "100µF"

    def test_empty_string(self):
        assert fix_double_utf8("") == ""

    def test_latin1_fallback(self):
        """If cp1252 decode fails, latin-1 is tried."""
        # Create a string that encodes differently via latin-1
        original = "café"
        double = original.encode("utf-8").decode("latin-1")
        assert fix_double_utf8(double) == original


class TestReadText:
    """Tests for read_text()."""

    def test_reads_utf8_file(self, tmp_path):
        path = str(tmp_path / "test.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Name,Value\nR1,10kΩ\n")
        result = read_text(path)
        assert "10kΩ" in result

    def test_reads_utf8_bom_file(self, tmp_path):
        path = str(tmp_path / "bom.csv")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("A,B\n1,2\n")
        result = read_text(path)
        # BOM should be stripped
        assert result.startswith("A,B")

    def test_reads_utf16_le_file(self, tmp_path):
        path = str(tmp_path / "utf16.csv")
        with open(path, "w", encoding="utf-16") as f:
            f.write("Header1,Header2\nVal1,Val2\n")
        result = read_text(path)
        assert "Header1" in result
        assert "Val2" in result

    def test_reads_utf16_be_file(self, tmp_path):
        path = str(tmp_path / "utf16be.csv")
        content = "Col1,Col2\nx,y\n"
        with open(path, "wb") as f:
            f.write(content.encode("utf-16-be"))
            # Manually prepend BOM for BE
        with open(path, "wb") as f:
            f.write(b"\xfe\xff" + content.encode("utf-16-be"))
        result = read_text(path)
        assert "Col1" in result

    def test_empty_file(self, tmp_path):
        path = str(tmp_path / "empty.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        assert read_text(path) == ""


class TestConvertXlsToCsv:
    """Tests for convert_xls_to_csv()."""

    def test_import_xlrd_available(self):
        """xlrd must be installed for XLS support."""
        import xlrd  # noqa: F401

    def test_converts_basic_xls(self, tmp_path):
        """Create a minimal XLS file and convert it."""
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        headers = ["Mouser Part Number", "MFR Part Number", "Quantity", "Unit Price"]
        for j, h in enumerate(headers):
            ws.write(0, j, h)
        ws.write(1, 0, "123-ABC")
        ws.write(1, 1, "STM32F405")
        ws.write(1, 2, 10)
        ws.write(1, 3, 5.25)
        path = str(tmp_path / "test.xls")
        wb.save(path)

        result = convert_xls_to_csv(path)
        assert result is not None
        assert result["row_count"] == 1
        assert result["headers"] == headers
        assert "STM32F405" in result["csv_text"]

    def test_skips_blank_rows(self, tmp_path):
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        headers = ["Mouser PN", "MFR PN", "Qty", "Price"]
        for j, h in enumerate(headers):
            ws.write(0, j, h)
        ws.write(1, 0, "A")
        ws.write(1, 1, "B")
        ws.write(1, 2, "5")
        ws.write(1, 3, "1.0")
        # Row 2 is blank
        ws.write(3, 0, "C")
        ws.write(3, 1, "D")
        ws.write(3, 2, "3")
        ws.write(3, 3, "2.0")
        path = str(tmp_path / "blanks.xls")
        wb.save(path)

        result = convert_xls_to_csv(path)
        assert result is not None
        # blank row should be skipped
        assert result["row_count"] == 2

    def test_skips_footer_rows(self, tmp_path):
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        headers = ["Mouser PN", "MFR PN", "Qty", "Price"]
        for j, h in enumerate(headers):
            ws.write(0, j, h)
        ws.write(1, 0, "X")
        ws.write(1, 1, "Y")
        ws.write(1, 2, "10")
        ws.write(1, 3, "3.5")
        # Footer row
        ws.write(2, 0, "Merchandise Total")
        ws.write(2, 1, "")
        ws.write(2, 2, "")
        ws.write(2, 3, "3.5")
        path = str(tmp_path / "footer.xls")
        wb.save(path)

        result = convert_xls_to_csv(path)
        assert result is not None
        assert result["row_count"] == 1

    def test_returns_none_for_empty_sheet(self, tmp_path):
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        # Write only one empty cell
        ws.write(0, 0, "")
        path = str(tmp_path / "empty.xls")
        wb.save(path)

        result = convert_xls_to_csv(path)
        assert result is None

    def test_float_cleanup(self, tmp_path):
        """Quantities like 10.0 should be cleaned to '10'."""
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        headers = ["Mouser PN", "MFR PN", "Qty", "Price"]
        for j, h in enumerate(headers):
            ws.write(0, j, h)
        ws.write(1, 0, "P1")
        ws.write(1, 1, "MPN1")
        ws.write(1, 2, 25.0)
        ws.write(1, 3, 1.5)
        path = str(tmp_path / "floats.xls")
        wb.save(path)

        result = convert_xls_to_csv(path)
        assert result is not None
        # "25.0" should be cleaned to "25" in the CSV text
        assert "25.0" not in result["csv_text"]
        assert ",25," in result["csv_text"] or "25\r" in result["csv_text"] or "25\n" in result["csv_text"]

    def test_finds_header_row_not_at_top(self, tmp_path):
        """Header detection should find the header even if it's not row 0."""
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        # Rows 0-1: junk
        ws.write(0, 0, "Some preamble text")
        ws.write(1, 0, "Order date: 2024-01-01")
        # Row 2: actual header
        headers = ["Digikey Part Number", "MFR Part Number", "Qty", "Price"]
        for j, h in enumerate(headers):
            ws.write(2, j, h)
        ws.write(3, 0, "DK-123")
        ws.write(3, 1, "ABC")
        ws.write(3, 2, 5)
        ws.write(3, 3, 2.0)
        path = str(tmp_path / "offset_header.xls")
        wb.save(path)

        result = convert_xls_to_csv(path)
        assert result is not None
        assert result["row_count"] == 1
        assert "DK-123" in result["csv_text"]


def test_migrate_csv_header_adds_po_id(tmp_path):
    """purchase_ledger.csv with old header (no po_id) gets po_id added on next read."""
    import csv

    from csv_io import migrate_csv_header

    path = str(tmp_path / "purchase_ledger.csv")
    old_fields = ["LCSC Part Number", "Quantity"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old_fields)
        w.writeheader()
        w.writerow({"LCSC Part Number": "C123", "Quantity": "10"})

    new_fields = ["LCSC Part Number", "Quantity", "po_id"]
    migrate_csv_header(path, new_fields)

    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["po_id"] == ""
    assert rows[0]["LCSC Part Number"] == "C123"
    assert rows[0]["Quantity"] == "10"
