"""Tests for domain.kicad_mapping — durable data/kicad_mapping.json entity."""

import json
import os

import pytest

import cache_db
from domain import kicad_mapping


def _insert_part(db, part_id, *, lcsc=None, description="Widget", package="0603"):
    db.execute(
        "INSERT INTO parts (part_id, lcsc, mpn, manufacturer, description, package, section)"
        " VALUES (?,?,?,?,?,?,?)",
        (part_id, lcsc if lcsc is not None else part_id, "", "", description, package, "Other"),
    )
    db.execute("INSERT INTO stock (part_id, quantity, unit_price) VALUES (?,10,0.01)", (part_id,))
    db.commit()


def _write_mapping(data_dir, data):
    path = os.path.join(data_dir, "kicad_mapping.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _sample_category():
    return {
        "id": "1",
        "name": "Passives/Capacitors/Ceramic",
        "source": "jlcpcb",
        "jlcpcb_catalog_name": "Multilayer Ceramic Capacitors MLCC - SMD/SMT",
        "default_symbol": "Device:C",
        "default_footprint_from_package": True,
        "default_reference": "C",
    }


def _sample_override():
    return {
        "category_id": "1",
        "kicad_symbol": None,
        "kicad_footprint": None,
        "kicad_datasheet": None,
        "eligible_override": None,
    }


def _sample_cache_entry():
    return {
        "lcsc": "C15850",
        "jlcpcb_catalog_name": "Multilayer Ceramic Capacitors MLCC - SMD/SMT",
        "resolved_category_id": "1",
        "resolved_via": "jlcpcb",
        "resolved_at": "2026-07-17T04:00:00Z",
    }


class TestRoundTrip:
    def test_write_load_query(self, db, data_dir):
        _insert_part(db, "C15850")
        _write_mapping(data_dir, {
            "version": 1,
            "categories": [_sample_category()],
            "part_overrides": {"C15850": _sample_override()},
            "part_category_cache": {"C15850": _sample_cache_entry()},
        })

        kicad_mapping.load_into_db(db, data_dir)

        cat_row = db.execute("SELECT * FROM kicad_categories WHERE id='1'").fetchone()
        assert cat_row["name"] == "Passives/Capacitors/Ceramic"
        assert cat_row["source"] == "jlcpcb"
        assert cat_row["jlcpcb_catalog_name"] == "Multilayer Ceramic Capacitors MLCC - SMD/SMT"
        assert cat_row["default_symbol"] == "Device:C"
        assert cat_row["default_footprint_from_package"] == 1
        assert cat_row["default_reference"] == "C"

        part_row = db.execute("SELECT * FROM kicad_part_state WHERE part_id='C15850'").fetchone()
        assert part_row["category_id"] == "1"
        assert part_row["eligible_override"] is None
        assert part_row["cache_lcsc"] == "C15850"
        assert part_row["cache_resolved_category_id"] == "1"
        assert part_row["cache_resolved_via"] == "jlcpcb"

    def test_categorize_fallback_category_shape(self, db, data_dir):
        _insert_part(db, "STLINKV3MINIE")
        _write_mapping(data_dir, {
            "version": 1,
            "categories": [{
                "id": "2",
                "name": "Development Boards, Kits, Programmers",
                "source": "categorize_fallback",
                "categorize_bucket": "Development Boards, Kits, Programmers",
                "default_symbol": None,
                "default_footprint_from_package": False,
                "default_reference": None,
            }],
            "part_overrides": {},
            "part_category_cache": {},
        })

        kicad_mapping.load_into_db(db, data_dir)

        row = db.execute("SELECT * FROM kicad_categories WHERE id='2'").fetchone()
        assert row["source"] == "categorize_fallback"
        assert row["categorize_bucket"] == "Development Boards, Kits, Programmers"
        assert row["default_symbol"] is None
        assert row["default_footprint_from_package"] == 0


class TestPerSkuOverrideCanonicalKey:
    def test_override_keyed_by_canonical_part_id_resolves_via_alias(self, db, data_dir):
        """Per-SKU overrides are keyed by the SAME canonical part_id
        domain/part_registry.py derives/registers — an override set against
        the canonical key must resolve correctly even though the row in
        `parts` may have been enriched under a different distributor PN.
        This guards the loose-precheck-vs-strict-key bug class (PR #354).
        """
        # The canonical key is what part_registry chose first (an MPN, say),
        # even though the ledger row now also carries an LCSC alias.
        _insert_part(db, "STM32F405", lcsc="C99")
        _write_mapping(data_dir, {
            "version": 1,
            "categories": [],
            "part_overrides": {
                "STM32F405": {
                    "category_id": "2",
                    "kicad_symbol": "MCU_ST_STM32F4:STM32F405RGTx",
                    "kicad_footprint": None,
                    "kicad_datasheet": None,
                    "eligible_override": True,
                },
            },
            "part_category_cache": {},
        })

        kicad_mapping.load_into_db(db, data_dir)

        row = db.execute(
            "SELECT * FROM kicad_part_state WHERE part_id='STM32F405'"
        ).fetchone()
        assert row is not None
        assert row["kicad_symbol"] == "MCU_ST_STM32F4:STM32F405RGTx"
        assert row["eligible_override"] == 1
        # An alias PN (e.g. "C99") is NOT a separate row -- only the
        # canonical key carries state.
        assert db.execute(
            "SELECT * FROM kicad_part_state WHERE part_id='C99'"
        ).fetchone() is None


class TestEligibilityTriState:
    @pytest.mark.parametrize("value,expected_db,expected_roundtrip", [
        (True, 1, True),
        (False, 0, False),
        (None, None, None),
    ])
    def test_tri_state_persists(self, db, data_dir, value, expected_db, expected_roundtrip):
        _insert_part(db, "ESP32-WROOM-32E-N4")
        _write_mapping(data_dir, {
            "version": 1,
            "categories": [],
            "part_overrides": {
                "ESP32-WROOM-32E-N4": {
                    "category_id": "2",
                    "kicad_symbol": None,
                    "kicad_footprint": None,
                    "kicad_datasheet": None,
                    "eligible_override": value,
                },
            },
            "part_category_cache": {},
        })

        kicad_mapping.load_into_db(db, data_dir)
        row = db.execute(
            "SELECT eligible_override FROM kicad_part_state WHERE part_id='ESP32-WROOM-32E-N4'"
        ).fetchone()
        assert row["eligible_override"] == expected_db

        kicad_mapping._persist(db, data_dir)
        with open(os.path.join(data_dir, "kicad_mapping.json"), encoding="utf-8") as f:
            written = json.load(f)
        assert written["part_overrides"]["ESP32-WROOM-32E-N4"]["eligible_override"] == expected_roundtrip


class TestMissingAndCorruptFile:
    def test_missing_file_is_noop_self_healing(self, db, data_dir):
        # No kicad_mapping.json written at all.
        kicad_mapping.load_into_db(db, data_dir)
        assert db.execute("SELECT * FROM kicad_categories").fetchall() == []
        assert db.execute("SELECT * FROM kicad_part_state").fetchall() == []

    def test_unsupported_version_raises(self, db, data_dir):
        _write_mapping(data_dir, {"version": 2, "categories": [], "part_overrides": {},
                                    "part_category_cache": {}})
        with pytest.raises(ValueError, match="Unsupported kicad_mapping.json version"):
            kicad_mapping.load_into_db(db, data_dir)

    def test_corrupt_json_raises(self, db, data_dir):
        path = os.path.join(data_dir, "kicad_mapping.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            kicad_mapping.load_into_db(db, data_dir)


class TestUnknownPartRetention:
    def test_override_for_deleted_part_is_warn_skipped_and_retained(self, db, data_dir, caplog):
        """A part_overrides entry for a SKU no longer in `parts` (deleted from
        the ledger since the override was set) must be warn-logged and
        skipped in the DB -- but RETAINED in the JSON on next _persist, the
        same "the part may return" contract as generic_parts.json's
        member-retention logic. This is the easiest thing to regress by
        copying generic_parts.py's pattern imprecisely.
        """
        # Note: no _insert_part call -- "GHOST123" is not in `parts`.
        _write_mapping(data_dir, {
            "version": 1,
            "categories": [],
            "part_overrides": {
                "GHOST123": {
                    "category_id": "1",
                    "kicad_symbol": "Device:R",
                    "kicad_footprint": None,
                    "kicad_datasheet": None,
                    "eligible_override": None,
                },
            },
            "part_category_cache": {
                "GHOST123": {
                    "lcsc": "GHOST123",
                    "jlcpcb_catalog_name": "Resistors",
                    "resolved_category_id": "1",
                    "resolved_via": "jlcpcb",
                    "resolved_at": "2026-07-17T04:00:00Z",
                },
            },
        })

        import logging
        with caplog.at_level(logging.WARNING):
            kicad_mapping.load_into_db(db, data_dir)
        assert any("GHOST123" in rec.message for rec in caplog.records)
        assert db.execute(
            "SELECT * FROM kicad_part_state WHERE part_id='GHOST123'"
        ).fetchone() is None

        # Now persist -- e.g. because some OTHER part's override changed --
        # and confirm GHOST123's entries survive in the JSON even though
        # they were never written into SQLite.
        _insert_part(db, "SOMEOTHERPART")
        kicad_mapping._persist(db, data_dir)

        with open(os.path.join(data_dir, "kicad_mapping.json"), encoding="utf-8") as f:
            written = json.load(f)
        assert "GHOST123" in written["part_overrides"]
        assert written["part_overrides"]["GHOST123"]["kicad_symbol"] == "Device:R"
        assert "GHOST123" in written["part_category_cache"]
        assert written["part_category_cache"]["GHOST123"]["resolved_category_id"] == "1"


class TestSchemaVersionBumpRebuild:
    def test_dropping_and_rebuilding_cache_restores_identical_rows(self, db, data_dir):
        """Prove SQLite really is a deletable derived view for this entity
        (docs/entity-store.md rule 3): drop kicad_categories/kicad_part_state
        (simulating a SCHEMA_VERSION bump) and re-run create_schema +
        load_into_db -- the restored rows must match the originals.
        """
        _insert_part(db, "C15850")
        _write_mapping(data_dir, {
            "version": 1,
            "categories": [_sample_category()],
            "part_overrides": {"C15850": _sample_override()},
            "part_category_cache": {"C15850": _sample_cache_entry()},
        })
        kicad_mapping.load_into_db(db, data_dir)

        before_cat = dict(db.execute("SELECT * FROM kicad_categories WHERE id='1'").fetchone())
        before_part = dict(
            db.execute("SELECT * FROM kicad_part_state WHERE part_id='C15850'").fetchone()
        )

        # Simulate a SCHEMA_VERSION bump dropping the derived tables.
        db.executescript(
            "DROP TABLE kicad_part_state; DROP TABLE kicad_categories;"
        )
        cache_db.create_schema(db)
        assert db.execute("SELECT * FROM kicad_categories").fetchall() == []
        assert db.execute("SELECT * FROM kicad_part_state").fetchall() == []

        kicad_mapping.load_into_db(db, data_dir)

        after_cat = dict(db.execute("SELECT * FROM kicad_categories WHERE id='1'").fetchone())
        after_part = dict(
            db.execute("SELECT * FROM kicad_part_state WHERE part_id='C15850'").fetchone()
        )
        assert after_cat == before_cat
        assert after_part == before_part
