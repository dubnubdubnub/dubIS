"""Stable part identity — canonical key + distributor-PN alias registry.

The canonical key for a part is the FIRST key ever derived for it (via the
LCSC > MPN > DigiKey > Pololu > Mouser precedence).  When a part later gains
a higher-precedence PN (enrichment), that PN becomes an *alias* pointing at
the original canonical key instead of silently changing the part's identity —
which would orphan adjustments, price observations, group memberships, and
PnP/feeder references.

data/part_registry.json is the durable store:
    {"version": 1, "parts": {"<canonical>": ["<alias>", ...]}}
The file is additive and self-healing: if deleted, the next rebuild
re-registers every part from the ledger (identities revert to derived keys,
which is exactly today's behavior).
"""

from __future__ import annotations

import json
import os

import csv_io
from dubis_errors import PartRegistryCollisionError

_JSON_FILE = "part_registry.json"

# Ledger columns holding part numbers, in identity-precedence order.
PN_COLUMNS = (
    "LCSC Part Number",
    "Manufacture Part Number",
    "Digikey Part Number",
    "Pololu Part Number",
    "Mouser Part Number",
)


def derive_key(row: dict[str, str]) -> str:
    """Best unique identifier: LCSC (C-prefixed) > MPN > Digikey > Pololu > Mouser."""
    lcsc = (row.get("LCSC Part Number") or "").strip()
    if lcsc and lcsc.upper().startswith("C"):
        return lcsc
    for col in PN_COLUMNS[1:]:
        val = (row.get(col) or "").strip()
        if val:
            return val
    return ""


def _present_pns(row: dict[str, str]) -> list[str]:
    """All non-empty PNs in the row (a non-C LCSC value is not a usable PN)."""
    pns = []
    lcsc = (row.get("LCSC Part Number") or "").strip()
    if lcsc and lcsc.upper().startswith("C"):
        pns.append(lcsc)
    for col in PN_COLUMNS[1:]:
        val = (row.get(col) or "").strip()
        if val:
            pns.append(val)
    return pns


class PartRegistry:
    def __init__(self, parts: dict[str, list[str]] | None = None):
        self.parts: dict[str, list[str]] = parts or {}
        self.alias_index: dict[str, str] = {
            alias: canonical
            for canonical, aliases in self.parts.items()
            for alias in aliases
        }
        self.dirty = False


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def load(data_dir: str) -> PartRegistry:
    """Load the registry; missing file → empty registry (self-healing)."""
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return PartRegistry()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return PartRegistry(data.get("parts", {}))


def save(data_dir: str, registry: PartRegistry) -> None:
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps({"version": 1, "parts": registry.parts},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    registry.dirty = False


def canonical_for_row(registry: PartRegistry, row: dict[str, str]) -> str:
    """Canonical key for a row via alias lookup; "" if no PN is registered.

    Raises PartRegistryCollisionError if the row's PNs map to two different
    registered parts (data corruption must fail loudly, not warn-and-drop).
    """
    canonicals = {
        registry.alias_index[pn]
        for pn in _present_pns(row)
        if pn in registry.alias_index
    }
    if len(canonicals) > 1:
        raise PartRegistryCollisionError(
            f"Row part numbers {_present_pns(row)!r} map to multiple "
            f"registered parts: {sorted(canonicals)!r}"
        )
    return next(iter(canonicals)) if canonicals else ""


def register_row(registry: PartRegistry, row: dict[str, str]) -> str:
    """Resolve a row to its canonical key, registering new PNs as aliases.

    Returns "" for rows with no usable PN (matches derive_key behavior).
    """
    canonical = canonical_for_row(registry, row)
    if not canonical:
        canonical = derive_key(row)
        if not canonical:
            return ""
    aliases = registry.parts.setdefault(canonical, [])
    if canonical not in aliases:
        aliases.append(canonical)
        registry.alias_index[canonical] = canonical
        registry.dirty = True
    for pn in _present_pns(row):
        if pn not in registry.alias_index:
            aliases.append(pn)
            registry.alias_index[pn] = canonical
            registry.dirty = True
    return canonical
