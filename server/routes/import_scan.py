"""Import/OCR + phone-scan-session /v1 routes.

Pure read/parse operations — no cache mutation, so no `finish_mutation`/publish
here (imported rows still have to go through `/v1/purchases/import` to land in
inventory). `start_scan_session` is the one route with a failure mode worth a
dedicated error code: it needs the in-process PnP HTTP server running (started
by `app.pyw`, stored on the facade as `api._pnp_server`), which a server-only
deployment may not have. That's checked directly here (rather than letting
`ScanFacade.start_scan_session`'s `RuntimeError` bubble to the generic 500
`dubis_error` mapping) so callers get a stable `pnp_server_unavailable` code.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/v1", tags=["import", "scan"])


# ── Body models ─────────────────────────────────────────────────────────────


class ParseImportBody(BaseModel):
    file_b64: str = ""
    file_name: str = ""
    template: str = "generic"
    path: str = ""


class OcrOverlayBody(BaseModel):
    file_b64: str
    file_name: str
    template: str = "generic"


class MatchPartBody(BaseModel):
    mpn: str
    manufacturer: str = ""


class DetectColumnsBody(BaseModel):
    headers: list[str]


class StartScanSessionBody(BaseModel):
    template: str = "generic"


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("/import/parse", operation_id="parse_import_source")
def parse_import_source(request: Request, body: ParseImportBody) -> list:
    api = request.app.state.api
    if body.path:
        return api.parse_source_file(body.path, body.template)
    return api.parse_source_file_b64(body.file_b64, body.file_name, body.template)


@router.post("/import/ocr", operation_id="ocr_overlay")
def ocr_overlay(request: Request, body: OcrOverlayBody) -> dict:
    api = request.app.state.api
    return api.ocr_overlay_b64(body.file_b64, body.file_name, body.template)


@router.get("/import/ocr/available", operation_id="ocr_engine_available")
def ocr_engine_available(request: Request) -> dict:
    api = request.app.state.api
    return {"available": api.ocr_engine_available()}


@router.post("/import/match-part", operation_id="match_part")
def match_part(request: Request, body: MatchPartBody) -> dict:
    api = request.app.state.api
    return api.match_part(body.mpn, body.manufacturer)


@router.post("/import/detect-columns", operation_id="detect_columns")
def detect_columns(request: Request, body: DetectColumnsBody) -> dict:
    api = request.app.state.api
    return api.detect_columns(body.headers)


@router.post("/scan/sessions", operation_id="start_scan_session")
def start_scan_session(request: Request, body: StartScanSessionBody) -> dict:
    api = request.app.state.api
    if getattr(api, "_pnp_server", None) is None:
        return JSONResponse(
            status_code=409,
            content={
                "error": "Phone-scan server is not running; cannot start a scan session.",
                "code": "pnp_server_unavailable",
                "detail": None,
            },
        )
    return api.start_scan_session(body.template)
