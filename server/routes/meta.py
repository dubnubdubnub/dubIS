from __future__ import annotations

from fastapi import APIRouter, Request, Response

from domain import thresholds
from server.build_identity import get_build_sha
from server.models import HealthResponse

router = APIRouter(prefix="/v1", tags=["meta"])


@router.get("/health", operation_id="health", response_model=HealthResponse)
def health(response: Response) -> dict:
    # `Access-Control-Allow-Origin: *` on this one route, so a dubIS window
    # served from one origin can read another server's health and show whether
    # it is reachable (the server-picker dots in Preferences; js/server-probe.js).
    # Without the header the browser hides a perfectly good 200 behind a CORS
    # error, which is indistinguishable from the server being down.
    #
    # Safe to open where nothing else is: the payload is a per-process
    # constant (liveness + build id), never user data, the route
    # is already unauthenticated (server/auth.py EXEMPT_PATHS), and `*` cannot
    # be used with credentials — a cookie- or token-bearing cross-origin read
    # is still rejected by the browser. Every other route stays same-origin.
    #
    # `build_sha` says which build is answering, so verifying a fremont roll
    # is one curl instead of six cluster commands. It reports THE ANSWERING
    # HUB's build, not the active source's — and that is correct, not a bug
    # to "fix" later: /v1/health is in proxy.LOCAL_ONLY_PATHS (never
    # proxied) and auth.EXEMPT_PATHS (no token needed to ask), which is what
    # makes it a safe per-hub liveness+identity probe. A per-source build
    # question belongs on a proxied route, not here.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return {"ok": True, "build_sha": get_build_sha()}


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
