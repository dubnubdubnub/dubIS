"""Tests for domain.part_registry — stable part identity via alias registry."""

import json
import os

import pytest

from domain import part_registry
from dubis_errors import PartRegistryCollisionError


def _row(**kw):
    base = {
        "LCSC Part Number": "", "Manufacture Part Number": "",
        "Digikey Part Number": "", "Pololu Part Number": "", "Mouser Part Number": "",
    }
    base.update(kw)
    return base


class TestDeriveKey:
    def test_precedence_lcsc_over_mpn(self):
        row = _row(**{"LCSC Part Number": "C1234", "Manufacture Part Number": "MPN1"})
        assert part_registry.derive_key(row) == "C1234"

    def test_non_c_prefixed_lcsc_falls_through_to_mpn(self):
        row = _row(**{"LCSC Part Number": "1234", "Manufacture Part Number": "MPN1"})
        assert part_registry.derive_key(row) == "MPN1"

    def test_empty_row_returns_empty(self):
        assert part_registry.derive_key(_row()) == ""


class TestRegistry:
    def test_load_missing_file_returns_empty_registry(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        assert reg.parts == {}
        assert reg.dirty is False

    def test_register_new_row_uses_derived_key_and_records_aliases(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        row = _row(**{"LCSC Part Number": "C77", "Manufacture Part Number": "STM32F405"})
        key = part_registry.register_row(reg, row)
        assert key == "C77"
        assert set(reg.parts["C77"]) == {"C77", "STM32F405"}
        assert reg.dirty is True

    def test_enrichment_does_not_change_identity(self, tmp_path):
        """The core bug this module fixes: adding a higher-precedence PN later
        must NOT flip the part's identity."""
        reg = part_registry.load(str(tmp_path))
        # Part first appears with only an MPN → canonical key is the MPN.
        key1 = part_registry.register_row(reg, _row(**{"Manufacture Part Number": "STM32F405"}))
        assert key1 == "STM32F405"
        # Later the same row is enriched with an LCSC number.
        enriched = _row(**{"LCSC Part Number": "C99", "Manufacture Part Number": "STM32F405"})
        key2 = part_registry.register_row(reg, enriched)
        assert key2 == "STM32F405"          # identity is stable
        assert reg.alias_index["C99"] == "STM32F405"  # new PN becomes an alias
        # A row later matching only by the new LCSC number still resolves.
        assert part_registry.canonical_for_row(reg, _row(**{"LCSC Part Number": "C99"})) == "STM32F405"

    def test_collision_raises(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        part_registry.register_row(reg, _row(**{"Manufacture Part Number": "MPN_A"}))
        part_registry.register_row(reg, _row(**{"LCSC Part Number": "C1"}))
        # One row claiming PNs of two different registered parts → hard fail.
        bad = _row(**{"LCSC Part Number": "C1", "Manufacture Part Number": "MPN_A"})
        with pytest.raises(PartRegistryCollisionError):
            part_registry.canonical_for_row(reg, bad)

    def test_save_load_roundtrip(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        part_registry.register_row(reg, _row(**{"LCSC Part Number": "C77", "Manufacture Part Number": "M1"}))
        part_registry.save(str(tmp_path), reg)
        path = os.path.join(str(tmp_path), "part_registry.json")
        assert json.load(open(path, encoding="utf-8"))["version"] == 1
        reg2 = part_registry.load(str(tmp_path))
        assert reg2.parts == reg.parts
        assert reg2.alias_index["M1"] == "C77"
        assert reg2.dirty is False

    def test_register_row_with_no_pns_returns_empty(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        assert part_registry.register_row(reg, _row()) == ""
        assert reg.dirty is False
