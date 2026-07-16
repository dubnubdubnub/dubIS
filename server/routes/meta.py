from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/v1", tags=["meta"])


@router.get("/health", operation_id="health")
def health() -> dict:
    return {"ok": True}


@router.get("/meta", operation_id="meta")
def meta(request: Request) -> dict:
    api = request.app.state.api
    import cache_db  # noqa: PLC0415

    return {
        "schema_version": cache_db.SCHEMA_VERSION,
        "section_order": api.SECTION_ORDER,
        "flat_section_order": api.FLAT_SECTION_ORDER,
    }
