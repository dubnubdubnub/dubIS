"""Hand-written hot-path commands, ported from the retired MCP tools.

These are the ten tools `tools/dubis-mcp` exposed, and they are hand-written
rather than generated for a reason worth stating: they are not aliases for
single /v1 routes. `get` aggregates five calls, `search` filters client-side
(no /v1 search route exists), `low-stock` reads a preferences threshold per
section, and `spec-search` routes a display string through /v1/spec/extract
before resolving. A generator over the OpenAPI spec cannot produce any of
them — the spec describes routes, and these are compositions.

Two of the original ten are absent because the generated table already covers
them exactly: `adjust_stock` is `dubis parts adjust` (with the same precheck,
via _PRECHECKS) and `consume_bom` is `dubis bom consume`.

Behaviour change from the MCP versions: a part_key that matches nothing raises
PartNotFoundError (exit 3) instead of returning the string "Part not found:
<key>" as a successful result. A tool result is read by a model that can
notice prose; a CLI's caller reads an exit code, and a miss reported as
success is a silent failure.
"""

from __future__ import annotations

from typing import Any

from tools.dubis_client import (
    PartNotFoundError,
    V1Client,
    compact_part,
    fetch_inventory,
    resolve_canonical_key,
)

_SEARCH_FIELDS = ("lcsc", "mpn", "description", "manufacturer", "package")


# ── implementations ──────────────────────────────────────────────────────────


def _status(client: V1Client, args) -> dict:
    client.get("/v1/health")
    meta = client.get("/v1/meta")
    # GET /v1/meta carries schema_version and section orders but no part
    # count, so this derives one from a full fetch rather than a cheap count
    # endpoint that does not exist.
    return {
        "server": client.base_url,
        "discovered_via": client.discovered_via,
        "schema_version": meta.get("schema_version"),
        "part_count": len(fetch_inventory(client)),
    }


def _search(client: V1Client, args) -> dict:
    query = (args.query or "").lower()
    section = (args.section or "").lower()
    hits = []
    for item in fetch_inventory(client):
        if section and (item.get("section") or "").lower() != section:
            continue
        if query:
            haystack = " ".join(str(item.get(f, "")) for f in _SEARCH_FIELDS).lower()
            if query not in haystack:
                continue
        hits.append(item)
    matches = [compact_part(item) for item in hits[: args.max_results]]
    return {"matches": matches, "total_count": len(hits), "returned": len(matches)}


def _get(client: V1Client, args) -> dict:
    item, key = resolve_canonical_key(client, args.part_key)
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


def _spec_search(client: V1Client, args) -> dict:
    """`--value` may be numeric (base units, e.g. 1e-7 farads) or a display
    string like "100nF"; the latter goes through /v1/spec/extract first."""
    raw = args.value
    try:
        numeric = float(raw)
    except ValueError:
        extracted = client.post("/v1/spec/extract", {
            "part_type": args.part_type,
            "value_str": str(raw),
            "package_str": args.package or "",
        })
        spec = extracted.get("spec", extracted) if isinstance(extracted, dict) else extracted
        if not isinstance(spec, dict) or "value" not in spec:
            return {"match": None}
        numeric = float(spec["value"])

    return client.post("/v1/bom/resolve-spec", {
        "part_type": args.part_type,
        "value": numeric,
        "package": args.package or "",
    })


def _low_stock(client: V1Client, args) -> dict:
    """With no --threshold, each part is judged against its own section's
    threshold from /v1/preferences.

    That lookup is flat and keyed on the exact section string: unlike the
    desktop UI, a compound section like "Parent > Sub" does NOT inherit
    "Parent"'s threshold — only an exact entry applies, and anything
    unconfigured defaults to 0 (zero-stock parts only).
    """
    prefs = client.get("/v1/preferences")
    thresholds = prefs.get("thresholds", {}) if isinstance(prefs, dict) else {}

    flagged = []
    for item in fetch_inventory(client):
        effective = (
            args.threshold if args.threshold is not None
            else thresholds.get(item.get("section", ""), 0)
        )
        if item.get("qty", 0) <= effective:
            compact = compact_part(item)
            compact["threshold"] = effective
            flagged.append(compact)
    return {"parts": flagged, "count": len(flagged)}


def _prices(client: V1Client, args) -> dict:
    _, key = resolve_canonical_key(client, args.part_key)
    prices = client.get(f"/v1/parts/{key}/prices")
    last_po = client.get(f"/v1/parts/{key}/last-po-quantity")
    return {
        "part_key": key,
        "distributors": prices,
        "last_po_quantity": last_po.get("quantity") if isinstance(last_po, dict) else None,
    }


def _history(client: V1Client, args) -> dict:
    _, key = resolve_canonical_key(client, args.part_key)
    entries = client.get(f"/v1/parts/{key}/history")
    return {"part_key": key, "history": entries[: args.limit]}


def _generic_groups(client: V1Client, args) -> dict:
    groups = client.get("/v1/generic-parts")
    result = []
    for group in groups:
        if args.part_type and group.get("part_type") != args.part_type:
            continue
        members = group.get("members", [])
        best = None
        for member in members:
            if best is None or (member.get("preferred"), member.get("quantity", 0)) > (
                best.get("preferred"), best.get("quantity", 0),
            ):
                best = member
        result.append({
            "generic_part_id": group.get("generic_part_id"),
            "name": group.get("name"),
            "part_type": group.get("part_type"),
            "member_count": len(members),
            "best_member": None if best is None else {
                "part_id": best.get("part_id"),
                "quantity": best.get("quantity"),
                "preferred": best.get("preferred"),
            },
        })
    return {"groups": result}


# ── argparse wiring ──────────────────────────────────────────────────────────


def _args_search(parser):
    parser.add_argument("query", nargs="?", default="",
                        help="case-insensitive substring; empty matches everything")
    parser.add_argument("--section", default="", help="restrict to one exact section")
    parser.add_argument("--max-results", dest="max_results", type=int, default=25)


def _args_part_key(parser):
    parser.add_argument("part_key", help="LCSC/MPN/Digikey/Pololu/Mouser PN, or a derived part_key")


def _args_spec_search(parser):
    parser.add_argument("part_type", help='e.g. "capacitor", "resistor"')
    parser.add_argument("value", help='base units (1e-7) or a display string ("100nF")')
    parser.add_argument("--package", default="", help='e.g. "0402"; empty matches any')


def _args_low_stock(parser):
    parser.add_argument("--threshold", type=int, default=None,
                        help="quantity ceiling; omit to use per-section thresholds")


def _args_history(parser):
    _args_part_key(parser)
    parser.add_argument("--limit", type=int, default=10)


def _args_generic_groups(parser):
    parser.add_argument("--part-type", dest="part_type", default="")


CURATED: dict[str, dict[str, Any]] = {
    "status": {
        "help": "which /v1 server this session talks to, and its part count",
        "add_args": lambda p: None,
        "run": _status,
    },
    "search": {
        "help": "substring search over inventory, compact projection",
        "add_args": _args_search,
        "run": _search,
    },
    "get": {
        "help": "aggregated detail card for one part",
        "add_args": _args_part_key,
        "run": _get,
    },
    "spec-search": {
        "help": "find the generic group matching a BOM spec",
        "add_args": _args_spec_search,
        "run": _spec_search,
    },
    "low-stock": {
        "help": "parts at or below a stock threshold",
        "add_args": _args_low_stock,
        "run": _low_stock,
    },
    "prices": {
        "help": "per-distributor price aggregates + last PO quantity",
        "add_args": _args_part_key,
        "run": _prices,
    },
    "history": {
        "help": "adjustment log for one part",
        "add_args": _args_history,
        "run": _history,
    },
    "generic-groups": {
        "help": "generic-part groups with member counts and best member",
        "add_args": _args_generic_groups,
        "run": _generic_groups,
    },
}

__all__ = ["CURATED", "PartNotFoundError"]
