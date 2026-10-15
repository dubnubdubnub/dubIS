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

`transfer` is the newest addition and the only one that WRITES: it moves stock
of one part between two configured sources of one hub, which is a composition
over four routes (`GET /v1/parts` twice, `POST /v1/parts`,
`POST /v1/parts/{key}/adjust` twice) plus the ordering and the refusals that no
single route can provide. It exists because the alternative was `purchases
import` — recording a purchase that never happened — to give a receiving server
a part with a name.

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
    V1Error,
    compact_part,
    derive_part_key,
    exact_part_number_match,
    fetch_inventory,
    matches_part_number,
    part_numbers,
    resolve_canonical_key,
    resolve_server,
)

# The prose fields `search` matches. The distributor part numbers are matched
# too, through `matches_part_number` rather than a second field list here, so
# the fields a caller can SEARCH and the fields it can SEE (compact_part's
# `part_numbers`) are the same tuple in tools/dubis_client/curate.py and cannot
# drift apart again — which is exactly how the DigiKey column went invisible
# twice over.
_TEXT_SEARCH_FIELDS = ("description", "manufacturer", "package")

# The prose fields `transfer` copies alongside the part numbers when a
# destination has never heard of the part. The numbers themselves come from
# `part_numbers()` — the same helper `search` and `get` use — because a part's
# identity in dubIS is that whole set, and a transfer that carried only the
# canonical key would arrive with the DigiKey and Mouser numbers stripped off.
# Nothing here says how much there is or who sold it: the quantity moves as an
# adjustment, and a price is a distributor's claim, not a fact about the part.
TRANSFER_TEXT_FIELDS = ("manufacturer", "package", "description")


class TransferError(Exception):
    """A transfer the CLI will not attempt, or stopped partway through.

    Exit 3, the same code as a server-rejected request: the caller asked for
    something the servers will not do. The halfway case carries the sentence
    that says which half landed, because "double-counted and visible" is the
    only acceptable shape for a half-finished transfer.
    """


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


def _resolve_transfer_end(client: V1Client, requested: str | None, via: str, role: str):
    """Resolve one end of a transfer to exactly one source, refusing a view.

    With no `--server` the destination is the hub's persisted default, read
    here rather than left implicit: a transfer has to be able to say where the
    stock went, and a write that lands on "whatever window last clicked a tab"
    is not an answer. A view (`merged`, or a comma group) has no single store to
    write to — the hub refuses one too, but by then the part has already been
    read from somewhere arbitrary.
    """
    if requested is None:
        roster = client.get("/v1/sources")
        requested = (roster.get("default") if isinstance(roster, dict) else "") or "local"
        via = "hub-default"
    selection = resolve_server(client, requested, via)
    if selection.is_view:
        raise TransferError(
            f"the {role} resolved to {selection.selector!r}, which is a merged view of "
            "several sources. A transfer writes stock to one store, so name one server."
        )
    return selection


def _resolve_part(items: list[dict], typed: str, server: str) -> dict | None:
    """The row a person means, exact first, then a fragment that names one.

    They type `TMR2615F`; the store's key is `TMR2615F-AAC-1.500-500`. A label
    carries a fragment of the key, and (see `part_numbers`) often a fragment of
    a *different* PN than the key — a DigiKey number with its `-ND` suffix. So
    an exact PN match wins, and a substring match is accepted only when exactly
    one row answers it: guessing between two parts is how stock gets moved for
    the wrong one, on the server that is not in front of you.
    """
    wanted = (typed or "").strip().lower()
    exact = [i for i in items
             if exact_part_number_match(i, wanted) or derive_part_key(i).lower() == wanted]
    if len(exact) > 1:
        raise TransferError(
            f"{typed!r} is the whole part number of several parts on {server}: "
            f"{sorted(derive_part_key(i) for i in exact)}; name one exactly"
        )
    if exact:
        return exact[0]
    partial = [i for i in items if matches_part_number(i, wanted)]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        return None
    raise TransferError(
        f"{typed!r} matches several parts on {server}: "
        f"{sorted(derive_part_key(i) for i in partial)}; name one exactly"
    )


def _transfer_side(client: V1Client, selection, part_key: str) -> dict | None:
    """Pin the client to one source and resolve *part_key* there.

    Returns that store's view of the part — the key IT calls it by, its on-hand
    quantity, and the identity a destination would need — or None when the store
    has never heard of it. The key is the store's, never what the caller typed,
    and every write below names that one: it is what the server keys off.
    """
    client.use_source(selection)
    item = _resolve_part(fetch_inventory(client), part_key, selection.names[0][1])
    if item is None:
        return None
    return {
        "selector": selection.selector,
        "name": selection.names[0][1],
        "part_key": derive_part_key(item),
        "qty": int(item.get("qty", 0) or 0),
        "identity": {
            **part_numbers(item),
            **{f: (item.get(f) or "").strip() for f in TRANSFER_TEXT_FIELDS
               if (item.get(f) or "").strip()},
        },
        "unit_price": float(item.get("unit_price") or 0.0),
    }


def _transfer(client: V1Client, args) -> dict:
    """`dubis transfer PART --quantity N --from aspen --server fremont`.

    Moves stock between two configured sources of one hub. The order of the two
    writes is the whole design: the destination is credited first and the source
    debited second, so the one failure that can happen mid-way leaves the parts
    counted twice — visible in both grids, correctable by hand — rather than
    physically present and recorded nowhere.

    A destination that does not know the part gets its identity through
    `POST /v1/parts`, which writes no price observation: the part must not
    arrive looking as though the destination bought it.
    """
    quantity = int(args.quantity)
    if quantity <= 0:
        raise TransferError(f"--quantity must be positive, got {quantity}")
    tag = args.source or "cli"

    dest = _resolve_transfer_end(client, args.server, args.server_via, "destination (--server)")
    src = _resolve_transfer_end(client, args.from_server, "flag", "source (--from)")
    if src.selector == dest.selector:
        raise TransferError(
            f"--from and --server both resolve to {src.selector!r}; a transfer needs two "
            "different sources (moving stock onto itself would just double it)"
        )

    source = _transfer_side(client, src, args.part_key)
    if source is None:
        raise TransferError(
            f"{args.part_key!r} matches no part on {src.names[0][1]} "
            f"({src.selector}); nothing was written to either source"
        )
    destination = _transfer_side(client, dest, args.part_key)
    if destination is None:
        # Two stores routinely know one part by different numbers: aspen recorded
        # the MPN, fremont only the DigiKey number. Searching on what the caller
        # typed alone would miss the destination and create a SECOND part for
        # the same reel, so try every number the source carries.
        for candidate in [source["part_key"], *source["identity"].values()]:
            destination = _transfer_side(client, dest, candidate)
            if destination is not None:
                break

    if source["qty"] < quantity:
        raise TransferError(
            f"{src.names[0][1]} holds {source['qty']} of {source['part_key']}, not "
            f"{quantity}. Refusing to remove more than it has: the server floors a "
            "removal at zero, so the difference would vanish instead of failing."
        )

    note = args.note or f"transfer {quantity} -> {dest.names[0][1]} [{tag}]"
    dst_key = destination["part_key"] if destination else source["part_key"]

    create_body: dict | None = None
    if destination is None:
        create_body = {"part_key": source["part_key"], **source["identity"], "source": tag}
        if source["unit_price"] > 0:
            create_body["unit_price"] = source["unit_price"]
    add_body = {"adj_type": "add", "quantity": quantity, "note": note, "source": tag}
    remove_body = {"adj_type": "remove", "quantity": quantity, "note": note, "source": tag}

    plan: list[dict] = []
    if create_body is not None:
        plan.append({"side": "destination", "method": "POST", "path": "/v1/parts",
                     "body": create_body})
    plan.append({"side": "destination", "method": "POST",
                 "path": f"/v1/parts/{dst_key}/adjust", "body": add_body})
    plan.append({"side": "source", "method": "POST",
                 "path": f"/v1/parts/{source['part_key']}/adjust", "body": remove_body})

    resolved = {
        "requested": args.part_key,
        "source": {"server": src.names[0][1], "selector": src.selector,
                   "part_key": source["part_key"], "qty": source["qty"]},
        # The destination SERVER is always named, part or no part: "where does
        # this go" is a question the caller needs answered even when the answer
        # is "to a store that has never heard of it".
        "destination": {
            "server": dest.names[0][1], "selector": dest.selector,
            "known": destination is not None,
            "part_key": destination["part_key"] if destination else source["part_key"],
            "qty": destination["qty"] if destination else 0,
        },
        "destination_will_create": destination is None,
    }
    expected = {
        "source": source["qty"] - quantity,
        "destination": (destination["qty"] if destination else 0) + quantity,
    }
    if args.dry_run:
        return {
            "dry_run": True,
            "quantity": quantity,
            "resolved": resolved,
            "plan": plan,
            "expected": {
                src.names[0][1]: expected["source"],
                dest.names[0][1]: expected["destination"],
            },
        }

    # Destination first, always. See the docstring: the alternative failure
    # mode is stock that exists and is recorded nowhere.
    client.use_source(dest)
    if create_body is not None:
        created = client.post("/v1/parts", create_body)
        detail = created.get("detail") if isinstance(created, dict) else None
        if isinstance(detail, dict) and detail.get("part_key"):
            dst_key = detail["part_key"]
    client.post(f"/v1/parts/{dst_key}/adjust", add_body)

    client.use_source(src)
    try:
        client.post(f"/v1/parts/{source['part_key']}/adjust", remove_body)
    except V1Error as exc:
        raise TransferError(
            f"{dest.names[0][1]} already has {quantity} more of {dst_key}, but removing "
            f"them from {src.names[0][1]} failed: {exc}. The stock is now counted twice. "
            f"Undo the destination with: dubis parts adjust {dst_key} --adj-type remove "
            f"--quantity {quantity} --server {dest.selector} --source {tag}"
        ) from exc

    rows = []
    for side, selection, key, before, want in (
        ("source", src, source["part_key"], source["qty"], expected["source"]),
        ("destination", dest, dst_key,
         destination["qty"] if destination else 0, expected["destination"]),
    ):
        after = _transfer_side(client, selection, key)
        actual = None if after is None else after["qty"]
        rows.append({
            "server": selection.names[0][1],
            "side": side,
            "part_key": key,
            "before": before,
            "expected": want,
            "actual": actual,
            "ok": actual == want,
        })

    return {
        "dry_run": False,
        "quantity": quantity,
        "source_tag": tag,
        "resolved": resolved,
        "created_on_destination": create_body is not None,
        "verification": rows,
        "verified": all(row["ok"] for row in rows),
        "undo": (
            f"dubis adjustments rollback-source {tag} --server {src.selector} "
            f"and --server {dest.selector}"
        ),
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


def _args_transfer(parser):
    """`transfer` is the one curated command that WRITES, so it is the one the
    global `--dry-run` means something for — and the one that has to read both
    servers to answer "what would this do", which is exactly what a dry run is
    for here. It therefore does contact the servers, and writes nothing."""
    parser.add_argument("part_key",
                        help="any PN or part_key; each side is resolved to ITS own key")
    parser.add_argument("--quantity", type=int, required=True,
                        help="how many to move")
    parser.add_argument("--from", dest="from_server", required=True, metavar="ID|NAME",
                        help="source server the stock leaves (a /v1/sources id or name); "
                             "`merged` and comma lists are refused — a view has no store")
    parser.add_argument("--note", default="",
                        help="adjustment note (default names both servers and the tag)")


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
    "transfer": {
        "help": "move stock of one part between two configured servers "
                "(--from SOURCE --server DEST); credits the destination first",
        "add_args": _args_transfer,
        "run": _transfer,
        "writes": True,
    },
    "jlc": {
        "help": "JLCPCB: paired accounts, or one account's private parts library",
        "add_args": _args_jlc,
        "run": _jlc,
    },
}

__all__ = ["CURATED", "PartNotFoundError", "TransferError"]
