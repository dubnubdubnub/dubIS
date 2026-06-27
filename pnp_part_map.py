"""Part-map helpers for OpenPnP part ID resolution.

Loads ``data/pnp_part_map.json`` (explicit mappings from OpenPnP part IDs to
dubIS keys) and resolves an OpenPnP part ID to a dubIS inventory key via:
1. Explicit mapping in pnp_part_map.json
2. Direct match against inventory LCSC/MPN/Digikey fields
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def _load_part_map(base_dir):
    """Load pnp_part_map.json from data directory."""
    path = os.path.join(base_dir, "pnp_part_map.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _resolve_part_id(part_id, part_map, inventory):
    """Resolve an OpenPnP part ID to a dubIS part key.

    Strategy:
    1. Check pnp_part_map.json for explicit mapping
    2. Try direct match against inventory LCSC/MPN/Digikey keys
    3. Return None if unresolved
    """
    # 1. Explicit mapping
    if part_id in part_map:
        return part_map[part_id]

    # 2. Direct match against inventory keys
    for item in inventory:
        if part_id in (item.get("lcsc"), item.get("mpn"), item.get("digikey")):
            return item.get("lcsc") or item.get("mpn") or item.get("digikey")

    return None
