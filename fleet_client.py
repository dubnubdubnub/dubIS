"""Client for the model-fleet registry — discover a vision model, lease it politely.

The infra repo runs a purpose-built registry (`fleet-registry`, namespace
`fleet`) whose whole job is that any tailnet agent can *discover* GPU capacity
and *lease* it. dubIS's picture/PDF reader is one such agent: in `remote` (and
`auto`-falling-back) reader mode it asks the registry for a model that can see,
then dials that model's own OpenAI-compatible `/v1` endpoint — the dialling and
the inference belong to `vlm_extract`, not here. This module only answers "which
endpoint, and may I use it".

**Leases are cooperative hints, not a mutex.** The registry is stateless: a
restart drops every lease, and nothing stops another agent from ignoring yours.
The real serialization is the node's own request queue — llama.cpp processes one
request at a time whether or not anybody leased anything. So:

  * Holding a lease is *not* a guarantee of exclusivity. Never build a critical
    section, a "safe to load a second model" decision, or any correctness
    argument on top of one.
  * A `LeaseConflictError` is information ("someone else is using this, and here
    is who"), not a lock failure to spin on. Back off, pick another entry, or
    proceed anyway if the work is cheap — all three are legitimate.
  * Failing to acquire, renew, or release is never fatal. `renew()`/`release()`
    therefore return `False` instead of raising: a lease you cannot release
    simply expires on its own TTL.

Registry base URL, in precedence order:
    1. the `base_url` constructor argument;
    2. `DUBIS_FLEET_URL` (mirrors `vlm_extract`'s `DUBIS_VLM_URL`);
    3. `http://fleet-registry.fleet.svc.cluster.local` when we appear to be
       running inside the cluster, else `https://fleet.miku-parore.ts.net`.

Reachability matters, because an advertised endpoint is not universally
dialable: an in-cluster node advertises `http://<svc>.<ns>.svc.cluster.local:<port>`,
which resolves nowhere off-cluster. Entries carry `path`/`location` saying so,
and `FleetModel.is_in_cluster` falls back to sniffing the hostname when a node
omits them. `discover()` drops in-cluster entries unless `can_reach_cluster`
(auto-detected from `KUBERNETES_SERVICE_HOST`, overridable by argument or
`DUBIS_FLEET_IN_CLUSTER`) — handing back an unreachable URL would just move the
failure somewhere less legible.

Public API:
    FleetClient(base_url=None, can_reach_cluster=None)
        .base_url                       -> resolved registry root
        .can_reach_cluster              -> bool
        .discover(need_caps=["vision"]) -> FleetModel | None  (top-ranked usable)
        .advertise(need_caps=None, include_all=False) -> list[FleetModel]
        .acquire_lease(model, holder, ttl_s, node=None) -> Lease
        .close()                        -> release every lease still held
    Lease: .renew(ttl_s=None) -> bool, .release() -> bool, context manager.
    LeaseConflictError (409, names the current holder), FleetUnavailableError.

Everything is stdlib `urllib` on purpose, matching `vlm_extract`: a short probe
timeout so a missing registry costs ~nothing, and no new dependency for one
JSON GET.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import weakref
from dataclasses import dataclass, field

from dubis_errors import DubISError

logger = logging.getLogger(__name__)

#: The registry on the tailnet — reachable from any tailnet member.
TAILNET_REGISTRY_URL = "https://fleet.miku-parore.ts.net"
#: The same registry from inside the cluster (no tailnet round trip).
IN_CLUSTER_REGISTRY_URL = "http://fleet-registry.fleet.svc.cluster.local"

_ENV_URL = "DUBIS_FLEET_URL"
_ENV_IN_CLUSTER = "DUBIS_FLEET_IN_CLUSTER"

# Discovery is on the critical path of an import the user is watching, so the
# registry gets a short leash; lease calls get a little more since they mutate.
_PROBE_TIMEOUT = 3.0
_LEASE_TIMEOUT = 5.0
_DEFAULT_TTL_S = 300

# States the registry may report. `GET /fleet` already excludes unhealthy and
# stale entries, so this is belt-and-braces: a deny-list rather than an
# allow-list, so an unfamiliar-but-fine state (say "warming") is not silently
# discarded by a client that is deliberately not the authority on ranking.
_BAD_STATES = frozenset({"unhealthy", "stale", "down", "offline", "dead",
                         "error", "draining", "unknown"})

_CLUSTER_WORDS = frozenset({"cluster", "in-cluster", "incluster", "in_cluster",
                            "internal", "clusterip"})
_NON_CLUSTER_WORDS = frozenset({"tailnet", "tailscale", "lan", "public",
                                "loopback", "local", "localhost", "host"})
_CLUSTER_HOST_SUFFIXES = (".svc.cluster.local", ".svc")

# Keys a 409 body might name the current holder under, and a last-resort scrape
# of a human-readable `detail` string ("... is leased by holder ci-runner ...").
_HOLDER_KEYS = ("holder", "current_holder", "held_by", "owner", "lessee")
_HOLDER_IN_TEXT = re.compile(
    # The optional second "holder" absorbs "... is leased by holder ci-runner",
    # where the naive pattern captures the word "holder" instead of the name.
    r"(?:holder|held by|leased by|owned by)\W{0,4}(?:holder\W{0,4})?"
    r"([A-Za-z0-9][\w.@:+-]*)",
    re.IGNORECASE)

# Live clients, so a clean interpreter exit hands capacity back rather than
# leaving other agents to wait out our TTL. Weak, so a garbage-collected client
# is not resurrected at exit.
_LIVE_CLIENTS: weakref.WeakSet = weakref.WeakSet()


class FleetError(DubISError):
    """Base for fleet-registry failures."""


class FleetUnavailableError(FleetError):
    """The registry could not be reached, or answered something unusable.

    Raised only on the lease path. Discovery answers `None` instead: a missing
    registry means "no remote reader", which every caller must already handle.
    """


class LeaseConflictError(FleetError):
    """`POST /leases` returned 409 — someone else holds this model.

    Carries the named holder (and expiry, when the registry supplies it) so the
    caller can say who, instead of a bare "conflict". Remember the module
    docstring: this is advisory information, not a lock failure. `holder` is
    `""` when the registry's body did not name one.
    """

    def __init__(self, message: str, *, model: str = "", holder: str = "",
                 node: str = "", expires_at: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.model = model
        self.holder = holder
        self.node = node
        self.expires_at = expires_at
        self.retry_after = retry_after


@dataclass(frozen=True)
class FleetModel:
    """One advertised model: what to dial, and whether we can dial it."""

    model: str
    node: str
    endpoint: str
    capabilities: tuple[str, ...] = ()
    ctx_max: int | None = None
    state: str = ""
    path: str = ""
    location: str = ""
    health: object = None
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_in_cluster(self) -> bool:
        """True when `endpoint` only resolves from inside the cluster.

        `path`/`location` are authoritative when the node sets them; otherwise
        the hostname decides, so a node that omits both is still classified.
        """
        for hint in (self.path, self.location):
            if hint in _CLUSTER_WORDS:
                return True
            if hint in _NON_CLUSTER_WORDS:
                return False
        host = (urllib.parse.urlsplit(self.endpoint).hostname or "").lower()
        return host.endswith(_CLUSTER_HOST_SUFFIXES)

    def has_caps(self, need_caps) -> bool:
        """True when every required capability is advertised. An entry that
        advertises no capabilities at all is not rejected — the registry already
        filtered on `need_caps`, so an empty list means "did not say", and
        second-guessing the authority would drop a usable node."""
        if not need_caps or not self.capabilities:
            return True
        wanted = {str(c).strip().lower() for c in need_caps if str(c).strip()}
        return wanted.issubset(set(self.capabilities))


@dataclass
class Lease:
    """A cooperative hold on one fleet model. Not a mutex — see the module docstring."""

    id: str
    model: str
    holder: str
    node: str = ""
    ttl_s: int = _DEFAULT_TTL_S
    expires_at: str = ""
    released: bool = False
    _client: "FleetClient | None" = field(default=None, repr=False, compare=False)

    def renew(self, ttl_s: int | None = None) -> bool:
        """Extend the lease. False (logged) if it is already released or the
        registry did not answer — an un-renewed lease just lapses."""
        if self.released or self._client is None:
            return False
        return self._client._renew(self, ttl_s)

    def release(self) -> bool:
        """Hand the model back. Idempotent: True only for the call that actually
        issued the `DELETE`, False for a repeat or a failed one. Never raises —
        this runs on shutdown paths."""
        if self.released or self._client is None:
            return False
        return self._client._release(self)

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *_exc) -> bool:
        self.release()
        return False


class FleetClient:
    """Stdlib HTTP client for the fleet registry."""

    def __init__(self, base_url: str | None = None, *,
                 can_reach_cluster: bool | None = None,
                 probe_timeout: float = _PROBE_TIMEOUT,
                 lease_timeout: float = _LEASE_TIMEOUT):
        self._base_url = base_url.rstrip("/") if base_url else None
        self._can_reach_cluster = can_reach_cluster
        self.probe_timeout = probe_timeout
        self.lease_timeout = lease_timeout
        self._leases: dict[str, Lease] = {}
        _LIVE_CLIENTS.add(self)

    # -- configuration -----------------------------------------------------

    @property
    def can_reach_cluster(self) -> bool:
        """Whether `*.svc.cluster.local` endpoints resolve for this process."""
        if self._can_reach_cluster is not None:
            return self._can_reach_cluster
        override = (os.environ.get(_ENV_IN_CLUSTER) or "").strip().lower()
        if override:
            return override not in ("0", "false", "no", "off")
        return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))

    @property
    def base_url(self) -> str:
        if self._base_url:
            return self._base_url
        env = (os.environ.get(_ENV_URL) or "").strip()
        if env:
            return env.rstrip("/")
        return IN_CLUSTER_REGISTRY_URL if self.can_reach_cluster else TAILNET_REGISTRY_URL

    # -- discovery ---------------------------------------------------------

    def advertise(self, need_caps=None, *, include_all: bool = False) -> list[FleetModel]:
        """`GET /fleet`, in the registry's ranked order. `[]` on any failure.

        `include_all=True` adds `?include=all` — diagnostics only. The normal
        path must never send it: unhealthy and stale entries are excluded
        server-side, and asking for them would drag them back in for us to
        re-filter (badly).
        """
        params: list[tuple[str, str]] = []
        for cap in (need_caps or []):
            cap = str(cap).strip()
            if cap:
                params.append(("need_caps", cap))
        if include_all:
            params.append(("include", "all"))
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}/fleet" + (f"?{query}" if query else "")
        try:
            body = self._json_request("GET", url, timeout=self.probe_timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Not a silent pass: a fleet that should be there and isn't is
            # exactly what an operator needs to see in the log.
            logger.warning("fleet registry unavailable at %s: %s", url, exc)
            return []
        entries = _entries_of(body)
        if entries is None:
            logger.warning("fleet registry at %s returned an unexpected shape: %r",
                           url, type(body).__name__)
            return []
        return [m for m in (_model_from(raw) for raw in entries) if m is not None]

    def discover(self, need_caps=("vision",)) -> FleetModel | None:
        """The top-ranked entry we can actually use, or `None`.

        "Usable" = the registry ranked it, it has an endpoint, its state is not
        explicitly bad, it advertises the requested capabilities, and its
        endpoint is reachable from here. Order is the registry's — ranking is
        its job, not ours.
        """
        for entry in self.advertise(need_caps=need_caps):
            if not entry.endpoint:
                logger.debug("fleet: skipping %s (no endpoint advertised)", entry.model)
                continue
            if entry.state in _BAD_STATES:
                logger.debug("fleet: skipping %s (state=%s)", entry.model, entry.state)
                continue
            if not entry.has_caps(need_caps):
                logger.debug("fleet: skipping %s (capabilities=%s)",
                             entry.model, entry.capabilities)
                continue
            if entry.is_in_cluster and not self.can_reach_cluster:
                logger.debug("fleet: skipping %s at %s (in-cluster endpoint, "
                             "not reachable from here)", entry.model, entry.endpoint)
                continue
            return entry
        return None

    # -- leases ------------------------------------------------------------

    @property
    def leases(self) -> tuple[Lease, ...]:
        """Leases acquired through this client and not yet released."""
        return tuple(self._leases.values())

    def acquire_lease(self, model: str, holder: str, ttl_s: int = _DEFAULT_TTL_S,
                      node: str | None = None) -> Lease:
        """`POST /leases`. Raises `LeaseConflictError` (409, naming the current
        holder) or `FleetUnavailableError`; never a raw urllib error."""
        body: dict = {"model": model, "holder": holder, "ttl_s": int(ttl_s)}
        if node:
            body["node"] = node
        url = f"{self.base_url}/leases"
        try:
            payload = self._json_request("POST", url, body=body, timeout=self.lease_timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise _conflict_from(exc, model=model, node=node or "") from exc
            raise FleetUnavailableError(
                f"fleet registry refused a lease for {model!r}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise FleetUnavailableError(
                f"fleet registry unreachable while leasing {model!r}: {exc}") from exc

        lease_id = _lease_id_of(payload)
        if not lease_id:
            raise FleetUnavailableError(
                f"fleet registry granted a lease for {model!r} with no id: {payload!r}")
        lease = Lease(
            id=lease_id,
            model=str(_dig(payload, "model") or model),
            holder=str(_dig(payload, "holder") or holder),
            node=str(_dig(payload, "node") or (node or "")),
            ttl_s=int(_dig(payload, "ttl_s") or ttl_s),
            expires_at=str(_dig(payload, "expires_at") or ""),
            _client=self,
        )
        self._leases[lease.id] = lease
        return lease

    def _renew(self, lease: Lease, ttl_s: int | None) -> bool:
        body = {"ttl_s": int(ttl_s)} if ttl_s is not None else {}
        url = f"{self.base_url}/leases/{urllib.parse.quote(lease.id, safe='')}/renew"
        try:
            payload = self._json_request("POST", url, body=body, timeout=self.lease_timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # A lease is a hint; a failed renew is not an error worth raising
            # into an import the user is watching. Log it and let it lapse.
            logger.warning("fleet: could not renew lease %s for %s: %s",
                           lease.id, lease.model, exc)
            return False
        expires_at = _dig(payload, "expires_at")
        if expires_at:
            lease.expires_at = str(expires_at)
        if ttl_s is not None:
            lease.ttl_s = int(ttl_s)
        return True

    def _release(self, lease: Lease) -> bool:
        url = f"{self.base_url}/leases/{urllib.parse.quote(lease.id, safe='')}"
        # Marked released first, and never retried: this runs from `close()` and
        # from the exit hook, where a loop against a dead registry would hang
        # shutdown. The registry expires the lease on its own TTL regardless.
        lease.released = True
        self._leases.pop(lease.id, None)
        try:
            self._json_request("DELETE", url, timeout=self.lease_timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("fleet: could not release lease %s for %s (it will "
                           "expire in %ss): %s", lease.id, lease.model, lease.ttl_s, exc)
            return False
        return True

    def close(self) -> None:
        """Release every lease still held. Idempotent; never raises."""
        for lease in tuple(self._leases.values()):
            lease.release()

    def __enter__(self) -> "FleetClient":
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False

    # -- transport ---------------------------------------------------------

    def _json_request(self, method: str, url: str, *, body=None, timeout: float):
        """One JSON request. Returns the parsed body, or `{}` for an empty one.

        Raises whatever urllib raises (`HTTPError`/`URLError`/`OSError`) plus
        `ValueError` for a non-JSON body — callers translate those into `None`,
        `[]`, or a typed error as their contract requires.
        """
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def _entries_of(body):
    """The list of advertised entries in a reply, or `None` if there isn't one.

    Written against a service in another repo, so both a bare list and the
    obvious envelope spellings are accepted rather than betting on one.
    """
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("models", "fleet", "entries", "items", "results", "data"):
            value = body.get(key)
            if isinstance(value, list):
                return value
    return None


def _dig(payload, key):
    """`payload[key]`, also looking one level into a `lease`/`data`/`detail` wrapper."""
    if not isinstance(payload, dict):
        return None
    if payload.get(key) is not None:
        return payload[key]
    for wrapper in ("lease", "data", "detail", "result"):
        nested = payload.get(wrapper)
        if isinstance(nested, dict) and nested.get(key) is not None:
            return nested[key]
    return None


def _lease_id_of(payload):
    for key in ("id", "lease_id", "lease"):
        value = _dig(payload, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_str(raw: dict, *keys) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, dict):
            # A nested object (e.g. {"model": {"id": ...}}) names itself inside.
            value = value.get("id") or value.get("name") or value.get("model")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _model_from(raw) -> FleetModel | None:
    """One advertised entry -> `FleetModel`; `None` for anything unrecognisable."""
    if not isinstance(raw, dict):
        return None
    caps_raw = raw.get("capabilities") or raw.get("caps") or []
    if isinstance(caps_raw, str):
        caps_raw = [caps_raw]
    caps = tuple(str(c).strip().lower() for c in caps_raw if str(c).strip())
    ctx_max = raw.get("ctx_max", raw.get("context_max"))
    try:
        ctx_max = int(ctx_max) if ctx_max not in (None, "") else None
    except (TypeError, ValueError):
        ctx_max = None
    model = _first_str(raw, "model", "model_id", "id", "name")
    endpoint = _first_str(raw, "endpoint", "url", "base_url").rstrip("/")
    if not model and not endpoint:
        return None
    return FleetModel(
        model=model,
        node=_first_str(raw, "node", "node_id", "host"),
        endpoint=endpoint,
        capabilities=caps,
        ctx_max=ctx_max,
        state=_first_str(raw, "state", "status").lower(),
        path=_first_str(raw, "path").lower(),
        location=_first_str(raw, "location").lower(),
        health=raw.get("health", raw.get("health_signal")),
        raw=raw,
    )


def _find_holder(obj, depth: int = 0) -> str:
    """The holder named anywhere in a 409 body.

    The registry's own error shape isn't pinned down here, so this looks for a
    holder key at any nesting depth and, failing that, scrapes a
    human-readable `detail` string. Surfacing *who* holds the model is the whole
    value of the 409 — a `LeaseConflictError` with an empty `holder` is a much
    worse error message.
    """
    if depth > 4:
        return ""
    if isinstance(obj, str):
        match = _HOLDER_IN_TEXT.search(obj)
        return match.group(1) if match else ""
    if isinstance(obj, dict):
        for key in _HOLDER_KEYS:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                named = _first_str(value, "name", "id", "holder")
                if named:
                    return named
        for value in obj.values():
            found = _find_holder(value, depth + 1)
            if found:
                return found
        return ""
    if isinstance(obj, list):
        for item in obj:
            found = _find_holder(item, depth + 1)
            if found:
                return found
    return ""


def _conflict_from(exc: urllib.error.HTTPError, *, model: str,
                   node: str) -> LeaseConflictError:
    try:
        body = json.loads((exc.read() or b"").decode("utf-8"))
    except (ValueError, OSError, AttributeError):
        body = None
    holder = _find_holder(body)
    named_model = str(_dig(body, "model") or model)
    expires_at = str(_dig(body, "expires_at") or _dig(body, "expires") or "")
    retry_after = _dig(body, "retry_after")
    try:
        retry_after = float(retry_after) if retry_after is not None else None
    except (TypeError, ValueError):
        retry_after = None
    who = f"held by {holder!r}" if holder else "already held (holder not named)"
    detail = f", expires {expires_at}" if expires_at else ""
    return LeaseConflictError(
        f"fleet lease for {named_model!r} is {who}{detail}. Leases are "
        f"cooperative hints, not a mutex — back off or proceed knowingly.",
        model=named_model, holder=holder, node=str(_dig(body, "node") or node),
        expires_at=expires_at, retry_after=retry_after)


def _release_at_exit() -> None:
    """Release outstanding leases at interpreter shutdown.

    Best-effort by design: the registry expires everything on its own TTL, so a
    failure here costs a wait, not correctness — and nothing may raise out of an
    atexit hook.
    """
    for client in list(_LIVE_CLIENTS):
        try:
            client.close()
        except Exception as exc:  # pragma: no cover - shutdown must not raise
            logger.warning("fleet: lease release at exit failed: %s", exc)


atexit.register(_release_at_exit)
