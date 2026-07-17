"""Auth layer: loopback trust + bearer tokens + tailnet allowlist header.

Configured entirely by env (12-factor; container-friendly), read once at
`create_app()` time (see server/app.py):

| Env                              | Meaning                                                        |
|-----------------------------------|-----------------------------------------------------------------|
| `DUBIS_AUTH_MODE`                  | `off` (default) or `on`                                        |
| `DUBIS_TOKENS`                     | `name:token,name2:token2` — bearer tokens with a stable identity|
| `DUBIS_TAILNET_ALLOWLIST`          | comma-separated tailnet logins trusted via the header below     |
| `DUBIS_TRUST_TAILSCALE_HEADER`     | `1` only when a tailscale proxy fronts the server (else ignored)|

Resolution order per request, when mode is `on`:
1. Loopback peer (`request.client.host` in `127.0.0.0/8`, `::1`) -> identity
   `local`, allowed.
2. `Authorization: Bearer <token>` matching `DUBIS_TOKENS` -> identity = the
   token's name.
3. Signed session cookie (set by `POST /v1/auth/session`) -> identity from
   the cookie.
4. `Tailscale-User-Login` header, when `DUBIS_TRUST_TAILSCALE_HEADER=1` and
   the login is in the allowlist -> identity = login.
5. Otherwise -> 401 `{error, code:"unauthorized", detail}`.

`off` mode: the middleware is never installed by `create_app` — zero
behavior change, no import-time cost either.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

COOKIE_NAME = "dubis_session"

# Paths that must answer without auth regardless of mode (k8s/probe traffic).
EXEMPT_PATHS = frozenset({"/v1/health"})


def _parse_tokens(raw: str) -> dict[str, str]:
    """Parse `name:token,name2:token2` into {token: name}."""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, sep, token = pair.partition(":")
        name = name.strip()
        token = token.strip()
        if not sep or not name or not token:
            raise ValueError(f"malformed DUBIS_TOKENS entry: {pair!r}")
        out[token] = name
    return out


def _parse_allowlist(raw: str) -> frozenset[str]:
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not a literal IP (e.g. a test-harness placeholder host) -> untrusted.
        return False


@dataclass
class AuthConfig:
    tokens_by_token: dict[str, str] = field(default_factory=dict)
    tailnet_allowlist: frozenset[str] = field(default_factory=frozenset)
    trust_tailscale_header: bool = False
    # Generated fresh per process: cookie sessions don't need to survive a
    # restart (the browser just re-bootstraps via POST /v1/auth/session).
    cookie_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))

    @classmethod
    def from_env(cls) -> AuthConfig:
        return cls(
            tokens_by_token=_parse_tokens(os.environ.get("DUBIS_TOKENS", "")),
            tailnet_allowlist=_parse_allowlist(os.environ.get("DUBIS_TAILNET_ALLOWLIST", "")),
            trust_tailscale_header=os.environ.get("DUBIS_TRUST_TAILSCALE_HEADER") == "1",
        )

    def sign_identity(self, identity: str) -> str:
        mac = hmac.new(self.cookie_secret, identity.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{identity}.{mac}"

    def verify_cookie(self, value: str) -> str | None:
        identity, sep, mac = value.rpartition(".")
        if not sep or not identity or not mac:
            return None
        expected = hmac.new(self.cookie_secret, identity.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, expected):
            return None
        return identity

    def lookup_token(self, token: str) -> str | None:
        """Constant-time bearer-token lookup.

        A plain `dict.get` short-circuits on the first differing byte of each
        key, which leaks timing information about how close a guess is to a
        valid token. Token counts here are tiny (operator-configured), so the
        cost of comparing against every entry with `hmac.compare_digest` is
        negligible — this keeps the bearer path timing-consistent with the
        cookie path above.
        """
        match = None
        for candidate, name in self.tokens_by_token.items():
            if hmac.compare_digest(candidate, token):
                match = name
        return match


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": "Authentication required", "code": "unauthorized", "detail": detail},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Installed by `create_app` only when `DUBIS_AUTH_MODE=on`.

    Gates every request (API routes AND the mounted static frontend, since
    both live on the same `app` this middleware wraps) except EXEMPT_PATHS.
    Resolved identity is stashed on `request.state.identity` for downstream
    handlers (e.g. mutation source-stamping in server/routes/inventory_mut.py).
    """

    def __init__(self, app, config: AuthConfig) -> None:
        super().__init__(app)
        self.config = config

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        identity = self._resolve(request)
        if identity is None:
            return _unauthorized("missing or invalid credentials")

        request.state.identity = identity
        return await call_next(request)

    def _resolve(self, request: Request) -> str | None:
        client = request.client
        if client is not None and _is_loopback(client.host):
            return "local"

        authz = request.headers.get("Authorization", "")
        scheme, _, param = authz.partition(" ")
        if scheme.lower() == "bearer":
            token = param.strip()
            name = self.config.lookup_token(token)
            if name is not None:
                return name

        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            identity = self.config.verify_cookie(cookie)
            if identity is not None:
                return identity

        if self.config.trust_tailscale_header:
            login = request.headers.get("Tailscale-User-Login")
            if login and login in self.config.tailnet_allowlist:
                return login

        return None


def set_session_cookie(response: Response, config: AuthConfig, identity: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        config.sign_identity(identity),
        httponly=True,
        samesite="lax",
    )


def stamp_source(request: Request, source: str) -> str:
    """Compose the mutation `source` field with the resolved caller identity.

    - `off` mode, or no middleware installed: `request.state` has no
      `identity` attribute at all -> source returned unchanged (byte-identical
      to today's behavior).
    - `on` mode, loopback caller (identity `local`): unchanged -- desktop
      ledger rows stay exactly as they are today.
    - `on` mode, any other identity: `{source}@{identity}` when the client
      supplied a source, else the bare identity.
    """
    identity = getattr(request.state, "identity", None)
    if not identity or identity == "local":
        return source
    if source:
        return f"{source}@{identity}"
    return identity
