"""Where a `/v1` request is served from — decided by the request, and only by it.

## No "current server" anywhere

The hub keeps **no mutable active-source state**. Every `/v1` request names the
source that serves it with `X-Dubis-Source: <id> | local | merged`, and a request
that names none falls back to the *persisted default* (`server/sources.py`).
`resolve_target` below is a pure function of `(registry, request)`, where the
registry is itself rebuilt read-only from preferences on every call.

This is the requirement, not an implementation detail. Several app windows are
open at once, deliberately on different servers. Had the hub held one "active
source" that a switch flipped, window A clicking a tab would move window B's data
underneath it mid-session — a worse bug than the restart-to-switch one this
feature removes. It also removes the race between two clients writing that state.

`PUT /v1/sources/active` therefore only saves a preference; it changes nothing
live. See its docstring in `server/routes/sources.py`.

**Frontend contract**: a dubIS window must send `X-Dubis-Source` on *every*
`/v1` request, including the first one of a page load. A request without it is
served from the persisted default, which is another window's setting — correct
for `tools/dubis-cli` and curl, and briefly wrong for a window that has not yet
told the hub what it is looking at.

## Why a middleware

The decision is a property of the request, not of any one route: all 15 routers
and their ~100 operations are equally subject to it, and a route that forgot to
declare a dependency would silently keep serving local data while the switcher
claimed otherwise — a wrong answer that looks like a working one. A middleware
cannot be forgotten, needs no edit to any existing route module, and is the only
one of the three options that can forward a request *without* it having been
parsed and validated against a local route's signature first (a proxied
`POST /v1/parts/C1/adjust` must reach the upstream verbatim; it is not ours to
validate).

Ordering, which is load-bearing:

* `create_app` adds this middleware BEFORE `AuthMiddleware`. Starlette builds
  its stack so the LAST-added middleware is outermost, so `AuthMiddleware` still
  runs first and still gates every request — including the ones that end up
  proxied. Nothing here can be reached by an unauthenticated caller that could
  not already reach the local route.
* `/v1/health` is untouched twice over: it is in `AuthMiddleware.EXEMPT_PATHS`
  and in `proxy.LOCAL_ONLY_PATHS`, so it answers about THIS hub whatever any
  header says.
* Anything not under `/v1` — the `/api/*` OpenPnP aliases and every static asset
  from the `StaticFiles` mount at `/` — passes straight through, so the mount is
  unaffected.

## The three outcomes

| `X-Dubis-Source` | `GET /v1/parts`          | everything else under `/v1`    |
|------------------|--------------------------|---------------------------------|
| `local`          | local route              | local route                     |
| `shop`           | proxied to `shop`        | proxied to `shop`               |
| `bench,shop`     | merge of those two       | read: local · write: 400        |
| `merged`         | merge of every enabled   | read: local · write: 400        |
| *(absent)*       | the persisted default    | the persisted default           |

A set merges the inventory and *only* the inventory: it is a view of parts, and
there is no sensible union of two servers' carts or preferences. The read/write
asymmetry on those two rows is deliberate and lives in `Registry.resolve` — a
caller that explicitly asked to merge on a **write** must hear that it has no
answer (`js/api.js`'s `apiOn` names the owning server instead), while a *read*
must not break for a window sitting on the All tab: that window sends the header
on every request, so keying the refusal on the header alone refused its carts,
vendors, purchase orders, warnings and generic-part groups too.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from dubis_errors import DubISError, SourceProtocolError
from server import events, fanout, proxy
from server.sources import MergeSet, Registry, Source, SourceClients

logger = logging.getLogger(__name__)

# The one path a merged view actually merges.
MERGED_PATH = "/v1/parts"

# Verbs whose forwarded success is worth re-announcing on the local SSE bus.
_MUTATING_VERBS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _merge_inventories(by_source: list[tuple[Any, list[dict]]]) -> list[dict]:
    """Thin seam over `domain/federation.py`'s pure merge.

    Imported lazily and through this one function for two reasons: the module is
    pure domain logic that must not be dragged into `create_app`'s import graph,
    and it keeps exactly one place in the server that knows the merge's name and
    signature — `merge_inventories(by_source) -> list[dict]`, where `by_source`
    is `[(SourceInfo(id, name), records), ...]`.
    """
    from domain.federation import merge_inventories  # noqa: PLC0415

    return merge_inventories(by_source)


def _source_info(source: Source) -> Any:
    from domain.federation import SourceInfo  # noqa: PLC0415

    return SourceInfo(id=source.id, name=source.name)


def inventory_records(body: Any) -> list[dict]:
    """A peer's `GET /v1/parts` body -> its rows, or raise.

    Raising is the whole point. A source that is up, answers 200 and speaks
    JSON — a captive portal, an SSO page, an nginx shim, a dubIS that renamed
    its envelope — used to be read with `body.get("inventory", [])` and reported
    as a source that answered fine while contributing nothing. That is the worst
    failure this feature has: the totals are short by a whole machine, the
    per-source status says everything is fine, and the partial-view banner,
    which reads only `ok`, shows nothing.

    `server/fanout.py` catches this per source, so an unrecognizable body lands
    in exactly the same place as a refused connection: `ok: false`, with a
    reason.
    """
    if not isinstance(body, dict) or not isinstance(body.get("inventory"), list):
        shape = type(body).__name__
        if isinstance(body, dict):
            shape = f"{{{', '.join(sorted(body)[:4])}}}"
        raise SourceProtocolError(
            f"answered 200 but not with a dubIS inventory envelope (got {shape}); "
            "it is reachable, but it is not answering this question"
        )
    return body["inventory"]


def _error_response(exc: Exception) -> JSONResponse:
    """Render *exc* with the same `{error, code, detail}` contract as every route.

    A middleware sits OUTSIDE Starlette's `ExceptionMiddleware`, so the handlers
    `server/errors.register_handlers` installs never see what is raised in here —
    an uncaught error would reach `ServerErrorMiddleware` and come back as an
    opaque 500 with no `code`. Rather than hardcode a second status table that
    could drift, this looks the exception up in `server/errors._MAPPING`, the one
    the routes use.
    """
    from server.errors import _MAPPING  # noqa: PLC0415 — avoids an import cycle

    status, code = 500, "dubis_error"
    for exc_type, mapped_status, mapped_code in _MAPPING:
        if isinstance(exc, exc_type):
            status, code = mapped_status, mapped_code
            break
    logger.warning("/v1 dispatch -> %s: %s", code, exc)
    return JSONResponse(
        status_code=status,
        content={"error": str(exc) or type(exc).__name__, "code": code, "detail": None},
    )


def is_mergeable(request: Request) -> bool:
    """Does this request have a merged answer at all? Only `GET /v1/parts` does."""
    return request.method == "GET" and request.url.path == MERGED_PATH


def resolve_target(registry: Registry, request: Request) -> Source | MergeSet:
    """What serves this request: one `Source`, or a `MergeSet` to merge across.

    Pure: `(registry, request)` in, the same answer out, every time. Nothing is
    read from or written to server state, which is what makes two windows on two
    different servers independent of each other.

    `X-Dubis-Source` wins — including `X-Dubis-Source: local`, which is how a
    window pins a request to this hub. With no header the persisted default
    applies, so `tools/dubis-cli`, curl and any client written before this
    feature keep working unchanged. See `Registry.resolve` for the three shapes
    the header can take.
    """
    requested = proxy.parse_source_header(request.headers.get(proxy.SOURCE_HEADER))
    selectors = requested or registry.default_selectors
    return registry.resolve(
        selectors,
        mergeable=is_mergeable(request),
        explicit=bool(requested),
        # Safe verb or not. A view has no answer for a write on any route but
        # `GET /v1/parts`, and `Registry.resolve` refuses one rather than
        # guessing an owner; a *read* off that route falls back to local.
        mutating=request.method in _MUTATING_VERBS,
    )


class SourceDispatchMiddleware(BaseHTTPMiddleware):
    """Serve `/v1` from the active source: locally, proxied, or merged."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path != "/v1" and not path.startswith("/v1/"):
            return await call_next(request)
        if proxy.is_local_only(path):
            return await call_next(request)

        app = request.app
        api = getattr(app.state, "api", None)
        if api is None:  # pragma: no cover — create_app always sets it
            return await call_next(request)

        try:
            # Read per request, deliberately: the switch must take effect
            # immediately and with no restart, and preferences.json is also
            # written by `PUT /v1/preferences` and by the JS server picker — any
            # cache here would need invalidating from three directions to buy
            # back one small JSON read per request.
            from server.sources import load_registry_from_api  # noqa: PLC0415

            registry = load_registry_from_api(api)
            target = resolve_target(registry, request)
        except DubISError as exc:
            return _error_response(exc)

        if isinstance(target, MergeSet):
            try:
                return await self._merged_parts(request, target)
            except DubISError as exc:
                # Includes the merge's own loud refusals (UnkeyablePartError,
                # MergeError from domain/federation.py). A source being *down* is
                # not one of these — fan-out records that per source and the
                # merged view degrades; see server/fanout.py.
                return _error_response(exc)
        if target.is_local:
            return await call_next(request)

        clients: SourceClients = app.state.source_clients
        try:
            response = await proxy.forward(request, target, clients)
        except DubISError as exc:
            return _error_response(exc)

        # A write we forwarded changed that source's data, and only the upstream
        # knows it — the hub does not subscribe to peers' `/v1/events` (that
        # background subscriber is a separate piece of the design). Re-publishing
        # it here, TAGGED WITH THE SOURCE, is what makes the window that issued
        # the write re-render. The tag is the point: a client filters on it
        # rather than the stream carrying a server-global idea of which source
        # anyone cares about, so a write on `shop` does not claim to be news
        # about `bench`.
        if request.method in _MUTATING_VERBS and response.status_code < 400:
            events.publish("inventory.updated", {
                "reason": "source.proxied_mutation",
                "detail": {"method": request.method, "path": path},
                "source": target.id,
            })
        return response

    async def _merged_parts(self, request: Request, wanted: MergeSet) -> JSONResponse:
        """`GET /v1/parts` across exactly *wanted*, summed by part key.

        `wanted.included` is what gets fetched — a tab group of two, or every
        enabled source for `merged`. `Registry.resolve` has already refused an
        unknown id (404), so nothing here can quietly shrink that set.

        `wanted.excluded` is what was deliberately left out (a disabled source),
        and it is reported in the SAME `sources` array as a failed entry. That is
        the invariant this method exists to hold: **a source left out of a merged
        view is still named in it**. Exclusion and failure are different reasons
        for the same missing stock, and a total missing a whole machine looks
        exactly like a complete one, so both have to be sayable.

        Answers 200 when sources are down or excluded: an id that does not exist
        is a client error, an id that does not answer is Tuesday. The per-source
        status is what the partial-view banner is built on.
        """
        app = request.app
        api = app.state.api
        clients: SourceClients = app.state.source_clients

        results = await fanout.fetch_path(
            wanted.included,
            MERGED_PATH,
            clients=clients,
            local_fetch=lambda: {"inventory": api._load_organized()},
            # Raises on a body that is not a dubIS inventory envelope, which
            # fan-out records as a source that did not answer. Without it a
            # reachable-but-not-dubIS peer reports `ok: true` and contributes
            # nothing — see `inventory_records`.
            extract=inventory_records,
        )

        by_source = [
            (_source_info(result.source), result.data)
            for result in results if result.ok
        ]
        status = [result.as_status() for result in results]
        status.extend(
            {"id": source.id, "name": source.name, "ok": False,
             "error": reason, "status": None, "excluded": True}
            for source, reason in wanted.excluded
        )

        return JSONResponse({
            "inventory": _merge_inventories(by_source),
            "sources": status,
        })
