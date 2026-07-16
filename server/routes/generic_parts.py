"""Generic-parts + saved-searches /v1 routes.

Generic-part/member mutations (create/update/add/remove/exclude/preferred)
change flyout state derived from inventory, so they end with
`finish_mutation(..., reason="generic-parts")`, which publishes
`inventory.updated`. The facade's own return value (members list or dict)
is passed through as `detail` — there is no `include=inventory` support
here since these facades don't return the inventory list.

Saved-search create/delete do NOT publish: saved searches are UI-scoped
convenience state (a named tag/search snapshot), not inventory-derived
data, so no other client needs to be notified when one changes.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from server.mutations import finish_mutation

router = APIRouter(prefix="/v1", tags=["generic-parts"])


# ── Body models ─────────────────────────────────────────────────────────────


class CreateGenericPartBody(BaseModel):
    name: str
    part_type: str
    spec: dict
    strictness: dict


class UpdateGenericPartBody(BaseModel):
    name: str
    spec: dict
    strictness: dict


class AddMemberBody(BaseModel):
    part_id: str


class CreateSavedSearchBody(BaseModel):
    name: str
    tag_state: dict
    search_text: str = ""
    frozen_members: list = []


# ── Generic parts ────────────────────────────────────────────────────────────


@router.get("/generic-parts", operation_id="list_generic_parts")
def list_generic_parts(request: Request) -> list:
    api = request.app.state.api
    return api.list_generic_parts()


@router.post("/generic-parts", operation_id="create_generic_part")
def create_generic_part(request: Request, body: CreateGenericPartBody) -> dict:
    api = request.app.state.api
    result = api.create_generic_part(body.name, body.part_type, body.spec, body.strictness)
    return finish_mutation(api, None, None, reason="generic-parts", detail=result)


@router.put("/generic-parts/{generic_part_id}", operation_id="update_generic_part")
def update_generic_part(request: Request, generic_part_id: str, body: UpdateGenericPartBody) -> dict:
    api = request.app.state.api
    result = api.update_generic_part(generic_part_id, body.name, body.spec, body.strictness)
    return finish_mutation(api, None, None, reason="generic-parts", detail=result)


@router.post("/generic-parts/{generic_part_id}/members", operation_id="add_generic_member")
def add_generic_member(request: Request, generic_part_id: str, body: AddMemberBody) -> dict:
    api = request.app.state.api
    members = api.add_generic_member(generic_part_id, body.part_id)
    return finish_mutation(api, None, None, reason="generic-parts", detail=members)


@router.delete(
    "/generic-parts/{generic_part_id}/members/{part_id}", operation_id="remove_generic_member",
)
def remove_generic_member(request: Request, generic_part_id: str, part_id: str) -> dict:
    api = request.app.state.api
    members = api.remove_generic_member(generic_part_id, part_id)
    return finish_mutation(api, None, None, reason="generic-parts", detail=members)


@router.post(
    "/generic-parts/{generic_part_id}/members/{part_id}/exclude", operation_id="exclude_generic_member",
)
def exclude_generic_member(request: Request, generic_part_id: str, part_id: str) -> dict:
    api = request.app.state.api
    api.exclude_generic_member(generic_part_id, part_id)
    return finish_mutation(
        api, None, None, reason="generic-parts",
        detail={"generic_part_id": generic_part_id, "part_id": part_id},
    )


@router.put(
    "/generic-parts/{generic_part_id}/members/{part_id}/preferred", operation_id="set_preferred_member",
)
def set_preferred_member(request: Request, generic_part_id: str, part_id: str) -> dict:
    api = request.app.state.api
    members = api.set_preferred_member(generic_part_id, part_id)
    return finish_mutation(api, None, None, reason="generic-parts", detail=members)


# ── Saved searches (no publish — UI-scoped, not inventory-derived) ─────────


@router.get(
    "/generic-parts/{generic_part_id}/saved-searches", operation_id="list_saved_searches",
)
def list_saved_searches(request: Request, generic_part_id: str) -> list:
    api = request.app.state.api
    return api.list_saved_searches(generic_part_id)


@router.post(
    "/generic-parts/{generic_part_id}/saved-searches", operation_id="create_saved_search",
)
def create_saved_search(request: Request, generic_part_id: str, body: CreateSavedSearchBody) -> dict:
    api = request.app.state.api
    result = api.create_saved_search(
        generic_part_id, body.name, body.tag_state, body.search_text, body.frozen_members,
    )
    return {"ok": True, "detail": result}


@router.delete("/saved-searches/{search_id}", operation_id="delete_saved_search")
def delete_saved_search(request: Request, search_id: str) -> dict:
    api = request.app.state.api
    api.delete_saved_search(search_id)
    return {"ok": True, "detail": {"search_id": search_id}}
