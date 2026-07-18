"""Tests for domain.kicad_view.is_eligible -- Task 4: default-exclude the
"Development Boards, Kits, Programmers" categorize.py bucket, with a
per-SKU eligible_override tri-state that can force either direction.

Design doc: docs/plans/2026-07-17-phase4-kicad-design.md §3 point 3.
"""

from __future__ import annotations

from domain import kicad_view

_DEV_BOARD_BUCKET = "Development Boards, Kits, Programmers"


def _insert_part(db, part_id, *, lcsc=None, description="Widget", package="0603", section="Other"):
    db.execute(
        "INSERT INTO parts (part_id, lcsc, mpn, manufacturer, description, package, section)"
        " VALUES (?,?,?,?,?,?,?)",
        (part_id, lcsc if lcsc is not None else part_id, "", "", description, package, section),
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
            categorize_bucket, default_symbol, 0, None,
        ),
    )
    db.commit()


def _insert_part_state(db, part_id, category_id, *, eligible_override=None):
    db.execute(
        "INSERT INTO kicad_part_state (part_id, category_id, eligible_override)"
        " VALUES (?,?,?)",
        (part_id, category_id, eligible_override),
    )
    db.commit()


class TestDevBoardDefaultExclude:
    def test_dev_board_no_override_is_excluded(self, db):
        _insert_part(db, "STLINKV3MINIE")
        _insert_category(db, "2", categorize_bucket=_DEV_BOARD_BUCKET)
        _insert_part_state(db, "STLINKV3MINIE", "2", eligible_override=None)

        assert kicad_view.is_eligible(db, "STLINKV3MINIE", "2") is False

    def test_dev_board_force_include_override_is_eligible(self, db):
        """The ESP32/SoM case: force-include wins even in the excluded bucket."""
        _insert_part(db, "ESP32-WROOM-32E-N4")
        _insert_category(db, "2", categorize_bucket=_DEV_BOARD_BUCKET)
        _insert_part_state(db, "ESP32-WROOM-32E-N4", "2", eligible_override=1)

        assert kicad_view.is_eligible(db, "ESP32-WROOM-32E-N4", "2") is True


class TestNormalCategoryDefaultInclude:
    def test_normal_category_no_override_is_eligible(self):
        pass  # covered via db-fixture variant below

    def test_normal_category_no_override_is_eligible_db(self, db):
        _insert_part(db, "C15850")
        _insert_category(db, "1", categorize_bucket="Passives - Capacitors")
        _insert_part_state(db, "C15850", "1", eligible_override=None)

        assert kicad_view.is_eligible(db, "C15850", "1") is True

    def test_normal_category_force_exclude_override_is_excluded(self):
        """The mislabeled bench tool case: force-exclude wins even outside
        the excluded bucket."""
        pass

    def test_normal_category_force_exclude_override_is_excluded_db(self, db):
        _insert_part(db, "BENCH-TOOL-1")
        _insert_category(db, "1", categorize_bucket="Passives - Capacitors")
        _insert_part_state(db, "BENCH-TOOL-1", "1", eligible_override=0)

        assert kicad_view.is_eligible(db, "BENCH-TOOL-1", "1") is False


class TestTriStateNoneUsesSectionDefault:
    def test_none_override_dev_board_bucket_defaults_to_excluded(self, db):
        _insert_part(db, "DEV1")
        _insert_category(db, "2", categorize_bucket=_DEV_BOARD_BUCKET)
        _insert_part_state(db, "DEV1", "2", eligible_override=None)
        assert kicad_view.is_eligible(db, "DEV1", "2") is False

    def test_none_override_non_dev_board_bucket_defaults_to_included(self, db):
        _insert_part(db, "R1")
        _insert_category(db, "1", categorize_bucket="Passives - Resistors")
        _insert_part_state(db, "R1", "1", eligible_override=None)
        assert kicad_view.is_eligible(db, "R1", "1") is True


class TestNoCategoryRow:
    def test_unresolved_category_id_none_is_eligible_by_default(self, db):
        """is_eligible itself, given category_id=None (unresolved), does not
        apply the dev-board exclusion (there's no bucket to match) -- the
        overall is_visible() gate independently invalidates unresolved-category
        SKUs via its own earlier check, per design doc §3 point 1 precedence."""
        _insert_part(db, "NOCAT1")
        assert kicad_view.is_eligible(db, "NOCAT1", None) is True


class TestIsVisibleIntegration:
    def test_is_visible_excludes_dev_board_end_to_end(self, db):
        _insert_part(db, "STLINKV3MINIE")
        _insert_category(db, "2", categorize_bucket=_DEV_BOARD_BUCKET, default_symbol="Device:R")
        _insert_part_state(db, "STLINKV3MINIE", "2", eligible_override=None)

        visible, category_id, symbol = kicad_view.is_visible(db, "STLINKV3MINIE")
        assert visible is False
        assert category_id == "2"

    def test_is_visible_includes_dev_board_with_force_include(self, db):
        _insert_part(db, "ESP32-WROOM-32E-N4")
        _insert_category(db, "2", categorize_bucket=_DEV_BOARD_BUCKET, default_symbol="Device:U")
        _insert_part_state(db, "ESP32-WROOM-32E-N4", "2", eligible_override=1)

        visible, category_id, symbol = kicad_view.is_visible(db, "ESP32-WROOM-32E-N4")
        assert visible is True
        assert category_id == "2"
        assert symbol == "Device:U"


class TestSectionNeverLeaks:
    """Section is fetched internally (for the eligibility check's category
    resolution path) but must never appear in any response-shaped payload
    built by kicad_view. (The HTTP-level privacy test lives in
    tests/python/server/test_kicad_routes.py; this is the domain-level
    companion confirming _fetch_part_row's added column doesn't leak into
    _summary/_detail.)"""

    def test_summary_and_detail_never_include_section(self, db):
        _insert_part(db, "C15850", section="Passives - Very Secret Section")
        _insert_category(db, "1", categorize_bucket="Passives - Capacitors")
        _insert_part_state(db, "C15850", "1", eligible_override=None)

        part = kicad_view._fetch_part_row(db, "C15850")
        footprint = kicad_view._footprint(db, part, "1")
        summary = kicad_view._summary(part, footprint)
        detail = kicad_view._detail(db, part, "1", "Device:R")

        assert "section" not in summary
        assert "section" not in detail
        assert "Very Secret Section" not in str(summary)
        assert "Very Secret Section" not in str(detail)
