"""Curation helpers shared by headless dubIS clients.

These are the parts of the retired tools/dubis-mcp that were NOT transport —
the things a raw `curl` caller gets wrong. Ported verbatim in behaviour, with
the module-global client replaced by an explicit ``client`` argument so they
are testable without discovery.

Four properties live here:

1. **Compact projections** (`compact_part`) — six fields, not the full
   14-field inventory record. Context economy for agent callers.
2. **Key derivation** (`derive_part_key`) — GET /v1/parts items carry no
   ``part_key`` field, so every caller that needs one derives it the same way.
3. **Canonical-key normalization** (`resolve_canonical_key`) — several /v1
   routes are canonical-key-strict: they key straight off the ``part_key``
   path/body value with no lookup of their own (POST /v1/parts/{k}/adjust,
   POST /v1/bom/consume, GET /v1/parts/{k}/prices, GET /v1/parts/{k}/history).
   Passing an alias PN (e.g. an MPN when the part's canonical key is its LCSC
   number) straight through would read or write against the alias, never the
   part — for adjustments that silently creates a disconnected row while
   reporting the canonical part's unchanged quantity back as the new one.
4. **The add/remove precheck** (`precheck_adjust`) — POST
   /v1/parts/{part_key}/adjust silently no-ops "add"/"remove" against a key
   with no existing ledger/adjustment row (the domain layer only materializes
   a new row for adj_type == "set"), so a bad key comes back as a misleading
   ``{"new_qty": None}`` rather than any error at all. "set" is deliberately
   exempt: creating new parts is what it is for.
"""

from __future__ import annotations

from typing import Any

from .v1client import V1Client

_PN_FIELDS = ("lcsc", "mpn", "digikey", "pololu", "mouser")

# adj_types that operate on an existing part and therefore need the precheck.
# "set" is absent on purpose — see property 4 in the module docstring.
_RELATIVE_ADJ_TYPES = frozenset({"add", "remove"})


class PartNotFoundError(Exception):
    """Raised when a part_key resolves to no inventory item.

    Message wording matches what tools/dubis-mcp used ("Part not found: <key>")
    so existing agent-facing behaviour is unchanged by the CLI migration.
    """

    def __init__(self, part_key: str):
        super().__init__(f"Part not found: {part_key}")
        self.part_key = part_key


def derive_part_key(item: dict[str, Any]) -> str:
    """Best unique identifier for an inventory item, mirroring
    domain/part_registry.derive_key's precedence: LCSC (C-prefixed) > MPN >
    Digikey > Pololu > Mouser."""
    lcsc = (item.get("lcsc") or "").strip()
    if lcsc and lcsc.upper().startswith("C"):
        return lcsc
    for field in _PN_FIELDS[1:]:
        val = (item.get(field) or "").strip()
        if val:
            return val
    return ""


def matches_part(item: dict[str, Any], part_key: str) -> bool:
    """True if *part_key* identifies *item* — either its derived key or any
    raw PN field matches exactly (loose match, so an alias PN still finds the
    part; `resolve_canonical_key` is what turns that back into the real key)."""
    if derive_part_key(item) == part_key:
        return True
    return any((item.get(f) or "") == part_key for f in _PN_FIELDS)


def compact_part(item: dict[str, Any]) -> dict[str, Any]:
    """The compact search/low-stock projection: part_key, description, qty,
    section, package, unit_price."""
    return {
        "part_key": derive_part_key(item),
        "description": item.get("description", ""),
        "qty": item.get("qty", 0),
        "section": item.get("section", ""),
        "package": item.get("package", ""),
        "unit_price": item.get("unit_price", 0.0),
    }


def fetch_inventory(client: V1Client) -> list[dict[str, Any]]:
    resp = client.get("/v1/parts")
    return resp.get("inventory", []) if isinstance(resp, dict) else resp


def find_part(client: V1Client, part_key: str) -> dict[str, Any] | None:
    for item in fetch_inventory(client):
        if matches_part(item, part_key):
            return item
    return None


def resolve_canonical_key(client: V1Client, part_key: str) -> tuple[dict[str, Any], str]:
    """Resolve *part_key* (canonical or alias PN) to its item and canonical key.

    Raises PartNotFoundError if it matches nothing. Callers hitting any
    canonical-key-strict route must go through this first — see property 3 in
    the module docstring.
    """
    item = find_part(client, part_key)
    if item is None:
        raise PartNotFoundError(part_key)
    return item, derive_part_key(item)


def precheck_adjust(client: V1Client, part_key: str, adj_type: str) -> str:
    """Validate an adjustment and return the canonical key to send to /v1.

    For "add"/"remove", raises PartNotFoundError when the part does not exist,
    rather than letting /v1 silently no-op. For "set", an unknown key is legal
    (it creates the part), so the key is passed through unchanged when it
    resolves to nothing and canonicalized when it does.
    """
    item = find_part(client, part_key)
    if item is None:
        if adj_type in _RELATIVE_ADJ_TYPES:
            raise PartNotFoundError(part_key)
        return part_key
    return derive_part_key(item)
