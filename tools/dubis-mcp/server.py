"""MCP dubis server — curated inventory tools over the /v1 API.

FastMCP stdio server, same layout/conventions as tools/dev-tools-mcp/server.py.
Tools call /v1 over HTTP via v1client.py's V1Client rather than talking to
InventoryApi directly — this process never touches the CSVs/SQLite cache
itself; the /v1 server (desktop app, or a spawned standalone instance) is the
single writer.

Task 1 shipped dubis_status(). Task 2 added the seven read tools. Task 3
(this revision) adds the two mutation tools (adjust_stock, consume_bom) and
USED_ROUTES/check_used_routes — the OpenAPI-snapshot contract guard exercised
by tests/python/test_dubis_mcp_contract.py
(see docs/plans/2026-07-16-phase2-mcp-server-plan.md).

Read tools return COMPACT projections only — trimmed fields + counts, never
the full 14-field inventory record — per the design doc's "never dump the
full inventory list" rule. Every projection derives a stable ``part_key``
via ``_derive_part_key`` (mirrors ``domain/part_registry.derive_key``'s
precedence: LCSC (C-prefixed) > MPN > Digikey > Pololu > Mouser) since
GET /v1/parts does not itself send a ``part_key`` field.

Mutation tools do not catch V1Error — a failing /v1 call (bad part_key,
validation error, etc.) propagates out of the tool function and FastMCP
turns it into the MCP tool call's error result, carrying the server's
message. This matches the read tools' style: only get_part translates a
"not found" case into a plain string because it's a lookup miss, not a
request error.

adjust_stock is a partial exception to "always let /v1 raise the error":
POST /v1/parts/{part_key}/adjust silently no-ops "add"/"remove" against a
part_key with no existing ledger/adjustment row (the domain layer only
materializes a brand-new row for adj_type == "set"), so a bad key would
otherwise come back as a misleading {"new_qty": None} instead of any error
at all. adjust_stock pre-checks existence via _find_part for "add"/"remove"
and raises ValueError("Part not found: <key>") itself, before ever calling
/v1, giving the same "Part not found" wording get_part uses. "set" is left
alone — it intentionally creates new parts, so no precheck applies to it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from v1client import V1Client, connect

mcp = FastMCP("dubis")

# tools/dubis-mcp/server.py -> repo root is two levels up.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_client: V1Client | None = None


def _get_client() -> V1Client:
    """Lazily discover and cache the /v1 client for this process's lifetime.

    Lazy (not at import time) so importing this module — e.g. from tests —
    never triggers discovery/spawn as a side effect.
    """
    global _client
    if _client is None:
        _client = connect(str(REPO_ROOT))
    return _client


# ── Shared helpers ───────────────────────────────────────────────────────────

_PN_FIELDS = ("lcsc", "mpn", "digikey", "pololu", "mouser")


def _derive_part_key(item: dict[str, Any]) -> str:
    """Best unique identifier for an inventory item, mirroring
    domain/part_registry.derive_key's precedence: LCSC (C-prefixed) > MPN >
    Digikey > Pololu > Mouser. GET /v1/parts items don't carry a part_key
    field, so every tool that needs one derives it the same way."""
    lcsc = (item.get("lcsc") or "").strip()
    if lcsc and lcsc.upper().startswith("C"):
        return lcsc
    for field in _PN_FIELDS[1:]:
        val = (item.get(field) or "").strip()
        if val:
            return val
    return ""


def _matches_part(item: dict[str, Any], part_key: str) -> bool:
    """True if *part_key* identifies *item* — either its derived key or any
    raw PN field matches exactly (loose match, mirroring get_sourced_distributors'
    style rather than the strict single-column match some mutation routes use)."""
    if _derive_part_key(item) == part_key:
        return True
    return any((item.get(f) or "") == part_key for f in _PN_FIELDS)


def _fetch_inventory() -> list[dict[str, Any]]:
    resp = _get_client().get("/v1/parts")
    return resp.get("inventory", []) if isinstance(resp, dict) else resp


def _compact_part(item: dict[str, Any]) -> dict[str, Any]:
    """The compact search/low-stock projection: part_key, description, qty,
    section, package, unit_price — exactly the design table's field list."""
    return {
        "part_key": _derive_part_key(item),
        "description": item.get("description", ""),
        "qty": item.get("qty", 0),
        "section": item.get("section", ""),
        "package": item.get("package", ""),
        "unit_price": item.get("unit_price", 0.0),
    }


def _find_part(part_key: str) -> dict[str, Any] | None:
    for item in _fetch_inventory():
        if _matches_part(item, part_key):
            return item
    return None


@mcp.tool()
def dubis_status() -> dict:
    """Report which /v1 server this MCP session is talking to.

    Returns:
        {server, discovered_via, schema_version, part_count} — discovered_via
        is one of "env", "port_file", "spawned" (see v1client.connect()'s
        discovery order).
    """
    client = _get_client()
    client.get("/v1/health")
    meta = client.get("/v1/meta")
    # GET /v1/meta (server/routes/meta.py) carries only schema_version and
    # section orders, no part count — this phase makes no server changes, so
    # part_count is derived from a full GET /v1/parts fetch instead of a
    # dedicated cheap count endpoint.
    parts = client.get("/v1/parts")
    part_count = len(parts.get("inventory", [])) if isinstance(parts, dict) else len(parts)
    return {
        "server": client.base_url,
        "discovered_via": client.discovered_via,
        "schema_version": meta.get("schema_version"),
        "part_count": part_count,
    }


@mcp.tool()
def search_parts(query: str = "", section: str = "", max_results: int = 25) -> dict:
    """Search inventory by a case-insensitive substring over lcsc/mpn/
    description/manufacturer/package, optionally scoped to one section.

    Args:
        query: substring to match (case-insensitive); empty matches everything.
        section: exact section name to restrict to (case-insensitive); empty means all sections.
        max_results: cap on the number of compact projections returned.

    Returns:
        {matches: [{part_key, description, qty, section, package, unit_price}, ...],
         total_count: total matches before the max_results cap,
         returned: len(matches)}
    """
    q = query.lower()
    sec = section.lower()
    hits = []
    for item in _fetch_inventory():
        if sec and (item.get("section") or "").lower() != sec:
            continue
        if q:
            haystack = " ".join(
                str(item.get(f, "")) for f in ("lcsc", "mpn", "description", "manufacturer", "package")
            ).lower()
            if q not in haystack:
                continue
        hits.append(item)
    matches = [_compact_part(item) for item in hits[:max_results]]
    return {"matches": matches, "total_count": len(hits), "returned": len(matches)}


@mcp.tool()
def get_part(part_key: str) -> dict | str:
    """Aggregated detail card for one part: inventory fields + prices +
    purchase-history + generic groups + the last 5 history entries.

    Args:
        part_key: LCSC/MPN/Digikey/Pololu/Mouser PN, or the derived part_key
            search_parts/low_stock returned.

    Returns:
        A detail dict, or a plain error string if part_key matches nothing.
    """
    item = _find_part(part_key)
    if item is None:
        return f"Part not found: {part_key}"

    key = _derive_part_key(item)
    client = _get_client()
    prices = client.get(f"/v1/parts/{key}/prices")
    purchase_history = client.get(f"/v1/parts/{key}/purchase-history")
    groups = client.get(f"/v1/parts/{key}/groups")
    history = client.get(f"/v1/parts/{key}/history")

    return {
        "part_key": key,
        "description": item.get("description", ""),
        "qty": item.get("qty", 0),
        "section": item.get("section", ""),
        "package": item.get("package", ""),
        "manufacturer": item.get("manufacturer", ""),
        "unit_price": item.get("unit_price", 0.0),
        "ext_price": item.get("ext_price", 0.0),
        "primary_vendor_id": item.get("primary_vendor_id", ""),
        "po_history": item.get("po_history", []),
        "prices": prices,
        "has_purchase_history": purchase_history.get("has_purchase_history", False),
        "groups": groups.get("groups", []),
        "recent_history": history[:5],
    }


@mcp.tool()
def spec_search(part_type: str, value: float | str, package: str = "") -> dict:
    """Find the generic group (and its best in-stock member) matching a BOM
    spec. Accepts either a numeric value in base units (e.g. 1e-7 farads) or
    a display string (e.g. "100nF") — non-numeric values are routed through
    POST /v1/spec/extract first to parse out the numeric value.

    Args:
        part_type: e.g. "capacitor", "resistor", "inductor".
        value: numeric value in base units, OR a display string like "100nF".
        package: e.g. "0402"; empty matches any package.

    Returns:
        {match: {generic_part_id, generic_name, best_part_id, members}} on a
        hit, or {match: None} on a miss.
    """
    numeric_value: float
    if isinstance(value, (int, float)):
        numeric_value = float(value)
    else:
        try:
            numeric_value = float(value)
        except ValueError:
            extracted = _get_client().post(
                "/v1/spec/extract",
                json={"part_type": part_type, "value_str": str(value), "package_str": package},
            )
            spec = extracted.get("spec", extracted) if isinstance(extracted, dict) else extracted
            if not isinstance(spec, dict) or "value" not in spec:
                return {"match": None}
            numeric_value = float(spec["value"])

    return _get_client().post(
        "/v1/bom/resolve-spec",
        json={"part_type": part_type, "value": numeric_value, "package": package},
    )


@mcp.tool()
def low_stock(threshold: int | None = None) -> dict:
    """Parts at or below a stock threshold.

    Args:
        threshold: quantity ceiling to flag; when omitted, uses each part's
            own per-section threshold from GET /v1/preferences (sections with
            no configured threshold default to 0, i.e. only zero-stock parts).

    Returns:
        {parts: [{part_key, description, qty, section, package, unit_price, threshold}, ...],
         count: len(parts)}
    """
    prefs = _get_client().get("/v1/preferences")
    thresholds = prefs.get("thresholds", {}) if isinstance(prefs, dict) else {}

    flagged = []
    for item in _fetch_inventory():
        section = item.get("section", "")
        effective = threshold if threshold is not None else thresholds.get(section, 0)
        qty = item.get("qty", 0)
        if qty <= effective:
            compact = _compact_part(item)
            compact["threshold"] = effective
            flagged.append(compact)

    return {"parts": flagged, "count": len(flagged)}


@mcp.tool()
def price_summary(part_key: str) -> dict:
    """Per-distributor price aggregates plus the last purchase-order quantity.

    Args:
        part_key: LCSC/MPN/Digikey/Pololu/Mouser PN, or a derived part_key.

    Returns:
        {part_key, distributors: {distributor: {latest_unit_price, avg_unit_price,
         price_count, last_observed, moq, source}, ...}, last_po_quantity}
    """
    client = _get_client()
    prices = client.get(f"/v1/parts/{part_key}/prices")
    last_po = client.get(f"/v1/parts/{part_key}/last-po-quantity")
    return {
        "part_key": part_key,
        "distributors": prices,
        "last_po_quantity": last_po.get("quantity") if isinstance(last_po, dict) else None,
    }


@mcp.tool()
def part_history(part_key: str, limit: int = 10) -> dict:
    """Adjustment log for one part, most recent entries first up to *limit*.

    Args:
        part_key: LCSC/MPN/Digikey/Pololu/Mouser PN, or a derived part_key.
        limit: max entries to return.

    Returns:
        {part_key, history: [{timestamp, kind, qty_delta, source, note}, ...]}
    """
    entries = _get_client().get(f"/v1/parts/{part_key}/history")
    return {"part_key": part_key, "history": entries[:limit]}


@mcp.tool()
def list_generic_parts(part_type: str = "") -> dict:
    """Generic-part groups with member counts and each group's best-stock
    member, optionally filtered to one part_type.

    Args:
        part_type: e.g. "capacitor"; empty returns all groups.

    Returns:
        {groups: [{generic_part_id, name, part_type, member_count,
         best_member: {part_id, quantity, preferred} | None}, ...]}
    """
    groups = _get_client().get("/v1/generic-parts")
    result = []
    for gp in groups:
        if part_type and gp.get("part_type") != part_type:
            continue
        members = gp.get("members", [])
        best = None
        for m in members:
            if best is None or (m.get("preferred"), m.get("quantity", 0)) > (
                best.get("preferred"), best.get("quantity", 0),
            ):
                best = m
        result.append({
            "generic_part_id": gp.get("generic_part_id"),
            "name": gp.get("name"),
            "part_type": gp.get("part_type"),
            "member_count": len(members),
            "best_member": (
                {
                    "part_id": best.get("part_id"),
                    "quantity": best.get("quantity"),
                    "preferred": best.get("preferred"),
                }
                if best is not None
                else None
            ),
        })
    return {"groups": result}


@mcp.tool()
def adjust_stock(part_key: str, adj_type: str, quantity: int, note: str = "") -> dict:
    """Apply a stock adjustment to one part and report its resulting quantity.

    Args:
        part_key: LCSC/MPN/Digikey/Pololu/Mouser PN, or a derived part_key.
        adj_type: one of "set" (absolute new quantity — may CREATE a brand-new
            part_key that has no prior ledger/adjustment row), "add"
            (increase an EXISTING part's quantity by quantity), "remove"
            (decrease an EXISTING part's quantity by quantity). "add" and
            "remove" require part_key to already exist.
        quantity: non-negative integer; interpretation depends on adj_type.
        note: optional free-text note recorded on the adjustment.

    Returns:
        {part_key, new_qty} — new_qty comes from refetching the part after
        the adjustment. The adjustment is recorded with source="mcp"
        (visible afterward via part_history).

    Raises:
        ValueError: adj_type is "add" or "remove" and part_key matches no
            existing part (the message reads "Part not found: <key>", same
            wording get_part uses for a lookup miss) — /v1 itself would
            otherwise silently no-op the request rather than error.
    """
    if adj_type in ("add", "remove") and _find_part(part_key) is None:
        raise ValueError(f"Part not found: {part_key}")
    _get_client().post(
        f"/v1/parts/{part_key}/adjust",
        json={"adj_type": adj_type, "quantity": quantity, "note": note, "source": "mcp"},
    )
    item = _find_part(part_key)
    return {"part_key": part_key, "new_qty": item.get("qty", 0) if item is not None else None}


@mcp.tool()
def consume_bom(matches: list[dict], board_qty: int = 1, bom_name: str = "mcp-bom") -> dict:
    """Consume a batch of BOM part matches against inventory as one operation.

    Args:
        matches: list of ``{"part_key": str, "bom_qty": int}`` dicts — this is
            the minimal shape POST /v1/bom/consume's ConsumeBomBody accepts
            (see server/routes/inventory_mut.py's ConsumeBomBody /
            domain/inventory.py's consume_bom): ``bom_qty`` is how many units
            of that part one board uses; the server multiplies it by
            board_qty before subtracting from stock. Extra keys are ignored.
        board_qty: number of boards being built.
        bom_name: label recorded on the adjustment (e.g. a BOM file name).

    Returns:
        {bom_name, board_qty, consumed: matches} echoing what was submitted.
        The adjustment is recorded with source="mcp".
    """
    _get_client().post(
        "/v1/bom/consume",
        json={
            "matches": matches,
            "board_qty": board_qty,
            "bom_name": bom_name,
            "note": "",
            "source": "mcp",
        },
    )
    return {"bom_name": bom_name, "board_qty": board_qty, "consumed": matches}


# ── OpenAPI-snapshot contract guard ──────────────────────────────────────────
#
# Every (verb, path-template, body-field-names) tuple this module actually
# sends over /v1, declared once here so tests/python/test_dubis_mcp_contract.py
# can assert each one still exists in docs/openapi-v1.json (path + verb) and
# that every body field name is a real property of that operation's request
# body schema. Path templates use the snapshot's `{param}` style. GET routes
# and read-only POSTs with no body fields to pin use an empty tuple.
UsedRoute = tuple[str, str, tuple[str, ...]]

USED_ROUTES: list[UsedRoute] = [
    ("get", "/v1/health", ()),
    ("get", "/v1/meta", ()),
    ("get", "/v1/parts", ()),
    ("get", "/v1/parts/{part_key}/prices", ()),
    ("get", "/v1/parts/{part_key}/purchase-history", ()),
    ("get", "/v1/parts/{part_key}/groups", ()),
    ("get", "/v1/parts/{part_key}/history", ()),
    ("get", "/v1/parts/{part_key}/last-po-quantity", ()),
    ("get", "/v1/preferences", ()),
    ("get", "/v1/generic-parts", ()),
    ("post", "/v1/spec/extract", ("part_type", "value_str", "package_str")),
    ("post", "/v1/bom/resolve-spec", ("part_type", "value", "package")),
    ("post", "/v1/parts/{part_key}/adjust", ("adj_type", "quantity", "note", "source")),
    ("post", "/v1/bom/consume", ("matches", "board_qty", "bom_name", "note", "source")),
]


def _request_body_fields(openapi: dict, verb: str, path: str) -> set[str] | None:
    """Property names of the named operation's JSON request-body schema, or
    None if that operation has no requestBody at all (resolves a single
    top-level $ref into components.schemas, which is all this snapshot uses)."""
    operation = openapi["paths"][path][verb]
    body = operation.get("requestBody")
    if body is None:
        return None
    schema = body["content"]["application/json"]["schema"]
    ref = schema.get("$ref")
    if ref:
        schema = openapi["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    return set(schema.get("properties", {}).keys())


def check_used_routes(routes: list[UsedRoute], openapi: dict) -> None:
    """Raise AssertionError if any (verb, path) in *routes* is missing from
    *openapi*'s paths, or if any declared body field isn't a real property of
    that operation's request-body schema. Pure function of its arguments —
    tests call it both with USED_ROUTES (must pass) and with a fabricated
    entry appended (must raise), so the guard is proven to actually guard."""
    paths = openapi.get("paths", {})
    for verb, path, body_fields in routes:
        methods = paths.get(path)
        if methods is None or verb not in methods:
            raise AssertionError(f"route not in OpenAPI snapshot: {verb.upper()} {path}")
        if not body_fields:
            continue
        allowed = _request_body_fields(openapi, verb, path)
        if allowed is None:
            raise AssertionError(
                f"{verb.upper()} {path} is used with body fields {body_fields} "
                "but the snapshot's operation has no requestBody"
            )
        extra = set(body_fields) - allowed
        if extra:
            raise AssertionError(
                f"{verb.upper()} {path} sends body field(s) {sorted(extra)} not in "
                f"its OpenAPI schema (allowed: {sorted(allowed)})"
            )


if __name__ == "__main__":
    mcp.run()
