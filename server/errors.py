"""DubISError → HTTP mapping. Body contract: {"error", "code", "detail"}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from dubis_errors import (
    CacheError,
    DistributorAuthError,
    DistributorError,
    DistributorTimeout,
    DubISError,
    PartRegistryCollisionError,
)
from server.auth import LoopbackRequiredError

logger = logging.getLogger(__name__)

_MAPPING: list[tuple[type[Exception], int, str]] = [
    # order matters: subclasses before bases
    (LoopbackRequiredError, 403, "loopback_only"),
    (PartRegistryCollisionError, 409, "part_registry_collision"),
    (DistributorAuthError, 401, "distributor_auth"),
    (DistributorTimeout, 504, "distributor_timeout"),
    (DistributorError, 502, "distributor_error"),
    (CacheError, 500, "cache_error"),
    (DubISError, 500, "dubis_error"),
    (KeyError, 404, "not_found"),
    (ValueError, 400, "value_error"),
]


def _body(exc: Exception, code: str) -> dict:
    return {"error": str(exc) or exc.__class__.__name__, "code": code, "detail": None}


def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Framework-raised HTTPException -> the same {error, code, detail} shape
    as every hand-written error.

    Covers the unknown-route case (Starlette's router raises this with
    status 404 and detail "Not Found" when nothing matches) and any other
    status a route explicitly raises via `raise HTTPException(...)` (none do
    today, but this keeps the contract uniform if one ever does).
    """
    code = "not_found" if exc.status_code == 404 else "http_error"
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else code
    logger.warning("/v1 %s -> %s: %s", request.url.path, code, message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": message, "code": code, "detail": None},
    )


def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic request-validation failure -> {error, code:"validation_error",
    detail:<field errors>}. `detail` carries the pydantic error list
    (jsonable-encoded, since some error `ctx` values aren't natively JSON
    serializable) so callers can see exactly which field(s) failed and why."""
    errors = jsonable_encoder(exc.errors())
    logger.warning("/v1 %s -> validation_error: %s", request.url.path, errors)
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "code": "validation_error", "detail": errors},
    )


def register_handlers(app: FastAPI) -> None:
    for exc_type, status, code in _MAPPING:
        def handler(request: Request, exc: Exception,
                    _status=status, _code=code):
            logger.warning("/v1 %s -> %s: %s", request.url.path, _code, exc)
            return JSONResponse(status_code=_status, content=_body(exc, _code))
        app.add_exception_handler(exc_type, handler)

    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
