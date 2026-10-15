from __future__ import annotations

from fastapi import APIRouter, Request, Response

from domain import thresholds

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

    # `default_thresholds` and `base_threshold` travel here because a client
    # that judges stock needs the same number the inventory grid colours with,
    # and this is the route that already answers "what are this server's
    # categories". `dubis low-stock` reads them from here rather than carrying
    # its own copy of the table — see domain/thresholds.py for why a third
    # default is the bug this closes.
    #
    # `threshold_presets` travels here too, and deliberately NOT through
    # /v1/preferences: savePreferences posts the whole in-memory bag, so
    # anything served through preferences persists into preferences.json as
    # user state on the next unrelated save (js/store.js documents this).
    # Presets are read-only server facts — the user's *choice* of preset id
    # and their sparse deltas are what lives in preferences, never the
    # resolved table.
    return {
        "schema_version": cache_db.SCHEMA_VERSION,
        "section_order": api.SECTION_ORDER,
        "flat_section_order": api.FLAT_SECTION_ORDER,
        "default_thresholds": api.DEFAULT_THRESHOLDS,
        "base_threshold": thresholds.BASE_THRESHOLD,
        "threshold_presets": thresholds.presets_payload(),
    }
