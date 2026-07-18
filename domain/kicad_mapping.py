"""KiCad category/part-mapping — durable entity for the `/v1/kicad/*` HTTP library.

`data/kicad_mapping.json` is the durable store (see `docs/entity-store.md`):
    {"version": 1,
     "categories": [...],
     "part_overrides": {"<canonical part_id>": {...}, ...},
     "part_category_cache": {"<canonical part_id>": {...}, ...}}

Keyed by canonical `part_id` throughout — the SAME key `domain/part_registry.py`
derives/registers, so an override set on a SKU stays attached to it across
distributor-PN enrichment (see `docs/entity-store.md`'s "Consistency and
recovery" section).

`categories` are the resolved KiCad-facing taxonomy (JLCPCB-catalog-sourced or
`categorize.py`-fallback-sourced). `part_overrides` is user-curated per-SKU state
(symbol/footprint/datasheet overrides, plus the eligibility tri-state override
bit) that must survive cache deletion. `part_category_cache` is a *memoized*
LCSC->JLCPCB-category lookup result — safe to delete/re-resolve, unlike
`part_overrides`.

SQLite (`kicad_categories`, `kicad_part_state`) is a deletable materialized
view restored from this file by `load_into_db`, mirroring
`domain/generic_parts.py`'s `_persist`/`load_into_db` pair: missing file ->
empty tables (self-healing, matches `part_registry.py::load`'s missing-file
behavior); unsupported `version` -> raise (fail loudly, matches
`generic_parts.py::load_into_db`); a `part_overrides`/`part_category_cache`
entry referencing a `part_id` not currently in `parts` is warn-logged and
skipped in the DB but retained in the JSON on next `_persist` (the part may
return — same contract as `generic_parts.json`'s member-retention logic).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import csv_io
from categorize import categorize

logger = logging.getLogger(__name__)

_JSON_FILE = "kicad_mapping.json"

_CATEGORY_COLUMNS = (
    "id", "name", "source", "jlcpcb_catalog_name", "categorize_bucket",
    "default_symbol", "default_footprint_from_package", "default_reference",
)


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def _bool_to_int(value: bool | None) -> int | None:
    """Tri-state bool -> nullable SQLite int (None/1/0)."""
    if value is None:
        return None
    return 1 if value else 0


def _int_to_bool(value: int | None) -> bool | None:
    """Nullable SQLite int -> tri-state bool (None/True/False)."""
    if value is None:
        return None
    return bool(value)


def _category_row_to_dict(row: Any) -> dict[str, Any]:
    cat: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "source": row["source"],
    }
    if row["source"] == "jlcpcb":
        cat["jlcpcb_catalog_name"] = row["jlcpcb_catalog_name"]
    elif row["source"] == "categorize_fallback":
        cat["categorize_bucket"] = row["categorize_bucket"]
    cat["default_symbol"] = row["default_symbol"]
    cat["default_footprint_from_package"] = bool(row["default_footprint_from_package"])
    cat["default_reference"] = row["default_reference"]
    return cat


def resolve_category_for_part(row: dict[str, Any], mapping: dict[str, Any]) -> str | None:
    """Task 5: category resolution fallback chain, operating on the raw
    `kicad_mapping.json` shape (not SQLite) -- design doc §2.3/§4.1 MVP.

    Deliberately does NOT check the per-SKU explicit `part_overrides`
    category -- that check has strictly higher precedence and happens one
    level up (`domain.kicad_view.resolve_category_id` reads
    `kicad_part_state.category_id` before ever calling this). This
    function is only the "no explicit override" fallback:

    1. `part_category_cache[row["part_id"]].resolved_category_id`, if
       present -- a memoized LCSC->JLCPCB (or any future) resolution wins
       outright; `categorize.py` is not even consulted (proves the
       cache-hit short-circuit -- see the required "cache wins" test).
    2. Else, run `categorize.categorize(row)` (dubIS's existing shelf-
       taxonomy bucketing -- reused, not reimplemented) and match the
       resulting bucket string against a `source: "categorize_fallback"`
       category entry's `categorize_bucket` field.
    3. Else `None` -- unresolved, therefore invisible (per the visibility
       gate in `domain/kicad_view.py`), not an error.

    `row` is a ledger-shaped dict (`categorize.categorize`'s expected
    keys: "Description", "Manufacture Part Number", "Manufacturer") with
    an additional `"part_id"` key used only for the cache lookup here.
    `mapping` is the full `kicad_mapping.json`-shaped dict (or an
    equivalent in-memory projection built from SQLite by
    `domain.kicad_view`).
    """
    part_id = row.get("part_id")
    cache = mapping.get("part_category_cache", {})
    if part_id and part_id in cache:
        cached_id = cache[part_id].get("resolved_category_id")
        if cached_id:
            return cached_id

    bucket = categorize(row)
    for cat in mapping.get("categories", []):
        if cat.get("source") == "categorize_fallback" and cat.get("categorize_bucket") == bucket:
            return cat.get("id")
    return None


def load_into_db(conn: Any, data_dir: str) -> None:
    """Restore kicad_mapping.json into SQLite (kicad_categories, kicad_part_state).

    Idempotent. Called during rebuild AFTER parts are loaded (so the
    known-parts skip-check below is meaningful), alongside
    `domain/generic_parts.py::load_into_db` and `domain/part_registry.py`'s
    load. Missing file -> leaves the (already-empty) tables alone
    (self-healing). Unsupported version -> raises ValueError.
    """
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if data.get("version") != 1:
        raise ValueError(f"Unsupported kicad_mapping.json version: {data.get('version')!r}")

    for cat in data.get("categories", []):
        conn.execute(
            """INSERT OR REPLACE INTO kicad_categories
               (id, name, source, jlcpcb_catalog_name, categorize_bucket,
                default_symbol, default_footprint_from_package, default_reference)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                cat["id"], cat["name"], cat["source"],
                cat.get("jlcpcb_catalog_name"), cat.get("categorize_bucket"),
                cat.get("default_symbol"),
                1 if cat.get("default_footprint_from_package") else 0,
                cat.get("default_reference"),
            ),
        )

    known_parts = {r[0] for r in conn.execute("SELECT part_id FROM parts").fetchall()}

    overrides = data.get("part_overrides", {})
    cache = data.get("part_category_cache", {})
    for part_id in set(overrides) | set(cache):
        if part_id not in known_parts:
            # Part was deleted from the ledger after this override/cache entry
            # was set. Inserting would violate the parts FK. Warn (visible),
            # keep the record in JSON (the part may return), skip the DB row.
            logger.warning(
                "kicad_mapping.json entry references unknown part %s — skipped", part_id
            )
            continue
        ov = overrides.get(part_id, {})
        c = cache.get(part_id, {})
        conn.execute(
            """INSERT OR REPLACE INTO kicad_part_state
               (part_id, category_id, kicad_symbol, kicad_footprint, kicad_datasheet,
                eligible_override, cache_lcsc, cache_jlcpcb_catalog_name,
                cache_resolved_category_id, cache_resolved_via, cache_resolved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                part_id,
                ov.get("category_id"),
                ov.get("kicad_symbol"),
                ov.get("kicad_footprint"),
                ov.get("kicad_datasheet"),
                _bool_to_int(ov.get("eligible_override")),
                c.get("lcsc"),
                c.get("jlcpcb_catalog_name"),
                c.get("resolved_category_id"),
                c.get("resolved_via"),
                c.get("resolved_at"),
            ),
        )
    conn.commit()
    logger.info(
        "Loaded %d KiCad categories, %d part states from %s",
        len(data.get("categories", [])), len(set(overrides) | set(cache)), path,
    )


def _persist(conn: Any, data_dir: str) -> None:
    """Write all durable KiCad mapping state from SQLite to kicad_mapping.json.

    Category defaults and per-SKU overrides/cache are the entity's source of
    truth persisted here; SQLite is a deletable cache (same pattern as
    generic_parts.json).
    """
    categories = [
        _category_row_to_dict(r)
        for r in conn.execute("SELECT * FROM kicad_categories ORDER BY id").fetchall()
    ]

    part_overrides: dict[str, dict[str, Any]] = {}
    part_category_cache: dict[str, dict[str, Any]] = {}
    for r in conn.execute("SELECT * FROM kicad_part_state").fetchall():
        has_override = (
            r["category_id"] or r["kicad_symbol"] or r["kicad_footprint"]
            or r["kicad_datasheet"] or r["eligible_override"] is not None
        )
        if has_override:
            part_overrides[r["part_id"]] = {
                "category_id": r["category_id"],
                "kicad_symbol": r["kicad_symbol"],
                "kicad_footprint": r["kicad_footprint"],
                "kicad_datasheet": r["kicad_datasheet"],
                "eligible_override": _int_to_bool(r["eligible_override"]),
            }
        if r["cache_resolved_category_id"] is not None:
            part_category_cache[r["part_id"]] = {
                "lcsc": r["cache_lcsc"],
                "jlcpcb_catalog_name": r["cache_jlcpcb_catalog_name"],
                "resolved_category_id": r["cache_resolved_category_id"],
                "resolved_via": r["cache_resolved_via"],
                "resolved_at": r["cache_resolved_at"],
            }

    # Retain JSON-only records for part_ids not currently in `parts` (warn-
    # skipped at load time -- see load_into_db). Without this, the next
    # unrelated mutation's _persist snapshot -- built purely from the DB --
    # would silently erase them, breaking the "the part may return" promise.
    known_parts = {r[0] for r in conn.execute("SELECT part_id FROM parts").fetchall()}
    existing_path = _json_path(data_dir)
    if os.path.exists(existing_path):
        try:
            with open(existing_path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, ValueError) as e:
            logger.warning("Could not read existing %s for override retention: %s",
                           existing_path, e)
            existing = {}
        for part_id, ov in existing.get("part_overrides", {}).items():
            if part_id not in known_parts and part_id not in part_overrides:
                part_overrides[part_id] = ov
        for part_id, c in existing.get("part_category_cache", {}).items():
            if part_id not in known_parts and part_id not in part_category_cache:
                part_category_cache[part_id] = c

    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps(
            {
                "version": 1,
                "categories": categories,
                "part_overrides": part_overrides,
                "part_category_cache": part_category_cache,
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
