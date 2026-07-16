"""FastAPI app factory for the /v1 service layer.

The app wraps an existing InventoryApi instance (same object the pywebview
bridge uses); endpoints are sync functions so FastAPI's thread pool +
InventoryApi._lock serialize exactly like the bridge and PnP threads today.
"""

from __future__ import annotations

from fastapi import FastAPI

from server.errors import register_handlers


def create_app(api) -> FastAPI:
    app = FastAPI(title="dubIS", version="1", docs_url="/v1/docs",
                  openapi_url="/v1/openapi.json")
    app.state.api = api
    register_handlers(app)

    from server.routes import (
        distributors,
        events,
        generic_parts,
        import_scan,
        inventory_mut,
        meta,
        parts_read,
        pnp,
        preferences,
        vendors_pos,
    )
    app.include_router(meta.router)
    app.include_router(events.router)
    app.include_router(parts_read.router)
    app.include_router(inventory_mut.router)
    app.include_router(generic_parts.router)
    app.include_router(vendors_pos.router)
    app.include_router(import_scan.router)
    app.include_router(distributors.router)
    app.include_router(pnp.router)
    app.include_router(preferences.router)
    return app
