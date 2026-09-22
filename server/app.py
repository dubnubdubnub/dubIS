"""FastAPI app factory for the /v1 service layer.

The app wraps an existing InventoryApi instance (same object the pywebview
bridge uses); endpoints are sync functions so FastAPI's thread pool +
InventoryApi._lock serialize exactly like the bridge and PnP threads today.

Since the multi-server work (docs/plans/2026-09-19-multi-server-hub-design.md)
this app is also a *hub*: `SourceDispatchMiddleware` decides, per request,
whether a `/v1` call is served in-process (today's path), proxied to another
dubIS server, or fanned out and merged across several. See server/dispatch.py
for why that is a middleware and why its position in the stack matters.
"""

from __future__ import annotations

import contextlib
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.errors import register_handlers


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    # Close the per-source httpx clients the hub pooled (server/sources.py).
    clients = getattr(app.state, "source_clients", None)
    if clients is not None:
        await clients.aclose()


def create_app(api, static_dir: str | None = None) -> FastAPI:
    app = FastAPI(title="dubIS", version="1", docs_url="/v1/docs",
                  openapi_url="/v1/openapi.json", lifespan=_lifespan)
    app.state.api = api
    register_handlers(app)

    from server.routes import (
        carts,
        distributors,
        events,
        feeders,
        generic_parts,
        import_scan,
        inventory_mut,
        meta,
        mirror,
        openpnp,
        parts_read,
        pnp,
        predicates,
        preferences,
        sources,
        vendors_pos,
    )
    app.include_router(meta.router)
    app.include_router(events.router)
    app.include_router(parts_read.router)
    app.include_router(predicates.router)
    app.include_router(inventory_mut.router)
    app.include_router(generic_parts.router)
    app.include_router(carts.router)
    app.include_router(vendors_pos.router)
    app.include_router(import_scan.router)
    app.include_router(distributors.router)
    app.include_router(pnp.router)
    app.include_router(preferences.router)
    app.include_router(openpnp.router)
    app.include_router(feeders.router)
    app.include_router(mirror.router)
    app.include_router(sources.router)

    # The pooled httpx clients (one per source url+token) that the proxy and the
    # fan-out share; closed by the lifespan above. Tests swap in a stub
    # transport by replacing this attribute before the first request.
    from server.dispatch import SourceDispatchMiddleware
    from server.sources import SourceClients
    from server.ssh_tunnel import TunnelManager

    # The tunnel supervisor records the ssh processes it owns in the data dir,
    # so the next hub start can reap any a hard-killed hub left behind
    # (server/ssh_tunnel.py, "Reaped"). Built lazily by SourceClients when the
    # api has no data dir (test doubles).
    prefs_json = getattr(api, "prefs_json", "")
    tunnels = TunnelManager(state_dir=os.path.dirname(prefs_json)) if prefs_json else None
    app.state.source_clients = SourceClients(tunnels=tunnels)
    # Added BEFORE AuthMiddleware on purpose: Starlette builds its stack so the
    # LAST-added middleware is outermost, so auth still runs first and still
    # gates every request, including the ones this one proxies.
    app.add_middleware(SourceDispatchMiddleware)

    if os.environ.get("DUBIS_AUTH_MODE", "off") == "on":
        from server.auth import AuthConfig, AuthMiddleware
        from server.routes import auth as auth_routes

        auth_config = AuthConfig.from_env()
        app.state.auth_config = auth_config
        app.add_middleware(AuthMiddleware, config=auth_config)
        app.include_router(auth_routes.router)

    if static_dir is not None and os.path.isdir(static_dir):
        # Mounted last so API routers above always win on path collisions.
        # AuthMiddleware (added above, if `on`) wraps the whole ASGI app
        # regardless of mount order, so static assets are gated too.
        # SourceDispatchMiddleware ignores anything not under /v1, so this
        # mount is untouched by it.
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
