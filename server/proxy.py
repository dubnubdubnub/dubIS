"""Forward one `/v1` request to one remote source and stream the answer back.

This is what "active source = a remote server" means in practice: the browser
keeps talking to the hub on its own loopback origin, and the hub re-issues the
request upstream carrying that source's bearer token. The browser therefore
never makes a cross-origin `/v1` call — which is the whole reason this is
server-side (no CORS, no preflight, no cross-site cookie rules, no mixed
content; see docs/plans/2026-09-19-multi-server-hub-design.md).

Two rules are load-bearing here:

* **Local-only paths never proxy.** `LOCAL_ONLY` below is an allowlist of the
  paths that are *about this hub*, not about inventory data, and proxying any of
  them would be a bug with teeth — `/v1/health` would report an upstream's
  liveness as our own (and is the one route carrying
  `Access-Control-Allow-Origin`), `/v1/sources` would let a remote's roster
  overwrite the switcher you are using to escape it, `/v1/preferences` would
  reintroduce exactly the wart this design removes (today the server picker's
  own roster moves when you switch servers, `js/store.js:311`),
  `/v1/import/parse` reads the SERVER's local disk and is loopback-gated for
  that reason, and `/v1/events` is the hub's own SSE bus — the hub re-publishes
  upstream events into it rather than handing the browser a foreign stream.

* **No new CORS surface.** Every `access-control-*` header an upstream sends is
  stripped from the proxied response, so a remote that is more permissive than
  us cannot widen this hub's exposure through the proxy.
  `tests/python/server/test_health_cors.py`'s spread guard stays true as
  written.
"""

from __future__ import annotations

import logging

import httpx
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import StreamingResponse

from dubis_errors import SourceConfigError, SourceUnavailableError
from server.sources import SELECTOR_SEPARATOR, Source, SourceClients, split_selectors

logger = logging.getLogger(__name__)

# Picks the target source explicitly, overriding whatever is active. This is how
# a write routes in merged mode: a merged row knows which source owns it, so the
# frontend stamps the mutation with that id. A header (rather than a path or
# query param) is chosen because js/api.js maps arguments *positionally* onto
# `entry.argOrder` across ~141 call sites — a header touches only the handful of
# mutation sites that need routing.
SOURCE_HEADER = "x-dubis-source"

# Its grammar: one id, several comma-separated ids, or the keyword `merged`.
#
#   X-Dubis-Source: shop          -> served by `shop`
#   X-Dubis-Source: bench,shop    -> a merge of exactly those two
#   X-Dubis-Source: merged        -> a merge of every enabled source
#
# Comma-separated because the value is a list of opaque ids with no internal
# structure, and a comma is what every other list-valued HTTP header uses; it
# also means a group costs one header, not one per member, on all ~141 call
# sites in js/api.js.
SOURCE_HEADER_SEPARATOR = SELECTOR_SEPARATOR


# What the hub sends UPSTREAM: "answer from your own data". Not the caller's
# value — see `_request_headers`.
PIN_TO_PEERS_OWN_DATA = "local"


def parse_source_header(raw: str | None) -> list[str]:
    """`"bench, shop"` -> `["bench", "shop"]`. Empty list when unset/blank."""
    return split_selectors(raw)


# Exact paths that are about THIS hub and must never be proxied.
LOCAL_ONLY_PATHS = frozenset({
    "/v1/health",
    "/v1/preferences",
    "/v1/import/parse",
    "/v1/events",
    "/v1/openapi.json",
    "/v1/docs",
    # Credential intake. The browser extension pushes a live JLC session
    # cookie here; forwarding it would hand a user's credential to a different
    # machine, and forwarding the pairing nonce that authorizes it would let an
    # upstream mint the token for that push. Both also call
    # `auth.require_loopback` — the allowlist stops the hub forwarding, the
    # guard stops a remote caller reaching the handler.
    "/v1/distributors/jlcpcb/pairing",
    "/v1/distributors/jlcpcb/session",
})

# Prefixes whose whole subtree is local-only. `/v1/sources` covers
# `/v1/sources/active` and the roster CRUD; `/v1/auth` covers the hub's own
# session bootstrap, which is meaningless against an upstream.
LOCAL_ONLY_PREFIXES = ("/v1/sources", "/v1/auth")

# Request headers that describe THIS connection, not the intent, plus the two
# credentials that must not leak upstream: the caller's Authorization (the hub
# substitutes the source's own token) and its cookies (the hub's session cookie
# means nothing to a different server).
_DROP_REQUEST_HEADERS = frozenset({
    "host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
    "authorization", "cookie", "accept-encoding",
    SOURCE_HEADER,
})

# Response headers describing the upstream hop's framing (httpx has already
# decoded the body for us), plus `set-cookie`, which would plant an upstream's
# session on the hub's origin.
_DROP_RESPONSE_HEADERS = frozenset({
    "content-encoding", "content-length", "transfer-encoding", "connection",
    "keep-alive", "trailer", "upgrade", "set-cookie",
})


def is_local_only(path: str) -> bool:
    """True for a path that must be served by this hub, never forwarded."""
    if path in LOCAL_ONLY_PATHS:
        return True
    return any(path == p or path.startswith(p + "/") for p in LOCAL_ONLY_PREFIXES)


def _request_headers(request: Request) -> list[tuple[bytes, bytes]]:
    """The caller's headers, minus this hop's, plus OUR pin.

    Two rules that look contradictory and are not:

    * The caller's `X-Dubis-Source` is dropped. Its ids name entries in THIS
      hub's roster and mean nothing upstream — `shop` here is very unlikely to
      be `shop` there.
    * The hub substitutes its own `X-Dubis-Source: local`. Every desktop dubIS
      is itself a hub with its own saved default, so without this a request to
      `bench` can come back holding whatever server bench's window was last
      switched to. The tab, the badge, the per-source breakdown and a routed
      adjustment would all name bench and all be about a different machine.
    """
    forwarded = [
        (key, value)
        for key, value in request.headers.raw
        if key.decode("latin-1").lower() not in _DROP_REQUEST_HEADERS
    ]
    forwarded.append((SOURCE_HEADER.encode("latin-1"), PIN_TO_PEERS_OWN_DATA.encode("latin-1")))
    return forwarded


def _response_headers(response: httpx.Response) -> list[tuple[str, str]]:
    out = []
    for key, value in response.headers.multi_items():
        lowered = key.lower()
        if lowered in _DROP_RESPONSE_HEADERS:
            continue
        # No new CORS surface, ever — not even one an upstream volunteered.
        if lowered.startswith("access-control-"):
            continue
        out.append((key, value))
    return out


async def forward(request: Request, source: Source, clients: SourceClients) -> StreamingResponse:
    """Re-issue *request* against *source* and stream the response back.

    The source's `Authorization: Bearer <token>` is attached by the pooled
    client (server/sources.SourceClients), so it cannot be forgotten at a call
    site; a source with no token sends none, which is the normal case for a
    server fronted by the tailscale operator proxy, where identity comes from
    the proxy.
    """
    path = request.url.path
    if is_local_only(path):
        raise SourceConfigError(
            f"{path} is served by this hub only and is never proxied to a source"
        )

    client = clients.get(source)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    body = await request.body()

    upstream_request = client.build_request(
        request.method,
        target,
        headers=_request_headers(request),
        content=body,
    )
    try:
        response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        raise SourceUnavailableError(
            f"source {source.id!r} ({source.url}) is unreachable: {type(exc).__name__}: {exc}",
            source_id=source.id,
            url=source.url,
        ) from exc

    return StreamingResponse(
        response.aiter_bytes(),
        status_code=response.status_code,
        headers=dict(_response_headers(response)),
        background=BackgroundTask(response.aclose),
    )
