"""`/v1/kicad/*` -- KiCad HTTP Library protocol (Phase 4).

Four read-only GET endpoints implementing KiCad's HTTP symbol/footprint
library protocol (design doc `docs/plans/2026-07-17-phase4-kicad-design.md`
§1). All are pure reads: no `finish_mutation` call, nothing to publish
`inventory.updated` for -- unlike `generic_parts.py`, there is no mutation
here at all.

Gating (category resolution, symbol resolution, eligibility) lives entirely
in `domain/kicad_view.py`, not here -- these handlers are thin: refresh the
cache, grab the connection, delegate, translate "not visible" to a uniform
404. Auth is handled entirely by `server/auth.py`'s `AuthMiddleware`, which
wraps the whole ASGI app when `DUBIS_AUTH_MODE=on` -- no per-route auth code
needed here (this router is mounted unconditionally, exactly like every
other `/v1` router; the middleware decides whether to gate it).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

import domain.kicad_view as kicad_view
from server.models import KicadCategory, KicadPartDetail, KicadPartSummary, KicadRootResponse

router = APIRouter(prefix="/v1/kicad", tags=["kicad"])


def _fresh_conn(request: Request):
    """Refresh the cache (same rebuild-or-catchup path `GET /v1/parts` uses)
    then return the live sqlite connection -- kicad_view reads `parts` +
    the kicad_* tables directly, so the cache must be current first."""
    api = request.app.state.api
    api._load_organized()
    return api._get_cache()


@router.get("/", operation_id="kicad_root", response_model=KicadRootResponse)
def kicad_root(request: Request) -> dict:
    """Connection-check shape (design doc §1.1). Keys only matter."""
    return {"categories": "", "parts": ""}


@router.get(
    "/categories.json", operation_id="kicad_categories", response_model=list[KicadCategory],
)
def kicad_categories(request: Request) -> list[dict]:
    conn = _fresh_conn(request)
    return kicad_view.list_categories(conn)


@router.get(
    "/parts/category/{category_id}.json",
    operation_id="kicad_parts_by_category",
    response_model=list[KicadPartSummary],
)
def kicad_parts_by_category(request: Request, category_id: str) -> list[dict]:
    conn = _fresh_conn(request)
    return kicad_view.visible_parts_by_category(conn, category_id)


@router.get(
    "/parts/{part_id}.json", operation_id="kicad_part_detail", response_model=KicadPartDetail,
)
def kicad_part_detail(request: Request, part_id: str) -> dict:
    conn = _fresh_conn(request)
    detail = kicad_view.resolve_part_detail(conn, part_id)
    if detail is None:
        raise KeyError(f"KiCad part not found or not visible: {part_id!r}")
    return detail
