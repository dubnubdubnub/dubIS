"""GET /v1/openpnp/part/{part_key} — OpenPnP-ready part-attributes projection.

First increment of the dubIS -> OpenPnP bridge (see
`docs/plans/2026-07-15-platform-architecture-design.md`, Phase 3). An OpenPnP-side Jython setup
script calls this endpoint to prefill `parts.xml`/`packages.xml` objects
(id, height, package-id, body dims, speed) instead of the operator entering
them by hand.

## Identity resolution

Resolves `part_key` through the SAME canonical-identity path as
`domain/part_registry.py` (the alias-index registry `inventory_ops.py`'s
`get_part_key` also consults) rather than inventing a second, parallel key
derivation. This repo has shipped exactly that class of bug before (PR #354:
`get_sourced_distributors`'s "loose match" key scope drifted from
`update_part_fields`'s strict `get_part_key`, causing distributor-PN rows to
resolve inconsistently) — so an alias distributor PN (e.g. an old LCSC PN
recorded before an MPN-precedence enrichment) here resolves to the exact same
part as its current canonical key, via the registry's `alias_index`, not a
bespoke lookup.

## Tier-1 / Tier-2 split

Chip passives (resistor/capacitor/inductor/LED/diode in a standard IPC chip
size) are looked up in the Tier-1 family table (`data/openpnp_families.json`,
keyed on `<part_type>_<size_code>`, derived from the part's `section`
(category) + `package` fields) and get `"tier": "standard"` with a full
package_id/body-dims/kicad_footprint projection. Anything that doesn't match
a family (ICs, connectors, oddball/non-chip parts) gets `"tier": "unmapped"`
with only what's directly knowable today (the raw `package` string as
`package_id`, height from spec if any) — Tier-2 (STEP-based package
generation) is a later increment, out of scope here.
"""

from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, Request

from domain import part_registry

router = APIRouter(prefix="/v1/openpnp", tags=["openpnp"])

# The Tier-1 family table is a static, repo-shared lookup asset (mirrors how
# InventoryApi loads data/constants.json — relative to THIS module's own
# directory, not `api.base_dir`, which tests routinely repoint at a tmp_path
# instance data dir that has no reason to carry a copy of it).
_FAMILIES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "openpnp_families.json",
)

# Category (the `section` field on an inventory item) -> Tier-1 family
# part_type key. Only categories that are ALWAYS standard chip passives are
# mapped here; everything else falls through to "unmapped".
_SECTION_TO_PART_TYPE = {
    "Passives - Resistors": "resistor",
    "Passives - Capacitors": "capacitor",
    "Passives - Inductors": "inductor",
    "LEDs": "led",
    "Diodes": "diode",
}

_STANDARD_SIZES = ("0201", "0402", "0603", "0805", "1206")
_SIZE_RE = re.compile("|".join(_STANDARD_SIZES))


def _load_families() -> dict:
    """Load the Tier-1 family table. Missing file -> empty dict (self-healing:
    every part falls through to tier:"unmapped" rather than the endpoint
    erroring, since the table is a lookup aid, not a required dependency)."""
    if not os.path.exists(_FAMILIES_PATH):
        return {}
    with open(_FAMILIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _size_code(package: str) -> str | None:
    m = _SIZE_RE.search((package or "").upper())
    return m.group(0) if m else None


def _identity_fields(item: dict) -> set[str]:
    ids = {item.get("lcsc"), item.get("mpn"), item.get("digikey"),
           item.get("pololu"), item.get("mouser")}
    ids.discard("")
    ids.discard(None)
    return ids


def _find_item(api, canonical: str, part_key: str) -> dict | None:
    """Find the inventory item whose own PN fields include the canonical key
    (or the raw part_key, for an unregistered/never-enriched part)."""
    for item in api._load_organized():
        ids = _identity_fields(item)
        if canonical in ids or part_key in ids:
            return item
    return None


@router.get("/part/{part_key}", operation_id="get_openpnp_part")
def get_openpnp_part(request: Request, part_key: str) -> dict:
    api = request.app.state.api
    registry = part_registry.load(api.base_dir)
    canonical = registry.alias_index.get(part_key, part_key)

    item = _find_item(api, canonical, part_key)
    if item is None:
        raise KeyError(f"Unknown part: {part_key}")

    canonical_id = (item.get("lcsc") or item.get("mpn") or item.get("digikey")
                     or item.get("pololu") or item.get("mouser") or canonical)
    name = item.get("mpn") or canonical_id

    spec = api.extract_spec(canonical_id) or {}
    # spec_extractor doesn't emit a height field today; this stays forward
    # compatible for when a part-specific height source lands (spec or a
    # future dedicated field) without changing the endpoint's response shape.
    spec_height_mm = spec.get("height_mm")

    families = _load_families()
    # `section` may be a flat category ("LEDs") or a hierarchical
    # "Category > Subcategory" string (e.g. "Passives - Resistors > Chip
    # Resistors") — only the top-level category determines part_type.
    top_section = (item.get("section") or "").split(" > ", 1)[0]
    part_type = _SECTION_TO_PART_TYPE.get(top_section)
    package_raw = (item.get("package") or "").strip()
    size_code = _size_code(package_raw)
    family = families.get(f"{part_type}_{size_code}") if part_type and size_code else None

    if family is not None:
        return {
            "id": canonical_id,
            "name": name,
            "height_mm": spec_height_mm if spec_height_mm is not None
            else family["default_height_mm"],
            "package_id": family["openpnp_package"],
            "package": {
                "body_width_mm": family["body_width_mm"],
                "body_height_mm": family["body_height_mm"],
            },
            "kicad_footprint": family["kicad_footprint"],
            "speed": 1.0,
            "tier": "standard",
        }

    return {
        "id": canonical_id,
        "name": name,
        "height_mm": spec_height_mm,
        "package_id": package_raw or None,
        "package": {"body_width_mm": None, "body_height_mm": None},
        "kicad_footprint": None,
        "speed": 1.0,
        "tier": "unmapped",
    }
