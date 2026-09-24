"""Auth layer: Unix-socket peer credentials + loopback trust + bearer tokens
+ tailnet allowlist header.

Configured entirely by env (12-factor; container-friendly), read once at
`create_app()` time (see server/app.py):

| Env                              | Meaning                                                        |
|-----------------------------------|-----------------------------------------------------------------|
| `DUBIS_AUTH_MODE`                  | `off` (default) or `on`                                        |
| `DUBIS_TOKENS`                     | `name:token,name2:token2` — bearer tokens with a stable identity|
| `DUBIS_TAILNET_ALLOWLIST`          | comma-separated tailnet logins trusted via the header below     |
| `DUBIS_TRUST_TAILSCALE_HEADER`     | `1` only when a tailscale proxy fronts the server (else ignored)|
| `DUBIS_TRUSTED_PROXY_IPS`          | comma-separated IPs/CIDRs allowed to assert the header below     |
| `DUBIS_TRUSTED_PROXY_HOSTS`        | comma-separated DNS names whose current A/AAAA records may too   |

Resolution order per request, when mode is `on`:
0. Unix-domain-socket peer (`python -m server --uds <path>`) -> identity is
   the connecting user's *username*, straight from the kernel's
   `SO_PEERCRED` — see server/peercred.py and server/uds.py. This is what
   makes `ssh -L <port>:/path/to.sock` attribute each teammate's mutations
   to them instead of collapsing every one into `local`. Unforgeable and
   secret-free: the uid comes from the OS, not from the request.
1. Loopback peer (`request.client.host` in `127.0.0.0/8`, `::1`) -> identity
   `local`, allowed.
2. `Authorization: Bearer <token>` or `Authorization: Token <token>` (the
   latter accepted for KiCad's HTTP library client, which uses the DRF
   `TokenAuthentication` convention) matching `DUBIS_TOKENS` -> identity =
   the token's name.
3. Signed session cookie (set by `POST /v1/auth/session`) -> identity from
   the cookie.
4. `Tailscale-User-Login` header, when ALL of: `DUBIS_TRUST_TAILSCALE_HEADER=1`,
   the request's peer IP (`request.client.host`) is a trusted proxy, and the
   login is in the allowlist -> identity = login. A peer is a trusted proxy
   when it is within one of the `DUBIS_TRUSTED_PROXY_IPS` networks OR is one
   of the addresses a `DUBIS_TRUSTED_PROXY_HOSTS` name currently resolves to
   (anything else, e.g. another in-cluster pod hitting the ClusterIP
   directly, could otherwise forge this header).

   Prefer HOSTS in a cluster: the tailscale operator's proxy is a pod whose
   IP churns on every restart, so a pinned IP silently turns tailnet login
   into 401s. The operator also creates a headless Service for the proxy
   StatefulSet (`ts-<ingress>-<suffix>.tailscale.svc.cluster.local`) that
   always resolves to the current proxy pod, and only the operator can
   create Services in its namespace. See `TrustedProxyHosts` for the cache
   (30s TTL; a miss re-resolves at most once per 5s, so a forged-header
   flood cannot become a DNS flood).

   Fail-safe: if trust is `1` but both lists are empty/unset, the header is
   never honored (one `logging.warning` at config load, not per-request). A
   name that fails to resolve trusts nothing (rate-limited warning). A header
   from an untrusted peer is ignored with a rate-limited warning naming the
   peer and what every configured name currently resolves to.
5. Otherwise -> 401 `{error, code:"unauthorized", detail}`.

`off` mode: the middleware is never installed by `create_app` — zero
behavior change, no import-time cost either.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from server import peercred

logger = logging.getLogger(__name__)

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Resolves one DNS name to every address it currently has (A and AAAA).
# Raises OSError (socket.gaierror for NXDOMAIN / no answer) on failure.
Resolver = Callable[[str], Awaitable[frozenset[IpAddress]]]

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


def _parse_trusted_proxies(raw: str) -> tuple[IpNetwork, ...]:
    """Parse `DUBIS_TRUSTED_PROXY_IPS` (`10.42.2.176,10.42.0.0/16`) into
    `ip_network` objects. A bare IP (no `/`) is treated as a single-host
    network (`/32` or `/128`) via `strict=False`."""
    nets: list[IpNetwork] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        nets.append(ipaddress.ip_network(part, strict=False))
    return tuple(nets)


def _is_trusted_proxy(host: str | None, networks: tuple[IpNetwork, ...]) -> bool:
    if not host or not networks:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP (e.g. a test-harness placeholder host) -> untrusted.
        return False
    return any(addr in net for net in networks)


_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)


def _parse_trusted_proxy_hosts(raw: str) -> tuple[str, ...]:
    """Parse `DUBIS_TRUSTED_PROXY_HOSTS` into DNS names.

    Fail-loud like `_parse_trusted_proxies`: an IP or CIDR here is almost
    certainly a value meant for `DUBIS_TRUSTED_PROXY_IPS`, and treating it as
    a name would resolve to nothing and quietly trust nothing."""
    hosts: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            pass
        else:
            raise ValueError(
                f"DUBIS_TRUSTED_PROXY_HOSTS entry {part!r} is an IP/CIDR, not a DNS name -- "
                "put it in DUBIS_TRUSTED_PROXY_IPS instead"
            )
        if not _HOSTNAME_RE.match(part):
            raise ValueError(f"malformed DUBIS_TRUSTED_PROXY_HOSTS entry: {part!r}")
        hosts.append(part.lower())
    return tuple(hosts)


def _parse_ip(host: str | None) -> IpAddress | None:
    if not host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


async def system_resolver(host: str) -> frozenset[IpAddress]:
    """A and AAAA for *host* via the event loop's `getaddrinfo`, which runs
    the blocking libc call in the default executor rather than on the loop."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    out: set[IpAddress] = set()
    for _family, _type, _proto, _canon, sockaddr in infos:
        # IPv6 link-local answers can carry a `%scope` suffix.
        addr = _parse_ip(str(sockaddr[0]).split("%", 1)[0])
        if addr is not None:
            out.add(addr)
    if not out:
        raise socket.gaierror(f"{host} resolved to no usable addresses")
    return frozenset(out)


class _RateLimit:
    """True at most once per *interval* seconds per key."""

    def __init__(self, interval: float, clock: Callable[[], float]) -> None:
        self.interval = interval
        self.clock = clock
        self._last: dict[str, float] = {}

    def ready(self, key: str) -> bool:
        now = self.clock()
        last = self._last.get(key)
        if last is not None and now - last < self.interval:
            return False
        self._last[key] = now
        return True


class TrustedProxyHosts:
    """Cached DNS resolution of `DUBIS_TRUSTED_PROXY_HOSTS`.

    - Each name's addresses are cached for `ttl` seconds; the first check
      after expiry re-resolves every name.
    - A peer that is not in the cache triggers one early re-resolution (the
      proxy pod may have just moved), but at most once per `miss_cooldown`
      seconds, so a flood of forged headers from an untrusted peer costs at
      most one DNS round per cooldown, not one per request.
    - A name that fails to resolve (NXDOMAIN, timeout, empty answer) maps to
      NO addresses -- fail closed, never open -- and logs a warning at most
      once per `warn_interval` per name.

    No lock: the attempt timestamp is stamped *before* awaiting DNS, so
    concurrent requests arriving mid-refresh see a fresh stamp and use the
    current cache rather than piling on further lookups. (An asyncio.Lock
    binds to one event loop, and the tests run several.)
    """

    def __init__(
        self,
        hosts: tuple[str, ...],
        *,
        resolver: Resolver | None = None,
        ttl: float = 30.0,
        miss_cooldown: float = 5.0,
        warn_interval: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hosts = hosts
        self.resolver: Resolver = resolver or system_resolver
        self.ttl = ttl
        self.miss_cooldown = miss_cooldown
        self.clock = clock
        self._addrs: dict[str, frozenset[IpAddress]] = {h: frozenset() for h in hosts}
        self._errors: dict[str, str] = {}
        self._resolved_at: float | None = None  # last attempt, success or not
        self._warn = _RateLimit(warn_interval, clock)

    def __bool__(self) -> bool:
        return bool(self.hosts)

    async def refresh(self) -> None:
        self._resolved_at = self.clock()
        for host in self.hosts:
            try:
                addrs = frozenset(await self.resolver(host))
            except Exception as exc:  # noqa: BLE001 -- any failure must fail closed, never crash auth
                self._addrs[host] = frozenset()
                self._errors[host] = f"{type(exc).__name__}: {exc}"
                if self._warn.ready(f"resolve:{host}"):
                    logger.warning(
                        "DUBIS_TRUSTED_PROXY_HOSTS: could not resolve %r (%s) -- it trusts NO "
                        "peer until it resolves again, so Tailscale-User-Login from the proxy "
                        "behind it will 401. Check the name with `kubectl -n tailscale get svc`.",
                        host, self._errors[host],
                    )
                continue
            self._addrs[host] = addrs
            self._errors.pop(host, None)

    async def contains(self, addr: IpAddress) -> bool:
        if not self.hosts:
            return False
        now = self.clock()
        if self._resolved_at is None or now - self._resolved_at >= self.ttl:
            await self.refresh()
        elif not self._matches(addr) and now - self._resolved_at >= self.miss_cooldown:
            await self.refresh()
        return self._matches(addr)

    def _matches(self, addr: IpAddress) -> bool:
        return any(addr in addrs for addrs in self._addrs.values())

    def describe(self) -> str:
        """`name -> [addrs]` (or the resolution error) for diagnostic logs."""
        parts = []
        for host in self.hosts:
            if host in self._errors:
                parts.append(f"{host} -> unresolved ({self._errors[host]})")
            else:
                addrs = ", ".join(sorted(str(a) for a in self._addrs[host])) or "nothing"
                parts.append(f"{host} -> [{addrs}]")
        return "; ".join(parts)


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
    trusted_proxy_ips: tuple[IpNetwork, ...] = field(default_factory=tuple)
    trusted_proxy_hosts: TrustedProxyHosts = field(default_factory=lambda: TrustedProxyHosts(()))
    # Generated fresh per process: cookie sessions don't need to survive a
    # restart (the browser just re-bootstraps via POST /v1/auth/session).
    cookie_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))

    # Rate limit for the "header from an untrusted peer" diagnosis.
    untrusted_warn_interval: float = 60.0

    def __post_init__(self) -> None:
        self._untrusted_warn = _RateLimit(self.untrusted_warn_interval, time.monotonic)

    @classmethod
    def from_env(cls, *, resolver: Resolver | None = None) -> AuthConfig:
        trust_tailscale_header = os.environ.get("DUBIS_TRUST_TAILSCALE_HEADER") == "1"
        trusted_proxy_ips = _parse_trusted_proxies(os.environ.get("DUBIS_TRUSTED_PROXY_IPS", ""))
        trusted_proxy_hosts = _parse_trusted_proxy_hosts(
            os.environ.get("DUBIS_TRUSTED_PROXY_HOSTS", ""),
        )
        if trust_tailscale_header and not trusted_proxy_ips and not trusted_proxy_hosts:
            logger.warning(
                "DUBIS_TRUST_TAILSCALE_HEADER=1 but DUBIS_TRUSTED_PROXY_IPS and "
                "DUBIS_TRUSTED_PROXY_HOSTS are both unset/empty -- the Tailscale-User-Login "
                "header will NOT be honored (fail-safe) until a trusted proxy is configured "
                "(prefer DUBIS_TRUSTED_PROXY_HOSTS=<the operator's headless proxy Service>)."
            )
        return cls(
            tokens_by_token=_parse_tokens(os.environ.get("DUBIS_TOKENS", "")),
            tailnet_allowlist=_parse_allowlist(os.environ.get("DUBIS_TAILNET_ALLOWLIST", "")),
            trust_tailscale_header=trust_tailscale_header,
            trusted_proxy_ips=trusted_proxy_ips,
            trusted_proxy_hosts=TrustedProxyHosts(trusted_proxy_hosts, resolver=resolver),
        )

    async def is_trusted_proxy(self, host: str | None) -> bool:
        """Is *host* (the peer IP) allowed to assert Tailscale-User-Login?"""
        if _is_trusted_proxy(host, self.trusted_proxy_ips):
            return True
        addr = _parse_ip(host)
        if addr is None:
            return False
        return await self.trusted_proxy_hosts.contains(addr)

    def warn_untrusted_header(self, host: str | None) -> None:
        """Rate-limited diagnosis for a header that arrived from the wrong
        peer -- almost always a proxy that moved to an IP the config does not
        name, which otherwise looks like nothing but a wall of 401s."""
        if not self.trusted_proxy_ips and not self.trusted_proxy_hosts:
            return  # already warned once at config load
        if not self._untrusted_warn.ready("untrusted"):
            return
        ips = ", ".join(str(n) for n in self.trusted_proxy_ips) or "(none)"
        hosts = self.trusted_proxy_hosts.describe() or "(none)"
        logger.warning(
            "Ignoring Tailscale-User-Login from untrusted peer %s: it is not a trusted proxy "
            "(DUBIS_TRUSTED_PROXY_IPS: %s; DUBIS_TRUSTED_PROXY_HOSTS: %s). If %s is the "
            "tailscale operator proxy, the configured proxy is stale -- point "
            "DUBIS_TRUSTED_PROXY_HOSTS at its headless Service. (Rate-limited: at most one "
            "such warning per %gs.)",
            host or "(unknown)", ips, hosts, host or "(unknown)", self.untrusted_warn_interval,
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

        identity = await self._resolve(request)
        if identity is None:
            return _unauthorized("missing or invalid credentials")

        request.state.identity = identity
        return await call_next(request)

    async def _resolve(self, request: Request) -> str | None:
        # 0. Unix-socket peer credentials. FIRST, and for two reasons.
        #
        # Correctness: `request.client` is None for a UDS connection (uvicorn
        # only fills it from a `(host, port)` peername, which AF_UNIX never
        # has), so the loopback branch below cannot match and the request
        # would fall through token/cookie/tailscale to a 401 — the whole
        # feature would look like a broken server. See
        # tests/python/server/test_peercred.py::test_uds_scope_does_not_401.
        #
        # Precedence: the kernel's answer is the strongest evidence available
        # here. It is supplied by the OS rather than the wire, so nothing a
        # caller can send should be able to override it with a different
        # identity.
        #
        # This branch is reachable ONLY through `server/uds.py`, which sets
        # the scope-state key exclusively for AF_UNIX transports — a TCP
        # client cannot get here regardless of what it sends.
        peer = peercred.identity_from_scope(request.scope)
        if peer is not None:
            return peer

        client = request.client
        if client is not None and _is_loopback(client.host):
            return "local"

        authz = request.headers.get("Authorization", "")
        scheme, _, param = authz.partition(" ")
        if scheme.lower() in ("bearer", "token"):
            token = param.strip()
            name = self.config.lookup_token(token)
            if name is not None:
                return name

        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            identity = self.config.verify_cookie(cookie)
            if identity is not None:
                return identity

        login = request.headers.get("Tailscale-User-Login")
        if self.config.trust_tailscale_header and login:
            # Checked only when the header is present, so ordinary untrusted
            # traffic never costs a DNS lookup.
            peer_host = client.host if client is not None else None
            if not await self.config.is_trusted_proxy(peer_host):
                self.config.warn_untrusted_header(peer_host)
            elif login in self.config.tailnet_allowlist:
                return login

        return None


def set_session_cookie(
    response: Response, config: AuthConfig, identity: str, *, secure: bool = False,
) -> None:
    """`secure` should be `request.url.scheme == "https"` from the caller.

    Unconditional `secure=True` was tried first but breaks
    `test_cookie_session_flow`: Starlette's `TestClient` (httpx under the
    hood) talks to `http://testserver`, and httpx's cookie jar -- like any
    real browser -- refuses to attach a Secure cookie back to a plain-http
    request, so the follow-up `GET /v1/parts` in that test would come back
    401 even though the cookie was set correctly. Making it scheme-conditional
    keeps local/test traffic (http, `DUBIS_AUTH_MODE=off` by default anyway)
    working unchanged while the real deployment -- always reached over the
    tailscale ingress's HTTPS -- gets the Secure attribute.
    """
    response.set_cookie(
        COOKIE_NAME,
        config.sign_identity(identity),
        httponly=True,
        samesite="lax",
        secure=secure,
    )


class LoopbackRequiredError(Exception):
    """Raised by `require_loopback` when a filesystem-path-accepting route is
    called by a resolved identity other than `local`.

    Mapped to 403 `{error, code:"loopback_only", detail}` by
    `server/errors.py::register_handlers` -- see design doc
    `docs/plans/2026-07-16-phase1c-remote-deploy-design.md` §3.
    """


def require_loopback(request: Request) -> None:
    """Gate a route (or a branch of one) that reads from the server's own
    filesystem, e.g. `/v1/import/parse`'s `path` field.

    - No `request.state.identity` attribute at all (auth `off` mode, or the
      route is exempt from `AuthMiddleware`) -> everything is loopback by
      definition -> allowed, unchanged from today's behavior.
    - Identity `local` (loopback peer resolved by `AuthMiddleware` in `on`
      mode) -> allowed.
    - Any other identity (bearer token, cookie session, tailnet header) ->
      raises `LoopbackRequiredError`, regardless of whether that identity is
      otherwise fully authenticated -- a remote caller must never read
      arbitrary paths off the server's disk.
    """
    identity = getattr(request.state, "identity", None)
    if identity is not None and identity != "local":
        raise LoopbackRequiredError(
            "This operation reads a file from the server's local disk and is "
            "only available to loopback callers."
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
