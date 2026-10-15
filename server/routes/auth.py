"""Cookie-session bootstrap, plus the identity diagnostic.

`POST /v1/auth/session` is registered only when `DUBIS_AUTH_MODE=on` (see
server/app.py). A caller that already authenticated via bearer token (or is
loopback) exchanges that for an HttpOnly cookie so subsequent same-origin
browser requests (including the static frontend, gated by the same middleware)
don't need to attach an `Authorization` header.

`GET /v1/whoami` is registered in BOTH modes, because the question it answers
("what do you think I am, and why?") is exactly the one an operator asks when
the answer is wrong — including when the answer is "everyone is `local`", which
is what an auth-`off` server says. It is deliberately NOT in
`AuthMiddleware.EXEMPT_PATHS`: identity is per-caller information, so the route
is reachable only by a caller who already proved something. It is also not
cross-origin readable (no CORS header — see
`tests/python/server/test_health_cors.py`, which sweeps the whole surface), and
it is in `proxy.LOCAL_ONLY_PATHS`, so it always answers about the server that
received it rather than about whichever source a hub is pointed at.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from server import peercred
from server.auth import MECHANISM_AUTH_OFF, set_session_cookie

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# Its own router, included unconditionally by server/app.py, unlike `router`
# above which only exists when auth is on. A diagnostic that 404s precisely in
# the mode where identity is degenerate would be a poor diagnostic.
diagnostic_router = APIRouter(tags=["auth"])


class SessionResponse(BaseModel):
    identity: str


@router.post("/session", operation_id="create_auth_session")
def create_session(request: Request, response: Response) -> SessionResponse:
    identity = request.state.identity
    set_session_cookie(
        response, request.app.state.auth_config, identity,
        secure=request.url.scheme == "https",
    )
    return SessionResponse(identity=identity)


class WhoAmIResponse(BaseModel):
    """What this server concluded about the caller that asked."""

    identity: str | None = Field(
        None,
        description="The identity every mutation from this request is stamped with, "
        "or null when auth is off and no identity is resolved for anyone.",
    )
    mechanism: str = Field(
        description="How that identity was arrived at: `uds_peer_uid` (the kernel's "
        "SO_PEERCRED uid of the Unix-socket peer), `uds_proxy_hop` (the socket peer is a "
        "uid named in DUBIS_UDS_PROXY_UIDS, so its Tailscale-User-Login was believed "
        "instead), `loopback`, `bearer_token`, `session_cookie`, `tailnet_header`, or "
        "`auth_disabled`.",
    )
    detail: str = Field(description="One sentence naming the evidence behind `mechanism`.")
    auth_mode: str = Field(description="`on` or `off` — whether identity is resolved at all.")
    socket_uid: int | None = Field(
        None,
        description="The uid the kernel reported for the Unix-socket peer, or null when "
        "this request did not arrive over a Unix socket (or this platform cannot read it).",
    )


@diagnostic_router.get("/v1/whoami", response_model=WhoAmIResponse, operation_id="whoami")
def whoami(request: Request) -> WhoAmIResponse:
    """Report who this server thinks you are, and the evidence it used.

    Ask it of whichever server you care about, directly: over the Unix socket
    (`curl -s --unix-socket /run/dubis/dubis.sock http://x/v1/whoami`), over the
    tailnet, or with `DUBIS_URL=https://<host> scripts/dubis whoami`. A hub
    answers about itself and never forwards the question, so the answer always
    describes the connection you made, not a hop behind it.
    """
    identity = getattr(request.state, "identity", None)
    auth_on = getattr(request.app.state, "auth_config", None) is not None
    socket_uid = peercred.peer_uid_from_scope(request.scope)
    if not auth_on:
        return WhoAmIResponse(
            identity=None,
            mechanism=MECHANISM_AUTH_OFF,
            detail="DUBIS_AUTH_MODE is not `on`, so no identity is resolved for any "
                   "caller and every mutation is stamped with the source the client sent.",
            auth_mode="off",
            socket_uid=socket_uid,
        )
    return WhoAmIResponse(
        identity=identity,
        mechanism=str(getattr(request.state, "identity_mechanism", "unknown")),
        detail=str(getattr(request.state, "identity_detail", "")),
        auth_mode="on",
        socket_uid=socket_uid,
    )
