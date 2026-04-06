"""Tests for spec_extractor — extract structured specs from part descriptions."""

import pytest

import spec_extractor


class TestExtractSpec:
    def test_capacitor_basic(self):
        spec = spec_extractor.extract_spec(
            description="100nF 16V 0402 Capacitor MLCC",
            package="0402",
        )
        assert spec["type"] == "capacitor"
        assert spec["value"] == pytest.approx(1e-7)  # 100nF
        assert spec["value_display"] == "100nF"
        assert spec["package"] == "0402"

    def test_resistor_basic(self):
        spec = spec_extractor.extract_spec(
            description="4.7k\u03a9 0402 Resistor",
            package="0402",
        )
        assert spec["type"] == "resistor"
        assert spec["value"] == pytest.approx(4700)
        assert spec["package"] == "0402"

    def test_inductor_basic(self):
        spec = spec_extractor.extract_spec(
            description="10\u00b5H Inductor 0805",
            package="0805",
        )
        assert spec["type"] == "inductor"
        assert spec["value"] == pytest.approx(1e-5)
        assert spec["package"] == "0805"

    def test_voltage_extraction(self):
        spec = spec_extractor.extract_spec(
            description="100nF 16V 0402 Capacitor MLCC",
            package="0402",
        )
        assert spec.get("voltage") == pytest.approx(16.0)

    def test_tolerance_extraction(self):
        spec = spec_extractor.extract_spec(
            description="4.7k\u03a9 \u00b11% 0402 Resistor",
            package="0402",
        )
        assert spec.get("tolerance") == "1%"

    def test_dielectric_extraction(self):
        spec = spec_extractor.extract_spec(
            description="100nF 16V C0G 0402 Capacitor MLCC",
            package="0402",
        )
        assert spec.get("dielectric") == "C0G"

    def test_unknown_type(self):
        spec = spec_extractor.extract_spec(
            description="STM32G491 Microcontroller",
            package="LQFP-48",
        )
        assert spec["type"] == "other"
        assert spec.get("value") is None

    def test_empty_description(self):
        spec = spec_extractor.extract_spec(description="", package="")
        assert spec["type"] == "other"


class TestSpecMatchesGeneric:
    def test_exact_match(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402"}
        generic_spec = {"value": "100nF", "package": "0402"}
        strictness = {"required": ["value", "package"]}
        assert spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_value_mismatch(self):
        spec = {"type": "capacitor", "value": 1e-6, "package": "0402"}
        generic_spec = {"value": "100nF", "package": "0402"}
        strictness = {"required": ["value", "package"]}
        assert not spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_optional_field_ignored(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402"}
        generic_spec = {"value": "100nF", "package": "0402", "dielectric": "C0G"}
        strictness = {"required": ["value", "package"], "optional": ["dielectric"]}
        assert spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_required_field_missing_fails(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402"}
        generic_spec = {"value": "100nF", "package": "0402", "voltage_min": 16}
        strictness = {"required": ["value", "package", "voltage_min"]}
        # spec doesn't have voltage, but it's required
        assert not spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_voltage_min_check(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402", "voltage": 25}
        generic_spec = {"value": "100nF", "package": "0402", "voltage_min": 16}
        strictness = {"required": ["value", "package", "voltage_min"]}
        assert spec_extractor.spec_matches(spec, generic_spec, strictness)

    def test_voltage_too_low(self):
        spec = {"type": "capacitor", "value": 1e-7, "package": "0402", "voltage": 6.3}
        generic_spec = {"value": "100nF", "package": "0402", "voltage_min": 16}
        strictness = {"required": ["value", "package", "voltage_min"]}
        assert not spec_extractor.spec_matches(spec, generic_spec, strictness)


class TestGenerateGenericId:
    def test_capacitor_id(self):
        gid = spec_extractor.generate_generic_id("capacitor", {"value": "100nF", "package": "0402"})
        assert gid == "cap_100nf_0402"

    def test_resistor_id(self):
        gid = spec_extractor.generate_generic_id("resistor", {"value": "4.7k\u03a9", "package": "0402"})
        assert gid == "res_4.7kohm_0402"

    def test_deduplicates(self):
        gid1 = spec_extractor.generate_generic_id("capacitor", {"value": "100nF", "package": "0402"})
        gid2 = spec_extractor.generate_generic_id("capacitor", {"value": "100nF", "package": "0402"})
        assert gid1 == gid2
