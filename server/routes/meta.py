from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/v1", tags=["meta"])


@router.get("/health", operation_id="health")
def health(response: Response) -> dict:
    # `Access-Control-Allow-Origin: *` on this one route, so a dubIS window
    # served from one origin can read another server's health and show whether
    # it is reachable (the server-picker dots in Preferences; js/server-probe.js).
    # Without the header the browser hides a perfectly good 200 behind a CORS
    # error, which is indistinguishable from the server being down.
    #
    # Safe to open where nothing else is: the payload is a constant, the route
    # is already unauthenticated (server/auth.py EXEMPT_PATHS), and `*` cannot
    # be used with credentials — a cookie- or token-bearing cross-origin read
    # is still rejected by the browser. Every other route stays same-origin.
    response.headers["Access-Control-Allow-Origin"] = "*"
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
