"""Tests for Task 5: category resolution -- hand-seeded taxonomy +
`categorize.py` fallback filling in `domain.kicad_view.resolve_category_id`
(and its pure-dict sibling `domain.kicad_mapping.resolve_category_for_part`).

Design doc: docs/plans/2026-07-17-phase4-kicad-design.md §2.3/§4.1.

Resolution order under test:
1. Explicit per-SKU override (`kicad_part_state.category_id`) wins outright.
2. A memoized `part_category_cache` entry wins -- `categorize.py` is not
   even consulted (proves the cache-hit short-circuit).
3. `categorize.py`'s bucket for the SKU, matched against a seeded
   `categorize_fallback` category's `categorize_bucket`.
4. Unresolved (`None`) if none of the above apply -- not an error.

This task is purely READ-TIME: no code path here writes into
`kicad_part_state`'s cache columns, so there is no cache-write/no-clobber
concern to prove for this task's own changes. (The cache-hit test below
proves the *read* short-circuit only; it seeds the cache column directly
via SQL/dict, it never invokes a write path.)
"""

from __future__ import annotations

from domain import kicad_mapping, kicad_view

_RESISTOR_BUCKET = "Passives - Resistors > Chip Resistors"
_MLCC_BUCKET = "Passives - Capacitors > MLCC"


def _insert_part(db, part_id, *, lcsc=None, mpn="", manufacturer="", description="Widget",
                  package="0603"):
    db.execute(
        "INSERT INTO parts (part_id, lcsc, mpn, manufacturer, description, package, section)"
        " VALUES (?,?,?,?,?,?,?)",
        (part_id, lcsc if lcsc is not None else part_id, mpn, manufacturer, description,
         package, "Other"),
    )
    db.execute("INSERT INTO stock (part_id, quantity, unit_price) VALUES (?,10,0.01)", (part_id,))
    db.commit()


def _insert_category(db, cat_id, *, categorize_bucket, name=None, default_symbol="Device:R"):
    db.execute(
        "INSERT INTO kicad_categories (id, name, source, jlcpcb_catalog_name, "
        "categorize_bucket, default_symbol, default_footprint_from_package, default_reference)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            cat_id, name or categorize_bucket, "categorize_fallback", None,
            categorize_bucket, default_symbol, 1, None,
        ),
    )
    db.commit()


# ── domain.kicad_mapping.resolve_category_for_part -- pure dict logic ──────


class TestResolveCategoryForPartPure:
    def _mapping(self, categories=None, cache=None):
        return {
            "version": 1,
            "categories": categories or [],
            "part_overrides": {},
            "part_category_cache": cache or {},
        }

    def _seeded_category(self, cat_id="1", bucket=_RESISTOR_BUCKET):
        return {
            "id": cat_id,
            "name": "Passives/Resistors",
            "source": "categorize_fallback",
            "categorize_bucket": bucket,
            "default_symbol": "Device:R",
            "default_footprint_from_package": True,
            "default_reference": "R",
        }

    def test_categorize_bucket_matches_seeded_category(self):
        row = {
            "part_id": "R1",
            "Description": "RES SMD 10K OHM 1% 1/10W 0402",
            "Manufacture Part Number": "RC0402FR-0710KL",
            "Manufacturer": "Yageo",
        }
        mapping = self._mapping(categories=[self._seeded_category()])

        assert kicad_mapping.resolve_category_for_part(row, mapping) == "1"

    def test_bucket_with_no_seeded_category_resolves_to_none(self):
        row = {
            "part_id": "MCU1",
            "Description": "32-bit ARM Microcontroller",
            "Manufacture Part Number": "STM32F103C8T6",
            "Manufacturer": "STMicroelectronics",
        }
        # No seeded category for "ICs - Microcontrollers" -- not in MVP seed.
        mapping = self._mapping(categories=[self._seeded_category()])

        assert kicad_mapping.resolve_category_for_part(row, mapping) is None

    def test_cache_entry_wins_without_consulting_categorize(self, monkeypatch):
        row = {
            "part_id": "C1",
            "Description": "This description would normally bucket as Diodes",
            "Manufacture Part Number": "1N4148",
            "Manufacturer": "X",
        }
        mapping = self._mapping(
            categories=[self._seeded_category(cat_id="1", bucket=_RESISTOR_BUCKET)],
            cache={"C1": {"resolved_category_id": "99"}},
        )

        def _boom(_row):
            raise AssertionError("categorize.py must not be consulted on a cache hit")

        monkeypatch.setattr(kicad_mapping, "categorize", _boom)

        assert kicad_mapping.resolve_category_for_part(row, mapping) == "99"

    def test_dev_boards_bucket_resolves_to_its_category_id(self):
        row = {
            "part_id": "DEV1",
            "Description": "ST-LINK/V3 In-Circuit Debugger/Programmer",
            "Manufacture Part Number": "STLINK-V3MINIE",
            "Manufacturer": "STMicroelectronics",
        }
        mapping = self._mapping(categories=[self._seeded_category(
            cat_id="2", bucket="Development Boards, Kits, Programmers",
        )])

        assert kicad_mapping.resolve_category_for_part(row, mapping) == "2"


# ── domain.kicad_view.resolve_category_id -- SQLite-backed integration ─────


class TestResolveCategoryIdExplicitOverrideWins:
    def test_explicit_override_wins_over_categorize_fallback(self, db):
        # Description would bucket as "Diodes" via categorize.py, but an
        # explicit per-SKU override to a different category must win.
        _insert_part(db, "R1", description="Diode Rectifier 1A 100V DO-214")
        _insert_category(db, "1", categorize_bucket=_RESISTOR_BUCKET, default_symbol="Device:R")
        _insert_category(db, "4", categorize_bucket="Diodes", default_symbol="Device:D")
        db.execute(
            "INSERT INTO kicad_part_state (part_id, category_id) VALUES (?,?)", ("R1", "1"),
        )
        db.commit()

        assert kicad_view.resolve_category_id(db, "R1") == "1"


class TestResolveCategoryIdCategorizeFallback:
    def test_resistor_row_resolves_via_categorize_fallback(self, db):
        _insert_part(
            db, "R1", mpn="RC0402FR-0710KL", manufacturer="Yageo",
            description="RES SMD 10K OHM 1% 1/10W 0402",
        )
        _insert_category(db, "1", categorize_bucket=_RESISTOR_BUCKET, default_symbol="Device:R")
        # No kicad_part_state row at all -- pure fallback resolution.

        assert kicad_view.resolve_category_id(db, "R1") == "1"

    def test_ceramic_cap_row_resolves_via_categorize_fallback(self, db):
        _insert_part(
            db, "C1", mpn="CL10B104KB8NNNC", manufacturer="Samsung Electro-Mechanics",
            description="100nF ±10% 16V X7R 0603 MLCC",
        )
        _insert_category(db, "2", categorize_bucket=_MLCC_BUCKET, default_symbol="Device:C")

        assert kicad_view.resolve_category_id(db, "C1") == "2"

    def test_unclassifiable_bucket_resolves_to_none(self, db):
        _insert_part(db, "MYSTERY1", description="Nothing maps to this")
        _insert_category(db, "1", categorize_bucket=_RESISTOR_BUCKET, default_symbol="Device:R")

        assert kicad_view.resolve_category_id(db, "MYSTERY1") is None

    def test_dev_boards_bucket_resolves_to_its_category_id(self, db):
        _insert_part(
            db, "DEV1", mpn="STLINK-V3MINIE", manufacturer="STMicroelectronics",
            description="ST-LINK/V3 In-Circuit Debugger/Programmer",
        )
        _insert_category(
            db, "2", categorize_bucket="Development Boards, Kits, Programmers",
            default_symbol=None,
        )

        assert kicad_view.resolve_category_id(db, "DEV1") == "2"

    def test_unknown_part_id_resolves_to_none(self, db):
        assert kicad_view.resolve_category_id(db, "DOES-NOT-EXIST") is None


class TestResolveCategoryIdCacheHit:
    def test_memoized_cache_entry_wins_over_categorize_fallback(self, db):
        # Description would bucket as "Diodes" via categorize.py, but a
        # pre-existing part_category_cache resolution (e.g. hand-entered, or
        # from a future backfill run) short-circuits that.
        _insert_part(db, "C1", description="Diode Rectifier 1A 100V DO-214")
        _insert_category(db, "1", categorize_bucket=_RESISTOR_BUCKET, default_symbol="Device:R")
        _insert_category(db, "4", categorize_bucket="Diodes", default_symbol="Device:D")
        db.execute(
            "INSERT INTO kicad_part_state (part_id, category_id, cache_resolved_category_id)"
            " VALUES (?,?,?)",
            ("C1", None, "1"),
        )
        db.commit()

        assert kicad_view.resolve_category_id(db, "C1") == "1"

    def test_no_clobber_of_pre_existing_override_on_same_part_id(self, db):
        """This task performs no cache WRITE at all -- resolve_category_id is
        purely read-time -- so there is nothing here that could clobber a
        durable per-SKU override. This test documents and locks in that
        invariant: calling resolve_category_id (repeatedly, including on a
        part with both an override and a cache column already populated)
        must never mutate kicad_part_state's override columns."""
        _insert_part(db, "P1", mpn="MPN-1", description="Widget")
        _insert_category(db, "1", categorize_bucket=_RESISTOR_BUCKET, default_symbol="Device:R")
        db.execute(
            "INSERT INTO kicad_part_state "
            "(part_id, category_id, kicad_symbol, kicad_footprint, kicad_datasheet, "
            " eligible_override, cache_resolved_category_id)"
            " VALUES (?,?,?,?,?,?,?)",
            ("P1", "1", "Device:R_Small", "Resistor_SMD:R_0402", "https://example.com/ds.pdf",
             1, "1"),
        )
        db.commit()

        before = dict(db.execute(
            "SELECT * FROM kicad_part_state WHERE part_id = 'P1'",
        ).fetchone())

        # Call the read-time resolver several times, as a live server would.
        for _ in range(3):
            assert kicad_view.resolve_category_id(db, "P1") == "1"

        after = dict(db.execute(
            "SELECT * FROM kicad_part_state WHERE part_id = 'P1'",
        ).fetchone())

        assert after == before
        assert after["kicad_symbol"] == "Device:R_Small"
        assert after["kicad_footprint"] == "Resistor_SMD:R_0402"
        assert after["kicad_datasheet"] == "https://example.com/ds.pdf"
        assert after["eligible_override"] == 1


class TestListingsPickUpFallbackResolvedParts:
    """A SKU resolved purely via the categorize.py fallback (no
    kicad_part_state row at all) must still show up in list_categories'
    membership count and visible_parts_by_category's listing -- proves the
    membership-enumeration fix (walking all `parts`, not just rows already
    present in `kicad_part_state`)."""

    def test_fallback_only_resistor_appears_in_category_listing(self, db):
        _insert_part(
            db, "R1", mpn="RC0402FR-0710KL", manufacturer="Yageo",
            description="RES SMD 10K OHM 1% 1/10W 0402",
        )
        _insert_category(db, "1", categorize_bucket=_RESISTOR_BUCKET, default_symbol="Device:R")

        cats = kicad_view.list_categories(db)
        assert {c["id"] for c in cats} == {"1"}

        parts = kicad_view.visible_parts_by_category(db, "1")
        assert {p["id"] for p in parts} == {"R1"}
