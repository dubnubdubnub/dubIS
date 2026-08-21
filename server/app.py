"""FastAPI app factory for the /v1 service layer.

The app wraps an existing InventoryApi instance (same object the pywebview
bridge uses); endpoints are sync functions so FastAPI's thread pool +
InventoryApi._lock serialize exactly like the bridge and PnP threads today.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.errors import register_handlers


def create_app(api, static_dir: str | None = None) -> FastAPI:
    app = FastAPI(title="dubIS", version="1", docs_url="/v1/docs",
                  openapi_url="/v1/openapi.json")
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
        openpnp,
        parts_read,
        pnp,
        predicates,
        preferences,
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
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
