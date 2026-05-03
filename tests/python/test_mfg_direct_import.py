"""Tests for mfg_direct_import module: parse_source_file."""
import io
import shutil

import pytest

import mfg_direct_import as mdi


def _make_text_pdf(rows: list[tuple[str, str, int, float]]) -> bytes:
    """Create a tiny PDF with a table-like layout via reportlab."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    y = 750
    c.drawString(50, y, "MPN          Mfg     Qty    Unit Price")
    y -= 20
    for mpn, mfg, qty, price in rows:
        c.drawString(50, y, f"{mpn:<12} {mfg:<7} {qty:<6} {price:.2f}")
        y -= 16
    c.save()
    return buf.getvalue()


class TestParseCSV:
    def test_csv_with_standard_headers(self, tmp_path):
        csv_text = (
            "Manufacture Part Number,Manufacturer,Quantity,Unit Price($)\n"
            "TMR2615,MDT,50,4.20\n"
            "TMR2305,MDT,25,3.10\n"
        )
        path = tmp_path / "po.csv"
        path.write_bytes(csv_text.encode("utf-8"))
        rows = mdi.parse_source_file(str(path))
        assert len(rows) == 2
        assert rows[0]["mpn"] == "TMR2615"
        assert rows[0]["manufacturer"] == "MDT"
        assert rows[0]["quantity"] == 50
        assert rows[0]["unit_price"] == 4.20


class TestParsePDF:
    def test_text_pdf_extracts_rows(self, tmp_path):
        data = _make_text_pdf([("TMR2615", "MDT", 50, 4.20),
                                ("TMR2305", "MDT", 25, 3.10)])
        path = tmp_path / "invoice.pdf"
        path.write_bytes(data)
        rows = mdi.parse_source_file(str(path))
        # Heuristic parse may extract 2 rows; require at least the MPNs
        mpns = {r["mpn"] for r in rows}
        assert "TMR2615" in mpns
        assert "TMR2305" in mpns

    def test_empty_pdf_returns_empty(self, tmp_path):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.save()
        path = tmp_path / "empty.pdf"
        path.write_bytes(buf.getvalue())
        assert mdi.parse_source_file(str(path)) == []


@pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract binary not available",
)
class TestParseImage:
    def test_png_ocr(self, tmp_path):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (400, 80), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 18)
        except OSError:
            font = ImageFont.load_default()
        draw.text((10, 10), "TMR2615 MDT 50 4.20", font=font, fill=(0, 0, 0))
        path = tmp_path / "scan.png"
        img.save(path)
        rows = mdi.parse_source_file(str(path))
        # OCR is fuzzy — assert we got *something* and TMR appears
        assert any("TMR" in r.get("mpn", "") for r in rows)


class TestUnknownExtension:
    def test_returns_empty(self, tmp_path):
        path = tmp_path / "foo.xyz"
        path.write_bytes(b"random")
        assert mdi.parse_source_file(str(path)) == []


class TestMatchPart:
    def _seed_parts(self, db):
        """Insert a few parts for matching."""
        db.execute(
            """INSERT INTO parts (part_id, lcsc, mpn, manufacturer)
               VALUES (?, ?, ?, ?)""",
            ("TMR2615", "", "TMR2615", "MDT"),
        )
        db.execute(
            """INSERT INTO parts (part_id, lcsc, mpn, manufacturer)
               VALUES (?, ?, ?, ?)""",
            ("DF40C-30DP-0.4V(51)", "C429942", "DF40C-30DP-0.4V(51)", "HRS"),
        )
        db.commit()

    def test_exact_match_definite(self, db):
        self._seed_parts(db)
        result = mdi.match_part(db, mpn="TMR2615", manufacturer="MDT")
        assert result["status"] == "definite"
        assert result["part_id"] == "TMR2615"

    def test_case_insensitive_match(self, db):
        self._seed_parts(db)
        result = mdi.match_part(db, mpn="tmr2615", manufacturer="mdt")
        assert result["status"] == "definite"

    def test_hyphen_normalized_match(self, db):
        self._seed_parts(db)
        result = mdi.match_part(db, mpn="TMR-2615", manufacturer="MDT")
        assert result["status"] == "definite"

    def test_fuzzy_match_levenshtein_2(self, db):
        self._seed_parts(db)
        result = mdi.match_part(db, mpn="TMR2615A", manufacturer="MDT")
        assert result["status"] == "possible"
        assert any(c["part_id"] == "TMR2615" for c in result["candidates"])

    def test_no_match(self, db):
        self._seed_parts(db)
        result = mdi.match_part(db, mpn="UNRELATED-XYZ", manufacturer="OtherMfg")
        assert result["status"] == "new"

    def test_empty_mpn_returns_new(self, db):
        result = mdi.match_part(db, mpn="", manufacturer="MDT")
        assert result["status"] == "new"


class TestImportPO:
    def test_import_writes_po_and_ledger(self, tmp_path):
        import vendors

        base_dir = tmp_path / "data"
        base_dir.mkdir()
        (base_dir / "sources").mkdir()
        vjson = str(base_dir / "vendors.json")
        po_csv = str(base_dir / "purchase_orders.csv")
        ledger_csv = str(base_dir / "purchase_ledger.csv")

        vendors.seed_builtins(vjson)
        v = vendors.create_vendor(vjson, name="MDT", url="https://tmr-sensors.com")

        line_items = [
            {"mpn": "TMR2615", "manufacturer": "MDT", "package": "SOIC-8",
             "quantity": 50, "unit_price": 4.20, "match": "new"},
            {"mpn": "TMR2305", "manufacturer": "MDT", "package": "SOIC-8",
             "quantity": 25, "unit_price": 3.10, "match": "new"},
        ]

        new_po = mdi.import_po(
            ledger_csv=ledger_csv,
            po_csv=po_csv,
            sources_dir=str(base_dir / "sources"),
            vendor_id=v["id"],
            source_file_bytes=None,
            source_file_ext=None,
            purchase_date="2026-04-15",
            notes="apr",
            line_items=line_items,
        )
        assert new_po["po_id"]

        import csv as _csv
        with open(ledger_csv, encoding="utf-8-sig") as f:
            ledger_rows = list(_csv.DictReader(f))
        assert len(ledger_rows) == 2
        for row in ledger_rows:
            assert row["po_id"] == new_po["po_id"]
            assert row["Manufacturer"] == "MDT"

    def test_import_with_match_links_existing_part(self, tmp_path):
        # When line_items contain match.part_id, the ledger row carries that part's
        # existing identifiers (LCSC, etc.) — so cache merge unifies the entries.
        import csv as _csv

        import vendors

        base_dir = tmp_path / "data"
        base_dir.mkdir()
        (base_dir / "sources").mkdir()
        vjson = str(base_dir / "vendors.json")
        po_csv = str(base_dir / "purchase_orders.csv")
        ledger_csv = str(base_dir / "purchase_ledger.csv")

        # Seed an existing inventory row first
        with open(ledger_csv, "w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=[
                "Digikey Part Number", "LCSC Part Number", "Pololu Part Number",
                "Mouser Part Number", "Manufacture Part Number", "Manufacturer",
                "Customer NO.", "Package", "Description", "RoHS",
                "Quantity", "Unit Price($)", "Ext.Price($)",
                "Estimated lead time (business days)", "Date Code / Lot No.", "po_id"])
            writer.writeheader()
            writer.writerow({"LCSC Part Number": "C12345",
                             "Manufacture Part Number": "TMR2615",
                             "Manufacturer": "MDT", "Quantity": "10",
                             "Unit Price($)": "4.50", "po_id": ""})

        vendors.seed_builtins(vjson)
        v = vendors.create_vendor(vjson, name="MDT", url="https://tmr-sensors.com")

        line_items = [{
            "mpn": "TMR-2615", "manufacturer": "MDT", "package": "",
            "quantity": 5, "unit_price": 4.20,
            "match": "definite", "match_part_id": "TMR2615",
        }]
        new_po = mdi.import_po(
            ledger_csv=ledger_csv, po_csv=po_csv,
            sources_dir=str(base_dir / "sources"),
            vendor_id=v["id"], source_file_bytes=None, source_file_ext=None,
            purchase_date="2026-04-15", notes="", line_items=line_items,
        )
        import csv as _csv2
        with open(ledger_csv, encoding="utf-8-sig") as f:
            rows = list(_csv2.DictReader(f))
        assert len(rows) == 2
        # New row inherits the matched LCSC code so cache merges them
        new_row = next(r for r in rows if r["po_id"] == new_po["po_id"])
        assert new_row["LCSC Part Number"] == "C12345"

    def test_import_empty_line_items_raises(self, tmp_path):
        import vendors
        vjson = str(tmp_path / "vendors.json")
        vendors.seed_builtins(vjson)
        with pytest.raises(ValueError, match="empty"):
            mdi.import_po(
                ledger_csv=str(tmp_path / "p.csv"),
                po_csv=str(tmp_path / "po.csv"),
                sources_dir=str(tmp_path / "sources"),
                vendor_id="v_unknown",
                source_file_bytes=None, source_file_ext=None,
                purchase_date="2026-04-15", notes="", line_items=[],
            )
