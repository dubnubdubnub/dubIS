"""Preferences /v1 routes.

Preferences are free-form user-configurable settings (thresholds, column
choices, filters, etc.) persisted to `data/preferences.json`. Unlike the
inventory-mutating routes elsewhere in `server/routes/`, saving preferences
does not touch inventory-derived state, so there is no `finish_mutation`
call and no `inventory.updated` publish here.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

router = APIRouter(prefix="/v1", tags=["preferences"])


@router.get("/preferences", operation_id="load_preferences")
def load_preferences(request: Request) -> dict:
    api = request.app.state.api
    return api.load_preferences()


@router.put("/preferences", operation_id="save_preferences")
def save_preferences(request: Request, body: dict = Body(...)) -> dict:
    api = request.app.state.api
    api.save_preferences(body)
    return {"ok": True}
