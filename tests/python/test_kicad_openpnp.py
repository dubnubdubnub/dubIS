"""Tests for kicad_openpnp module — S-expr parser, footprint parsing, XML gen."""

import json
import os
import xml.etree.ElementTree as ET

import pytest

from kicad_openpnp import (
    _calc_body_size,
    _parse_pad_string,
    _simplify_footprint,
    generate_openpnp_packages_xml,
    generate_openpnp_parts_xml,
    load_json,
    load_kicad_projects,
    load_openpnp_parts,
    load_part_links,
    parse_easyeda_footprint,
    parse_kicad_footprint,
    parse_sexpr,
    regenerate_pnp_part_map,
    save_json,
    scan_kicad_project,
)


# ── S-expression parser ─────────────────────────────────────────────────────


class TestParseSexpr:
    def test_simple_list(self):
        result = parse_sexpr('(foo "bar" baz)')
        assert result == ["foo", "bar", "baz"]

    def test_nested(self):
        result = parse_sexpr('(a (b c) d)')
        assert result == ["a", ["b", "c"], "d"]

    def test_deep_nesting(self):
        result = parse_sexpr('(a (b (c d)) e)')
        assert result == ["a", ["b", ["c", "d"]], "e"]

    def test_quoted_string_with_spaces(self):
        result = parse_sexpr('(property "Reference" "R1")')
        assert result == ["property", "Reference", "R1"]

    def test_escaped_quote(self):
        result = parse_sexpr(r'(a "hello \"world\"")')
        assert result == ["a", 'hello "world"']

    def test_multiline(self):
        text = """(kicad_sch
  (version 20211014)
  (generator eeschema)
)"""
        result = parse_sexpr(text)
        assert result[0] == "kicad_sch"
        assert result[1] == ["version", "20211014"]
        assert result[2] == ["generator", "eeschema"]

    def test_empty_string(self):
        result = parse_sexpr('(a "" b)')
        assert result == ["a", "", "b"]

    def test_numbers_stay_strings(self):
        result = parse_sexpr("(at 1.5 -2.3)")
        assert result == ["at", "1.5", "-2.3"]


# ── KiCad footprint name simplification ──────────────────────────────────


class TestSimplifyFootprint:
    def test_strip_prefix_and_metric(self):
        assert _simplify_footprint("Resistor_SMD:R_0402_1005Metric") == "R_0402"

    def test_strip_prefix_only(self):
        assert _simplify_footprint("Package_SO:SOIC-8") == "SOIC-8"

    def test_no_prefix(self):
        assert _simplify_footprint("R_0402_1005Metric") == "R_0402"

    def test_no_metric_suffix(self):
        assert _simplify_footprint("MyLib:SOT-23") == "SOT-23"

    def test_empty(self):
        assert _simplify_footprint("") == ""


# ── KiCad project scanner ───────────────────────────────────────────────────


class TestScanKicadProject:
    def test_scan_basic_project(self, tmp_path):
        """Create a minimal KiCad project and verify scanning."""
        # Create .kicad_pro
        (tmp_path / "TestBoard.kicad_pro").write_text("{}", encoding="utf-8")

        # Create a minimal schematic
        sch_content = """(kicad_sch (version 20211014) (generator eeschema)
  (symbol (lib_id "Device:R") (at 100 100 0)
    (property "Reference" "R1")
    (property "Value" "10k")
    (property "Footprint" "Resistor_SMD:R_0402_1005Metric")
    (property "LCSC" "C25744")
    (in_bom yes)
  )
  (symbol (lib_id "Device:C") (at 200 100 0)
    (property "Reference" "C1")
    (property "Value" "100nF")
    (property "Footprint" "Capacitor_SMD:C_0402_1005Metric")
    (property "LCSC Part Number" "C1525")
    (property "MPN" "GRM155R71C104KA88J")
    (in_bom yes)
  )
  (symbol (lib_id "power:GND") (at 100 200 0)
    (property "Reference" "#PWR01")
    (property "Value" "GND")
  )
)"""
        (tmp_path / "TestBoard.kicad_sch").write_text(sch_content, encoding="utf-8")

        result = scan_kicad_project(str(tmp_path))
        assert result["name"] == "TestBoard"
        assert result["kicad_pro"] == "TestBoard.kicad_pro"
        assert len(result["parts"]) == 2

        r1 = next(p for p in result["parts"] if p["ref"] == "R1")
        assert r1["value"] == "10k"
        assert r1["lcsc"] == "C25744"
        assert r1["footprint"] == "R_0402"

        c1 = next(p for p in result["parts"] if p["ref"] == "C1")
        assert c1["lcsc"] == "C1525"
        assert c1["mpn"] == "GRM155R71C104KA88J"

    def test_dnp_parts(self, tmp_path):
        (tmp_path / "Board.kicad_pro").write_text("{}", encoding="utf-8")
        sch = """(kicad_sch (version 20211014)
  (symbol (lib_id "Device:R") (at 0 0 0)
    (property "Reference" "R1")
    (property "Value" "DNP")
    (property "Footprint" "Resistor_SMD:R_0402_1005Metric")
    (dnp yes)
    (in_bom yes)
  )
)"""
        (tmp_path / "Board.kicad_sch").write_text(sch, encoding="utf-8")
        result = scan_kicad_project(str(tmp_path))
        assert result["parts"][0]["dnp"] is True

    def test_skips_power_symbols(self, tmp_path):
        (tmp_path / "Board.kicad_pro").write_text("{}", encoding="utf-8")
        sch = """(kicad_sch (version 20211014)
  (symbol (lib_id "power:VCC") (at 0 0 0)
    (property "Reference" "#PWR01")
    (property "Value" "VCC")
  )
)"""
        (tmp_path / "Board.kicad_sch").write_text(sch, encoding="utf-8")
        result = scan_kicad_project(str(tmp_path))
        assert len(result["parts"]) == 0

    def test_no_pro_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No .kicad_pro"):
            scan_kicad_project(str(tmp_path))

    def test_not_a_directory_raises(self):
        with pytest.raises(ValueError, match="Not a directory"):
            scan_kicad_project("/nonexistent/path")

    def test_jlcpcb_field_name(self, tmp_path):
        """JLCPCB field should be treated as LCSC."""
        (tmp_path / "Board.kicad_pro").write_text("{}", encoding="utf-8")
        sch = """(kicad_sch (version 20211014)
  (symbol (lib_id "Device:R") (at 0 0 0)
    (property "Reference" "R1")
    (property "Value" "10k")
    (property "Footprint" "R:R_0402_1005Metric")
    (property "JLCPCB Part #" "C25744")
    (in_bom yes)
  )
)"""
        (tmp_path / "Board.kicad_sch").write_text(sch, encoding="utf-8")
        result = scan_kicad_project(str(tmp_path))
        assert result["parts"][0]["lcsc"] == "C25744"


# ── EasyEDA pad string parsing ───────────────────────────────────────────


class TestParsePadString:
    def test_basic_rect_pad(self):
        pad_str = "PAD~RECT~100~200~10~20~1~~1~0~~0"
        result = _parse_pad_string(pad_str)
        assert result is not None
        assert result["name"] == "1"
        assert result["shape"] == "RECT"
        assert result["x"] == 100.0
        assert result["y"] == 200.0
        assert result["width"] == 10.0
        assert result["height"] == 20.0
        assert result["rotation"] == 0.0

    def test_oval_pad_with_rotation(self):
        pad_str = "PAD~OVAL~50~60~8~12~1~~2~0~~45"
        result = _parse_pad_string(pad_str)
        assert result is not None
        assert result["name"] == "2"
        assert result["shape"] == "OVAL"
        assert result["rotation"] == 45.0

    def test_invalid_short_string(self):
        assert _parse_pad_string("PAD~RECT~1~2") is None

    def test_zero_width_rejected(self):
        assert _parse_pad_string("PAD~RECT~100~200~0~20~1~~1~0~~0") is None


# ── EasyEDA footprint parsing ────────────────────────────────────────────


class TestParseEasyedaFootprint:
    def test_datastr_shape_format(self):
        data = {
            "dataStr": {
                "shape": [
                    "PAD~RECT~-10~0~8~10~1~~1~0~~0",
                    "PAD~RECT~10~0~8~10~1~~2~0~~0",
                ]
            }
        }
        result = parse_easyeda_footprint(data, "C0402")
        assert result["body_width"] > 0
        assert result["body_height"] > 0
        assert len(result["pads"]) == 2

        # Check Y inversion and centering
        p1, p2 = sorted(result["pads"], key=lambda p: p["x"])
        assert p1["x"] < 0  # left pad
        assert p2["x"] > 0  # right pad
        # After centering, pads should be symmetric
        assert abs(p1["x"] + p2["x"]) < 0.001

    def test_roundness_mapping(self):
        data = {
            "dataStr": {
                "shape": [
                    "PAD~ROUND~0~0~10~10~1~~1~0~~0",
                    "PAD~OVAL~20~0~10~15~1~~2~0~~0",
                    "PAD~RECT~40~0~10~10~1~~3~0~~0",
                ]
            }
        }
        result = parse_easyeda_footprint(data, "test")
        pads = {p["name"]: p for p in result["pads"]}
        assert pads["1"]["roundness"] == 100.0  # ROUND
        assert pads["2"]["roundness"] == 50.0   # OVAL
        assert pads["3"]["roundness"] == 0.0    # RECT

    def test_no_pads_raises(self):
        with pytest.raises(ValueError, match="No pad data"):
            parse_easyeda_footprint({}, "test")


# ── KiCad .kicad_mod parsing ────────────────────────────────────────────


class TestParseKicadFootprint:
    def test_parse_mod_file(self, tmp_path):
        """Create a minimal .kicad_mod and verify parsing."""
        lib_dir = tmp_path / "Resistor_SMD.pretty"
        lib_dir.mkdir()

        mod_content = """(footprint "R_0402_1005Metric"
  (pad "1" smd rect (at -0.48 0) (size 0.56 0.62))
  (pad "2" smd rect (at 0.48 0) (size 0.56 0.62))
)"""
        (lib_dir / "R_0402_1005Metric.kicad_mod").write_text(mod_content, encoding="utf-8")

        result = parse_kicad_footprint("Resistor_SMD:R_0402_1005Metric", [str(tmp_path)])
        assert result is not None
        assert len(result["pads"]) == 2
        assert result["body_width"] > 0

        # Pads should be centered
        p1, p2 = sorted(result["pads"], key=lambda p: p["x"])
        assert abs(p1["x"] + p2["x"]) < 0.001

    def test_roundrect_pads(self, tmp_path):
        lib_dir = tmp_path / "MyLib.pretty"
        lib_dir.mkdir()

        mod_content = """(footprint "test_roundrect"
  (pad "1" smd roundrect (at 0 0) (size 1.0 0.5) (roundrect_rratio 0.25))
)"""
        (lib_dir / "test_roundrect.kicad_mod").write_text(mod_content, encoding="utf-8")

        result = parse_kicad_footprint("MyLib:test_roundrect", [str(tmp_path)])
        assert result is not None
        assert result["pads"][0]["roundness"] == 50.0

    def test_circle_pads(self, tmp_path):
        lib_dir = tmp_path / "MyLib.pretty"
        lib_dir.mkdir()

        mod_content = """(footprint "test_circle"
  (pad "1" smd circle (at 0 0) (size 0.5 0.5))
)"""
        (lib_dir / "test_circle.kicad_mod").write_text(mod_content, encoding="utf-8")

        result = parse_kicad_footprint("MyLib:test_circle", [str(tmp_path)])
        assert result is not None
        assert result["pads"][0]["roundness"] == 100.0

    def test_missing_lib_returns_none(self):
        assert parse_kicad_footprint("NoLib:NoFp", ["/nonexistent"]) is None

    def test_no_colon_returns_none(self):
        assert parse_kicad_footprint("NoColonHere", []) is None

    def test_y_inversion(self, tmp_path):
        lib_dir = tmp_path / "TestLib.pretty"
        lib_dir.mkdir()

        mod_content = """(footprint "test_y"
  (pad "1" smd rect (at 0 -1.0) (size 0.5 0.5))
  (pad "2" smd rect (at 0 1.0) (size 0.5 0.5))
)"""
        (lib_dir / "test_y.kicad_mod").write_text(mod_content, encoding="utf-8")

        result = parse_kicad_footprint("TestLib:test_y", [str(tmp_path)])
        assert result is not None
        # KiCad Y=−1.0 should become OpenPnP Y=+1.0 (before centering)
        # After centering both should be offset from 0
        p1, p2 = sorted(result["pads"], key=lambda p: p["y"])
        assert p1["y"] < 0
        assert p2["y"] > 0


# ── Body size calculation ────────────────────────────────────────────────


class TestCalcBodySize:
    def test_two_pads(self):
        pads = [
            {"x": -0.5, "y": 0, "width": 0.4, "height": 0.5},
            {"x": 0.5, "y": 0, "width": 0.4, "height": 0.5},
        ]
        w, h = _calc_body_size(pads)
        assert w == 1.0
        assert h == 0.5

    def test_empty(self):
        assert _calc_body_size([]) == (1.0, 1.0)

    def test_multi_pad(self):
        pads = [
            {"x": -1, "y": -1, "width": 0.5, "height": 0.5},
            {"x": 1, "y": -1, "width": 0.5, "height": 0.5},
            {"x": -1, "y": 1, "width": 0.5, "height": 0.5},
            {"x": 1, "y": 1, "width": 0.5, "height": 0.5},
        ]
        w, h = _calc_body_size(pads)
        assert w == pytest.approx(2.5)
        assert h == pytest.approx(2.5)


# ── OpenPnP XML generation ──────────────────────────────────────────────


class TestGenerateOpenpnpXml:
    def test_packages_xml(self, tmp_path):
        openpnp_data = {
            "parts": {
                "C1525": {
                    "openpnp_id": "C0402-100nF",
                    "package_id": "C0402",
                    "height": 0.5,
                    "speed": 1.0,
                    "footprint": {
                        "body_width": 1.0,
                        "body_height": 0.5,
                        "pads": [
                            {"name": "1", "x": -0.4, "y": 0, "width": 0.4, "height": 0.5, "rotation": 0, "roundness": 0},
                            {"name": "2", "x": 0.4, "y": 0, "width": 0.4, "height": 0.5, "rotation": 0, "roundness": 0},
                        ],
                    },
                }
            }
        }

        path = generate_openpnp_packages_xml(openpnp_data, str(tmp_path))
        assert os.path.isfile(path)

        tree = ET.parse(path)
        root = tree.getroot()
        packages = list(root)
        assert len(packages) == 1
        assert packages[0].get("id") == "C0402"

        fp = packages[0].find("footprint")
        assert fp is not None
        assert fp.get("body-width") == "1.0"
        pads = list(fp)
        assert len(pads) == 2

    def test_parts_xml(self, tmp_path):
        openpnp_data = {
            "parts": {
                "C1525": {
                    "openpnp_id": "C0402-100nF",
                    "package_id": "C0402",
                    "height": 0.5,
                    "speed": 1.0,
                }
            }
        }

        path = generate_openpnp_parts_xml(openpnp_data, str(tmp_path))
        assert os.path.isfile(path)

        tree = ET.parse(path)
        root = tree.getroot()
        parts = list(root)
        assert len(parts) == 1
        assert parts[0].get("id") == "C0402-100nF"
        assert parts[0].get("height") == "0.5"

    def test_preserves_non_dubis_entries(self, tmp_path):
        """Existing non-dubIS packages should be preserved."""
        existing = """<?xml version='1.0' encoding='UTF-8'?>
<openpnp-packages>
  <package id="ManualPkg" description="manually added">
    <footprint units="Millimeters" body-width="2.0" body-height="1.0" />
  </package>
</openpnp-packages>"""
        (tmp_path / "packages.xml").write_text(existing, encoding="utf-8")

        openpnp_data = {
            "parts": {
                "C1525": {
                    "package_id": "C0402",
                    "footprint": {
                        "body_width": 1.0, "body_height": 0.5,
                        "pads": [{"name": "1", "x": 0, "y": 0, "width": 0.4, "height": 0.5}],
                    },
                }
            }
        }

        generate_openpnp_packages_xml(openpnp_data, str(tmp_path))
        tree = ET.parse(str(tmp_path / "packages.xml"))
        packages = list(tree.getroot())
        ids = [p.get("id") for p in packages]
        assert "ManualPkg" in ids
        assert "C0402" in ids

    def test_idempotent_update(self, tmp_path):
        """Running twice should not duplicate entries."""
        openpnp_data = {
            "parts": {
                "C1525": {
                    "package_id": "C0402",
                    "footprint": {
                        "body_width": 1.0, "body_height": 0.5,
                        "pads": [{"name": "1", "x": 0, "y": 0, "width": 0.4, "height": 0.5}],
                    },
                }
            }
        }

        generate_openpnp_packages_xml(openpnp_data, str(tmp_path))
        generate_openpnp_packages_xml(openpnp_data, str(tmp_path))
        tree = ET.parse(str(tmp_path / "packages.xml"))
        assert len(list(tree.getroot())) == 1


# ── JSON persistence ────────────────────────────────────────────────────


class TestJsonPersistence:
    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "test.json")
        data = {"foo": "bar", "num": 42}
        save_json(path, data)
        loaded = load_json(path)
        assert loaded == data

    def test_load_missing_file(self, tmp_path):
        assert load_json(str(tmp_path / "nonexistent.json")) == {}

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert load_json(str(path)) == {}

    def test_load_part_links_defaults(self, monkeypatch):
        monkeypatch.setattr("kicad_openpnp._data_path", lambda f: "/nonexistent/" + f)
        data = load_part_links()
        assert data == {"links": {}, "version": 1}

    def test_load_openpnp_parts_defaults(self, monkeypatch):
        monkeypatch.setattr("kicad_openpnp._data_path", lambda f: "/nonexistent/" + f)
        data = load_openpnp_parts()
        assert "parts" in data
        assert "nozzle_tips" in data

    def test_load_kicad_projects_defaults(self, monkeypatch):
        monkeypatch.setattr("kicad_openpnp._data_path", lambda f: "/nonexistent/" + f)
        data = load_kicad_projects()
        assert data == {"projects": {}}


class TestRegeneratePnpPartMap:
    def test_generates_map(self, tmp_path, monkeypatch):
        monkeypatch.setattr("kicad_openpnp._data_path", lambda f: str(tmp_path / f))
        openpnp_data = {
            "parts": {
                "C1525": {"openpnp_id": "C0402-100nF"},
                "C2345": {"openpnp_id": "R0402-10k"},
            }
        }
        regenerate_pnp_part_map(openpnp_data)

        with open(str(tmp_path / "pnp_part_map.json"), encoding="utf-8") as f:
            result = json.load(f)
        assert result == {"C0402-100nF": "C1525", "R0402-10k": "C2345"}
