"""DubISError → HTTP mapping. Body contract: {"error", "code", "detail"}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dubis_errors import (
    CacheError,
    DistributorAuthError,
    DistributorError,
    DistributorTimeout,
    DubISError,
    PartRegistryCollisionError,
)

logger = logging.getLogger(__name__)

_MAPPING: list[tuple[type[Exception], int, str]] = [
    # order matters: subclasses before bases
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


def register_handlers(app: FastAPI) -> None:
    for exc_type, status, code in _MAPPING:
        def handler(request: Request, exc: Exception,
                    _status=status, _code=code):
            logger.warning("/v1 %s -> %s: %s", request.url.path, _code, exc)
            return JSONResponse(status_code=_status, content=_body(exc, _code))
        app.add_exception_handler(exc_type, handler)
