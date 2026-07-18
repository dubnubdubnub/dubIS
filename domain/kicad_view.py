"""KiCad HTTP Library read-only view -- the single gating seam for
`/v1/kicad/*` routes (design doc `docs/plans/2026-07-17-phase4-kicad-design.md`
§3: "gating logic lives in one place ... not duplicated across route
handlers").

Reads only `cache.db` (the `parts` table + Task 2's `kicad_categories` /
`kicad_part_state` tables) -- never touches CSVs or the network. Callers
(server/routes/kicad.py) are responsible for calling
`api._load_organized()` first so the cache is fresh before these functions
read it.

**Seams left for later plan tasks** (do not remove these comments when
extending -- they mark exactly what changes and what doesn't):

- `resolve_category_id` -- TASK 5's scope. For now this only reads the
  already-materialized `kicad_part_state.category_id` (a per-SKU override
  recorded in `kicad_mapping.json`'s `part_overrides`). It does NOT yet
  derive a category from a SKU with no explicit override (the LCSC ->
  JLCPCB-taxonomy lookup, or the `categorize.py` bucket-match fallback,
  per design doc §2.3). Task 5 extends this one function; nothing else in
  this module, nor any route handler, needs to change when it does.
- `is_eligible` -- TASK 4's scope. For now this only honors the explicit
  per-SKU `eligible_override` tri-state (`False` force-excludes, `True`/
  `None` both currently pass) and does NOT yet apply the category-level
  default-exclude rule for the "Development Boards, Kits, Programmers"
  bucket (design doc §3 point 3). Task 3 intentionally "expose[s] all
  mapped parts for now" per the plan brief -- Task 4 adds the bucket check
  inside this one function.

Everything else here (symbol resolution, the visibility AND of the three
gates, field/summary/detail shaping, string-encoding) is this task's real,
non-stubbed implementation -- design doc §3 states it once, cross-cutting,
and it belongs here regardless of which task fills in category/eligibility
resolution.
"""

from __future__ import annotations

import sqlite3

from spec_extractor import extract_spec

# Fixed visible-field set for v1 (design doc §1.4/§2.4) -- not configurable.
_VISIBLE_FIELDS = frozenset({"Value", "MPN", "LCSC", "Datasheet"})


def resolve_category_id(conn: sqlite3.Connection, part_id: str) -> str | None:
    """The SKU's resolved KiCad category id, or None if unresolved.

    TASK 5 SEAM: currently reads only the explicit per-SKU override in
    `kicad_part_state.category_id`. See module docstring.
    """
    row = conn.execute(
        "SELECT category_id FROM kicad_part_state WHERE part_id = ?", (part_id,),
    ).fetchone()
    if row is None:
        return None
    return row["category_id"]


def resolve_symbol(
    conn: sqlite3.Connection, part_id: str, category_id: str | None,
) -> str | None:
    """symbolIdStr resolution order (design doc §1.4/§3 point 2):
    per-SKU `kicad_symbol` override -> resolved category's `default_symbol`
    -> None (unresolved -> invisible).
    """
    row = conn.execute(
        "SELECT kicad_symbol FROM kicad_part_state WHERE part_id = ?", (part_id,),
    ).fetchone()
    override = row["kicad_symbol"] if row else None
    if override:
        return override
    if category_id is None:
        return None
    cat = conn.execute(
        "SELECT default_symbol FROM kicad_categories WHERE id = ?", (category_id,),
    ).fetchone()
    if cat is None or not cat["default_symbol"]:
        return None
    return cat["default_symbol"]


def is_eligible(conn: sqlite3.Connection, part_id: str, category_id: str | None) -> bool:
    """Eligibility gate (design doc §3 point 3).

    TASK 4 SEAM: currently only honors the explicit `eligible_override`
    tri-state force-exclude (`False`); the category-level default-exclude
    bucket rule is not yet applied -- "expose all mapped parts for now"
    per the plan brief. See module docstring.
    """
    row = conn.execute(
        "SELECT eligible_override FROM kicad_part_state WHERE part_id = ?", (part_id,),
    ).fetchone()
    override = row["eligible_override"] if row else None  # 1 / 0 / None
    if override == 0:
        return False
    return True


def is_visible(conn: sqlite3.Connection, part_id: str) -> tuple[bool, str | None, str | None]:
    """The one visibility gate (design doc §3), AND of all three conditions.

    Returns (visible, category_id, symbol) so callers that already need
    category_id/symbol (list/detail builders) don't re-derive them.
    """
    category_id = resolve_category_id(conn, part_id)
    if category_id is None:
        return False, None, None
    symbol = resolve_symbol(conn, part_id, category_id)
    if not symbol:
        return False, category_id, None
    if not is_eligible(conn, part_id, category_id):
        return False, category_id, symbol
    return True, category_id, symbol


def _fetch_part_row(conn: sqlite3.Connection, part_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT part_id, lcsc, mpn, digikey, pololu, mouser, manufacturer, "
        "description, package FROM parts WHERE part_id = ?", (part_id,),
    ).fetchone()


def _name(part: sqlite3.Row) -> str:
    """mpn, else lcsc/digikey/pololu/mouser in that order (design doc §2.4)."""
    for col in ("mpn", "lcsc", "digikey", "pololu", "mouser"):
        val = (part[col] or "").strip()
        if val:
            return val
    return part["part_id"]


def _keywords(description: str) -> str:
    """Lowercased, punctuation-stripped, deduplicated tokens from the description."""
    tokens = []
    seen = set()
    for raw in description.lower().split():
        tok = "".join(ch for ch in raw if ch.isalnum())
        if tok and tok not in seen:
            seen.add(tok)
            tokens.append(tok)
    return " ".join(tokens)


def _footprint(conn: sqlite3.Connection, part: sqlite3.Row, category_id: str | None) -> str:
    row = conn.execute(
        "SELECT kicad_footprint FROM kicad_part_state WHERE part_id = ?", (part["part_id"],),
    ).fetchone()
    override = row["kicad_footprint"] if row else None
    if override:
        return override
    if category_id is None:
        return ""
    cat = conn.execute(
        "SELECT default_footprint_from_package FROM kicad_categories WHERE id = ?",
        (category_id,),
    ).fetchone()
    if cat and cat["default_footprint_from_package"]:
        return part["package"] or ""
    return ""


def _footprint_filters(footprint: str) -> list[str]:
    if not footprint:
        return []
    short = footprint.split(":")[-1]
    return [f"{short}*"]


def _datasheet(conn: sqlite3.Connection, part_id: str) -> str:
    row = conn.execute(
        "SELECT kicad_datasheet FROM kicad_part_state WHERE part_id = ?", (part_id,),
    ).fetchone()
    override = row["kicad_datasheet"] if row else None
    return override or ""


def _summary(part: sqlite3.Row, footprint: str) -> dict:
    description = part["description"] or ""
    return {
        "id": part["part_id"],
        "name": _name(part),
        "description": description,
        "keywords": _keywords(description),
        "footprint_filters": _footprint_filters(footprint),
    }


def _detail(conn: sqlite3.Connection, part: sqlite3.Row, category_id: str | None, symbol: str) -> dict:
    description = part["description"] or ""
    footprint = _footprint(conn, part, category_id)
    spec = extract_spec(description, part["package"] or "")
    value_display = str(spec.get("value_display", ""))

    fields = {
        "footprint": {"value": footprint, "visible": "False"},
        "datasheet": {"value": _datasheet(conn, part["part_id"]), "visible": "True"},
        "Value": {"value": value_display, "visible": "True"},
        "MPN": {"value": part["mpn"] or "", "visible": "True"},
        "LCSC": {"value": part["lcsc"] or "", "visible": "True"},
        "Manufacturer": {"value": part["manufacturer"] or "", "visible": "False"},
    }

    return {
        "id": part["part_id"],
        "name": _name(part),
        "symbolIdStr": symbol,
        "description": description,
        "keywords": _keywords(description),
        "exclude_from_bom": "False",
        "exclude_from_board": "False",
        "exclude_from_sim": "False",
        "footprint_filters": _footprint_filters(footprint),
        "fields": fields,
    }


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    """Categories with >= 1 visible member (design doc §1.2). Omits dead branches."""
    cats = conn.execute(
        "SELECT id, name, jlcpcb_catalog_name, categorize_bucket FROM kicad_categories",
    ).fetchall()
    out = []
    for cat in cats:
        member_ids = [
            r["part_id"] for r in conn.execute(
                "SELECT part_id FROM kicad_part_state WHERE category_id = ?", (cat["id"],),
            ).fetchall()
        ]
        if not any(is_visible(conn, pid)[0] for pid in member_ids):
            continue
        description = cat["jlcpcb_catalog_name"] or cat["categorize_bucket"] or cat["name"]
        out.append({"id": cat["id"], "name": cat["name"], "description": description})
    return out


def visible_parts_by_category(conn: sqlite3.Connection, category_id: str) -> list[dict]:
    """Summary projection (no `fields`, no `symbolIdStr`) of visible SKUs in a category."""
    member_ids = [
        r["part_id"] for r in conn.execute(
            "SELECT part_id FROM kicad_part_state WHERE category_id = ?", (category_id,),
        ).fetchall()
    ]
    out = []
    for part_id in member_ids:
        visible, resolved_category_id, _symbol = is_visible(conn, part_id)
        if not visible:
            continue
        part = _fetch_part_row(conn, part_id)
        if part is None:
            continue
        footprint = _footprint(conn, part, resolved_category_id)
        out.append(_summary(part, footprint))
    return out


def resolve_part_detail(conn: sqlite3.Connection, part_id: str) -> dict | None:
    """Full shape for a visible SKU, or None (route maps to 404) if the id
    is unknown, unresolved, or gated-invisible."""
    visible, category_id, symbol = is_visible(conn, part_id)
    if not visible:
        return None
    part = _fetch_part_row(conn, part_id)
    if part is None:
        return None
    return _detail(conn, part, category_id, symbol)
