"""`/v1/sources` — the hub's source roster and which one is active.

This whole subtree is local-only (`server/proxy.LOCAL_ONLY_PREFIXES`): it is the
switcher you use to get *out* of a remote, so proxying it would let the remote's
roster replace it and leave no way back.

There is no "switch here" route, because the hub holds no active source to
switch. A window chooses its server per request with `X-Dubis-Source`
(`server/dispatch.py`); `PUT /v1/sources/active` only records the DEFAULT for
clients that send no header.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Request

from server import sources as sources_mod
from server.models import (
    CreateSourceBody,
    SetActiveSourceBody,
    SourcesResponse,
    UpdateSourceBody,
)
from server.mutations import finish_mutation

router = APIRouter(prefix="/v1", tags=["sources"])


async def _status_payload(request: Request, registry: sources_mod.Registry) -> dict:
    """Roster + active + a live reachability probe of every source, in parallel.

    `local` is deliberately NOT in `sources`. It is this process — the one that
    just answered — so it can neither be configured nor be unreachable, and
    both halves of the frontend synthesize it rather than read it
    (`js/servers-logic.js`'s roster and `js/server-tabs-logic.js`'s tab strip
    both reserve the id for exactly that reason). Listing it would render two
    Local tabs, switchable in two contradictory ways.
    """
    clients: sources_mod.SourceClients = request.app.state.source_clients
    remotes = registry.remotes
    status: dict[str, sources_mod.ProbeResult] = {}

    async def probe_one(source: sources_mod.Source) -> None:
        status[source.id] = await sources_mod.probe(source, clients)

    async with anyio.create_task_group() as tg:
        for source in remotes:
            tg.start_soon(probe_one, source)

    unprobed = sources_mod.ProbeResult(reachable=False, detail="not probed")

    return {
        # `default` is the canonical name: it is what a request with no
        # `X-Dubis-Source` falls back to, not "the server this hub is on" —
        # there is no such thing. `active` is the same value under the name
        # `js/server-tabs-logic.js` already reads, and is what a fresh window
        # adopts before the user picks a tab.
        "default": registry.default,
        "active": registry.default,
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "enabled": s.enabled,
                # Whether a credential is held — never the credential. See
                # server/token_store.py: the token does not travel to any
                # client, and `has_token` is what lets the picker say "this
                # server needs one" instead of leaving the user to infer it
                # from a wall of 401s.
                "has_token": bool(s.token),
                "reachable": status.get(s.id, unprobed).reachable,
                "detail": status.get(s.id, unprobed).detail,
                "auth": status.get(s.id, unprobed).auth,
                # The hub-managed ssh tunnel behind an `ssh://` source, or null.
                # `detail` above already carries its failure sentence; this is
                # the structured half (state, kind) the picker can branch on.
                "tunnel": clients.tunnel_status(s),
            }
            for s in remotes
        ],
    }


@router.get("/sources", response_model=SourcesResponse, operation_id="list_sources")
async def list_sources(request: Request) -> dict:
    registry = sources_mod.load_registry_from_api(request.app.state.api)
    return await _status_payload(request, registry)


@router.put("/sources/active", operation_id="set_active_source")
def set_active_source(request: Request, body: SetActiveSourceBody) -> dict:
    """Save the DEFAULT source. This does NOT switch anything that is running.

    The name invites the wrong reading, and it is kept only because
    `active_source` is already the persisted key, so say it plainly: this writes
    a preference and stops. It changes nothing any open window is showing,
    because every window names its own source on every request
    (`X-Dubis-Source`, see server/dispatch.py) — which is exactly what lets two
    windows sit on two different servers at the same time. What it changes is
    where a client that sends NO header lands: `tools/dubis-cli`, curl, and the
    next page load before it has resolved its own source.

    It publishes nothing, for the same reason: no data any client is currently
    looking at has changed. Recorded as such in
    `tests/python/server/test_mutation_publishes.py`'s EXEMPT list.
    """
    registry = sources_mod.set_default(request.app.state.api, body.source)
    return {
        "ok": True,
        "detail": {"default": registry.default, "server_url": registry.default_url},
    }


def _source_detail(source: sources_mod.Source, registry: sources_mod.Registry) -> dict:
    return {
        "source": {"id": source.id, "name": source.name, "url": source.url,
                   "enabled": source.enabled, "has_token": bool(source.token)},
        "default": registry.default,
    }


@router.post("/sources", operation_id="create_source")
def create_source(request: Request, body: CreateSourceBody) -> dict:
    """Add a source to the roster.

    Publishes like any other mutation, because in `merged` mode it *is* one: the
    merged inventory is the union of the enabled sources, so adding one changes
    what `GET /v1/parts` answers on the very next request. The same reasoning
    covers the PATCH and DELETE below (a token, a URL, or `enabled` flipping all
    change the data served), which is why none of them is exempt from
    `tests/python/server/test_mutation_publishes.py`.
    """
    registry, source = sources_mod.add_source(
        request.app.state.api,
        url=body.url,
        source_id=body.id,
        name=body.name,
        token=body.token,
        enabled=body.enabled,
    )
    return finish_mutation("source.added", _source_detail(source, registry))


@router.patch("/sources/{source_id}", operation_id="update_source")
def update_source(request: Request, source_id: str, body: UpdateSourceBody) -> dict:
    registry, source = sources_mod.update_source(
        request.app.state.api,
        source_id,
        name=body.name,
        url=body.url,
        token=body.token,
        enabled=body.enabled,
    )
    return finish_mutation("source.updated", _source_detail(source, registry))


@router.delete("/sources/{source_id}", operation_id="delete_source")
def delete_source(request: Request, source_id: str) -> dict:
    """Remove a roster entry. Deleting the ACTIVE source falls back to `local`."""
    registry = sources_mod.remove_source(request.app.state.api, source_id)
    return finish_mutation("source.removed", {
        "removed": source_id,
        "default": registry.default,
        "server_url": registry.default_url,
    })
