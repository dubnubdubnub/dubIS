"""Cookie-session bootstrap for browser clients.

Registered only when `DUBIS_AUTH_MODE=on` (see server/app.py). A caller that
already authenticated via bearer token (or is loopback) exchanges that for an
HttpOnly cookie so subsequent same-origin browser requests (including the
static frontend, gated by the same middleware) don't need to attach an
`Authorization` header.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from server.auth import set_session_cookie

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class SessionResponse(BaseModel):
    identity: str


@router.post("/session", operation_id="create_auth_session")
def create_session(request: Request, response: Response) -> SessionResponse:
    identity = request.state.identity
    set_session_cookie(response, request.app.state.auth_config, identity)
    return SessionResponse(identity=identity)
