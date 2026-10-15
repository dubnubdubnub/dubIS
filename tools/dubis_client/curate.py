"""Curation helpers shared by headless dubIS clients.

These are the parts of the retired tools/dubis-mcp that were NOT transport —
the things a raw `curl` caller gets wrong. Ported verbatim in behaviour, with
the module-global client replaced by an explicit ``client`` argument so they
are testable without discovery.

Four properties live here:

1. **Compact projections** (`compact_part`) — seven fields, not the full
   14-field inventory record. Context economy for agent callers. One of the
   seven is `part_numbers`, the whole distributor-PN set, because a projection
   that carries only the derived `part_key` hides the numbers a caller is most
   likely to need (`part_numbers`' docstring).
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


def part_numbers(item: dict[str, Any]) -> dict[str, str]:
    """Every distributor part number a record carries, empty ones dropped.

    A part's identity in dubIS is this *set*, not any one member of it:
    `derive_part_key` returns the first non-empty field in precedence order,
    so a part keyed by its LCSC number hides its DigiKey, Mouser and Pololu
    numbers from anything that only carries `part_key`. Those hidden numbers are
    the ones a person actually arrives with — off a reel label, a BOM, or the
    other dubIS server they are moving the part to — and they are frequently
    found nowhere else in the record. A DigiKey number embeds that vendor's own
    revision code: `664-12401951E412ACT-ND` says `E412` where the MPN beside it,
    `12401951F412A`, says `F412`, so the string a person is holding matches no
    other field of the row.

    Empty keys are dropped rather than spelled `""`: "is this part sourced at
    Mouser" becomes a membership test, and a search result line does not pay
    for three empty strings per row.
    """
    numbers: dict[str, str] = {}
    for field in _PN_FIELDS:
        value = (item.get(field) or "").strip()
        if value:
            numbers[field] = value
    return numbers


def matches_part_number(item: dict[str, Any], needle: str) -> bool:
    """True when *needle* is a case-insensitive substring of any PN field.

    The substring form is what `search` needs: a person types what is printed
    on the reel, which is a vendor number with or without its `-ND` suffix, and
    often a fragment of one. `matches_part` is the exact form, for a caller
    that already holds a whole key.
    """
    probe = (needle or "").lower()
    return bool(probe) and any(probe in v.lower() for v in part_numbers(item).values())


def exact_part_number_match(item: dict[str, Any], query: str) -> bool:
    """True when *query* IS one of this item's part numbers, whole.

    The distinction `search` ranks on: a whole-field hit means the caller
    pasted an identifier, which names one part; a row whose prose happens to
    contain the same characters is a weaker answer to the same keystrokes.
    """
    probe = (query or "").strip().lower()
    return bool(probe) and any(probe == v.lower() for v in part_numbers(item).values())


def compact_part(item: dict[str, Any]) -> dict[str, Any]:
    """The compact search/low-stock projection: part_key, the distributor part
    numbers behind it, description, qty, section, package, unit_price."""
    return {
        "part_key": derive_part_key(item),
        "part_numbers": part_numbers(item),
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
