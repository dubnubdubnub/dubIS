"""Hand-written hot-path commands, ported from the retired MCP tools.

These are the ten tools `tools/dubis-mcp` exposed, and they are hand-written
rather than generated for a reason worth stating: they are not aliases for
single /v1 routes. `get` aggregates five calls, `search` filters client-side
(no /v1 search route exists), `low-stock` resolves a per-section threshold
across /v1/preferences and /v1/meta, and `spec-search` routes a display string
through /v1/spec/extract
before resolving. A generator over the OpenAPI spec cannot produce any of
them — the spec describes routes, and these are compositions.

Two of the original ten are absent because the generated table already covers
them exactly: `adjust_stock` is `dubis parts adjust` (with the same precheck,
via _PRECHECKS) and `consume_bom` is `dubis bom consume`.

`jlc` is the one command here that was never an MCP tool: it is a composition
over two unrelated JLCPCB routes ("which accounts are paired" and "that
account's library"), and it projects the inventory-shaped library records down
to the five fields a library listing is about.

Behaviour change from the MCP versions: a part_key that matches nothing raises
PartNotFoundError (exit 3) instead of returning the string "Part not found:
<key>" as a successful result. A tool result is read by a model that can
notice prose; a CLI's caller reads an exit code, and a miss reported as
success is a silent failure.
"""

from __future__ import annotations

from typing import Any

from domain.category_match import matches_category_name
from domain.thresholds import PRESET_SHIPPED, resolve, stored_preset_id
from tools.dubis_client import (
    PartNotFoundError,
    V1Client,
    compact_part,
    exact_part_number_match,
    fetch_inventory,
    matches_part_number,
    part_numbers,
    resolve_canonical_key,
)

# The prose fields `search` matches. The distributor part numbers are matched
# too, through `matches_part_number` rather than a second field list here, so
# the fields a caller can SEARCH and the fields it can SEE (compact_part's
# `part_numbers`) are the same tuple in tools/dubis_client/curate.py and cannot
# drift apart again — which is exactly how the DigiKey column went invisible
# twice over.
_TEXT_SEARCH_FIELDS = ("description", "manufacturer", "package")


# ── implementations ──────────────────────────────────────────────────────────


def _status(client: V1Client, args) -> dict:
    client.get("/v1/health")
    meta = client.get("/v1/meta")
    # GET /v1/meta carries schema_version and section orders but no part
    # count, so this derives one from a full fetch rather than a cheap count
    # endpoint that does not exist.
    return {
        # For a Unix-socket server the base_url is only a Host header, so the
        # socket path is the honest answer to "which server am I talking to".
        "server": client.uds or client.base_url,
        "discovered_via": client.discovered_via,
        "source": _answering_source(client),
        "schema_version": meta.get("schema_version"),
        "part_count": len(fetch_inventory(client)),
    }


def _answering_source(client: V1Client) -> dict:
    """Which configured server source the hub served this session from.

    The hub echoes no "served by" header (server/dispatch.py and
    server/proxy.py forward the upstream's headers and add none), so this is
    derived rather than echoed — but it is not a guess. With `--server` /
    `DUBIS_SERVER` the hub either serves exactly the resolved selector or
    rejects the request (an unknown id is its 404), and the CLI already
    resolved it against the roster. With neither, a request carrying no
    `X-Dubis-Source` is served from the roster's `default`, read here from the
    same `GET /v1/sources` the hub resolves it from.
    """
    selection = client.source
    if selection is not None:
        return {
            "selector": selection.selector,
            "names": [name for _, name in selection.names],
            "requested": selection.requested,
            "via": selection.via,
        }
    roster = client.get("/v1/sources")
    default = roster.get("default", "")
    names = {s.get("id"): s.get("name") for s in roster.get("sources", [])}
    names.setdefault("local", "Local")
    ids = [part.strip() for part in default.split(",") if part.strip()]
    return {
        "selector": default,
        "names": [names.get(i, i) for i in ids],
        "requested": None,
        "via": "hub-default",
    }


def _search(client: V1Client, args) -> dict:
    query = (args.query or "").lower()
    section = (args.section or "").lower()
    hits = []
    for item in fetch_inventory(client):
        if section and (item.get("section") or "").lower() != section:
            continue
        if query:
            # `--category-hit` mirrors the app's Preferences toggle: a term that
            # NAMES this row's category keeps the row even when no field of it
            # contains the term. Explicit rather than default-on because the CLI
            # has no preferences file — there is no stored toggle to read, and
            # silently defaulting on would make `dubis search diodes` disagree
            # with `dubis search diodes` on a laptop that turned it off. The
            # rule itself is shared with the frontend (domain/category_match.py).
            category_hit = bool(args.category_hit) and matches_category_name(
                query, item.get("section")
            )
            if not category_hit:
                # Matched through the shared helper, not a second field list
                # here — see _TEXT_SEARCH_FIELDS.
                if not matches_part_number(item, query):
                    haystack = " ".join(
                        str(item.get(f, "")) for f in _TEXT_SEARCH_FIELDS
                    ).lower()
                    if query not in haystack:
                        continue
        hits.append(item)
    if query:
        # A query that IS a part number names one part, so that row goes first:
        # it is the answer, and it now survives `--max-results` truncation
        # however far back the inventory sorts it. Partial hits are NOT
        # promoted — "104" inside an MPN is no stronger a signal than "100nF"
        # inside a description, and promoting it would reorder results for
        # ordinary queries on no evidence the caller wanted an identifier.
        # list.sort is stable, so everything else keeps the inventory's own
        # order and a descriptive query returns exactly what it returned before.
        hits.sort(key=lambda item: 0 if exact_part_number_match(item, query) else 1)
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
        # Every distributor number the record carries, not just the one the
        # canonical key happens to be: this card is what an agent reads before
        # it moves a part to another server or orders it, and both of those
        # need the DigiKey/Mouser numbers that `part_key` cannot show.
        "part_numbers": part_numbers(item),
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
    threshold: the caller's override from /v1/preferences, then the chosen
    preset bundle from /v1/meta, then the server's ACTIVE threshold snapshot
    from /v1/meta, then the server's shipped default (snapshot 0), then
    derivation, then that server's base.

    That is the order the inventory grid colours with — js/threshold-logic.js
    is the frontend mirror of domain/thresholds.py, whose resolve() is what
    runs here — and a compound "Parent > Sub" section inherits its parent's
    entry exactly as the desktop does.

    The CLI has no client profile of its own: it never sends overrides it
    collected itself, and the server's active snapshot is always the server
    layer it resolves against. An older server names no active profile, and
    "no server snapshot" is the honest reading of that — the pre-snapshot
    order, not a guess.

    It used to stop at the preferences and fall through to 0, so a section
    nobody had moved the slider for flagged only zero-stock parts while the
    desktop called the same part low. The two defaults were the bug; one
    resolution order served by the server is the fix.
    """
    prefs = client.get("/v1/preferences")
    overrides = prefs.get("thresholds", {}) if isinstance(prefs, dict) else {}
    # The preset id is STORED state, so it takes the lenient read: a stale or
    # hand-typo'd id degrades to the shipped order rather than failing the
    # command (domain.thresholds.stored_preset_id).
    preset_id = stored_preset_id(prefs.get("threshold_preset") if isinstance(prefs, dict) else None)
    meta = client.get("/v1/meta")
    defaults = meta.get("default_thresholds", {}) if isinstance(meta, dict) else {}
    # An older server answers neither key. Its base is then unknown, and the
    # honest fallback is this build's base — not 0, which is the default that
    # made this command quietly disagree with the window beside it.
    base = meta.get("base_threshold") if isinstance(meta, dict) else None
    # The bundle comes from the server too — an older server serves no preset
    # list, and "no bundle" is the honest reading of that, not a guess.
    presets = meta.get("threshold_presets") if isinstance(meta, dict) else None
    preset = {}
    if isinstance(presets, list) and preset_id != PRESET_SHIPPED:
        preset = next(
            (p.get("thresholds", {}) for p in presets if p.get("id") == preset_id),
            {},
        )
    # The server's active threshold snapshot (domain/threshold_snapshots.py).
    # Absent on an older server — and absent means no server layer, which is
    # exactly what that server has.
    active_profile = meta.get("active_threshold_profile") if isinstance(meta, dict) else None

    flagged = []
    for item in fetch_inventory(client):
        effective = (
            args.threshold if args.threshold is not None
            else resolve(
                item.get("section", ""), overrides, defaults, base,
                preset=preset, server=active_profile,
            )
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


def _jlc(client: V1Client, args) -> dict:
    """`dubis jlc library [ACCOUNT]` — the JLCPCB private parts library.

    A composition, not a route alias, for the same reason `get` is: with no
    account named this answers the *roster* (GET .../sessions) rather than
    guessing which warehouse to fetch, and with one named it projects the
    inventory-shaped records down to the five fields a library listing is
    about. /v1 exposes those as two unrelated routes; "show me my JLC parts"
    is one question.

    No response here can carry a credential: `sessions` returns account, label
    and two timestamps by construction (threat-model rule 4), and `library`
    returns parts.
    """
    account = (args.account or "").strip()
    if not account:
        status = client.get("/v1/distributors/jlcpcb/sessions")
        accounts = status.get("accounts", []) if isinstance(status, dict) else []
        return {
            "accounts": accounts,
            "count": len(accounts),
            # Named rather than implied even when exactly one account is
            # paired: a library read is a live JLC round trip, and `jlc
            # library` with no argument is how someone asks what is paired.
            "hint": "name an account to print its library: dubis jlc library <account>",
        }

    library = client.get("/v1/distributors/jlcpcb/library", account=account)
    records = library.get("records", []) if isinstance(library, dict) else []
    if args.limit is not None:
        records = records[: args.limit]
    return {
        "account": library.get("account", account),
        "total": library.get("total", len(records)),
        "returned": len(records),
        "parts": [
            {
                "lcsc": r.get("lcsc", ""),
                "mpn": r.get("mpn", ""),
                "qty": r.get("qty", 0),
                "package": r.get("package", ""),
                "description": r.get("description", ""),
            }
            for r in records
        ],
    }


# ── argparse wiring ──────────────────────────────────────────────────────────


def _args_search(parser):
    parser.add_argument("query", nargs="?", default="",
                        help="case-insensitive substring over every distributor part number "
                             "(lcsc/mpn/digikey/mouser/pololu) and over description, "
                             "manufacturer and package; empty matches everything")
    parser.add_argument("--section", default="", help="restrict to one exact section")
    parser.add_argument("--category-hit", dest="category_hit", action="store_true",
                        help="also keep rows whose SECTION the query names, even when no "
                             "field of the row contains it ('diodes' finds the Diodes "
                             "category, whose rows only ever say 'Diode'). Whole words "
                             "only, so 'ic' matches no category. Off by default: the CLI "
                             "has no preferences file, so unlike the app's Preferences "
                             "toggle (on by default) this can only be asked for.")
    parser.add_argument("--max-results", dest="max_results", type=int, default=25)


def _args_part_key(parser):
    parser.add_argument("part_key", help="LCSC/MPN/Digikey/Pololu/Mouser PN, or a derived part_key")


def _args_spec_search(parser):
    parser.add_argument("part_type", help='e.g. "capacitor", "resistor"')
    parser.add_argument("value", help='base units (1e-7) or a display string ("100nF")')
    parser.add_argument("--package", default="", help='e.g. "0402"; empty matches any')


def _args_low_stock(parser):
    parser.add_argument("--threshold", type=int, default=None,
                        help="quantity ceiling; omit to use per-section thresholds "
                             "(resolved against the server's active threshold snapshot — "
                             "the CLI has no client profile of its own)")


def _args_history(parser):
    _args_part_key(parser)
    parser.add_argument("--limit", type=int, default=10)


def _args_generic_groups(parser):
    parser.add_argument("--part-type", dest="part_type", default="")


def _args_jlc(parser):
    """`jlc` is the only curated command with a verb of its own.

    The sub-noun is spelled as a positional with `choices` rather than a nested
    subparser so the one dispatch rule in dubis.py (`CURATED[args.resource]`)
    keeps holding; a bad verb is still argparse's own exit 2, naming what is
    valid.
    """
    parser.add_argument("what", choices=["library"],
                        help="library: the private parts library (paired accounts if none named)")
    parser.add_argument("account", nargs="?", default="",
                        help="JLC account number; omit to list the paired accounts")
    parser.add_argument("--limit", type=int, default=None,
                        help="print at most this many parts (default: all of them)")


CURATED: dict[str, dict[str, Any]] = {
    "status": {
        "help": "which /v1 server and server source answer this session, and its part count",
        "add_args": lambda p: None,
        "run": _status,
    },
    "search": {
        "help": "substring search over inventory incl. every distributor part number, "
                "compact projection (--category-hit for a whole category)",
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
        "help": "parts at or below a stock threshold (per-section: the server's "
                "active threshold snapshot, then its shipped defaults)",
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
    "jlc": {
        "help": "JLCPCB: paired accounts, or one account's private parts library",
        "add_args": _args_jlc,
        "run": _jlc,
    },
}

__all__ = ["CURATED", "PartNotFoundError"]
