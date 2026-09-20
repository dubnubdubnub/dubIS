"""Who is on the other end of a Unix-domain-socket connection?

Why this exists
---------------
A dubIS server on a shared Linux box binds loopback and teammates reach it
with `ssh -L`. Every such request resolves to identity `local` (see
`server/auth.py::_resolve`), so every mutation is stamped with the same
`source` and `dubis adjustments rollback-source` cannot separate one
person's session from another's.

The kernel already knows who it is. `ssh -L <port>:/path/to.sock` makes
*sshd* connect to the socket as the logged-in user, and `SO_PEERCRED`
reports that process's pid/uid/gid. The uid comes from the kernel, not from
the wire, so a caller cannot claim to be someone else — no shared secret,
no token to leak, no header to forge. "Who has SSH access" becomes "who
dubIS knows you are."

The house availability-predicate pattern
----------------------------------------
`SO_PEERCRED` is Linux-only. macOS spells it `LOCAL_PEERCRED` over a
different struct (`xucred`), and Windows has neither `SO_PEERCRED` nor the
`pwd` module. CLAUDE.md's Traps section is explicit that the fix for a
platform-specific dependency is never a widened `except` — it is an
availability predicate callers branch on, in the shape of
`browser_page.available()` / `digikey_client.hidden_window_available()`.
That predicate is `available()` below, and every import that could fail on
a platform (`pwd`, `socket.AF_UNIX`, `socket.SO_PEERCRED`) is resolved once
at module import into a module-level `None` rather than being allowed to
raise past somebody's `except OSError` at request time.

What a Unix-socket caller resolves to
-------------------------------------
- Linux, uid has a passwd entry -> that username (`isaac`).
- Linux, uid has no passwd entry (a container user, a deleted account) ->
  `uid:<n>`. Honest — it reports exactly what the kernel said, it is stable
  across requests, and it still separates one caller from another, which is
  the entire point of the feature. Inventing a name, or falling back to a
  shared identity, would quietly merge two people's ledger rows.
- Any other platform (macOS dev boxes, Windows) -> `local`, i.e. *exactly*
  what a loopback TCP caller resolves to today. A Unix-socket peer is by
  construction on the same machine, so this is the same trust class as
  loopback: the granularity degrades, the trust level never rises. Callers
  are told once, loudly, at startup (`server/__main__.py` logs
  `unavailable_reason()`), so this is a documented degradation and not a
  silent one.
- Not a Unix socket at all -> `None`, and `server/auth.py` carries on down
  its existing resolution order unchanged.

Anti-spoofing
-------------
`identity_for_socket` is the only producer of a peer identity, and it
refuses anything whose transport socket is not `AF_UNIX` *before* it reads
a single byte of credential. That matters concretely: on Linux,
`getsockopt(SOL_SOCKET, SO_PEERCRED)` against an `AF_INET` socket does not
error — it hands back a zeroed `struct ucred`, which would unpack as uid 0,
i.e. **root**. Three independent gates stand in the way:

1. the transport socket's own `family` must be `AF_UNIX`;
2. `SO_DOMAIN` (the kernel's own answer for "what domain is this socket",
   Linux-only) must agree;
3. the returned `pid` must be > 0 — the kernel never reports pid 0 for a
   live peer, so a zeroed struct is rejected even if 1 and 2 were somehow
   defeated.

Nothing a client sends — header, cookie, body, TLS SNI — participates in
any of those. See `tests/python/server/test_peercred.py`.
"""

from __future__ import annotations

import logging
import os
import socket
import struct
import sys

logger = logging.getLogger(__name__)

try:  # POSIX only; absent on Windows, where the desktop app runs.
    import pwd
except ImportError:  # pragma: no cover - exercised on Windows, not in CI
    pwd = None  # type: ignore[assignment]

# Resolved once, here, rather than at each call site: a bare
# `socket.SO_PEERCRED` is an AttributeError on macOS/Windows, and an
# AttributeError is exactly the kind of thing that escapes an `except OSError`
# three frames up (CLAUDE.md, Traps: "A desktop-only dependency escapes a
# narrow except").
_AF_UNIX: int | None = getattr(socket, "AF_UNIX", None)
_SO_PEERCRED: int | None = getattr(socket, "SO_PEERCRED", None)
_SO_DOMAIN: int | None = getattr(socket, "SO_DOMAIN", None)

# `struct ucred { pid_t pid; uid_t uid; gid_t gid; }` in native byte order and
# alignment: pid_t is a signed 32-bit int, uid_t/gid_t unsigned 32-bit.
_UCRED = struct.Struct("iII")

#: Identity a same-machine caller gets when per-user attribution is not
#: possible. Deliberately the same string `server/auth.py` already returns for
#: a loopback TCP peer — `require_loopback()` and `stamp_source()` both special-
#: case it, and a Unix-socket peer deserves exactly those same privileges.
LOCAL_IDENTITY = "local"

#: Key under which `server/uds.py` stashes the resolved identity in the ASGI
#: scope's `state` dict. Server-side only: nothing a client sends can reach it.
PEER_IDENTITY_KEY = "dubis_peer_identity"


def available() -> bool:
    """Can this platform answer "which user is at the other end of this Unix
    socket?"

    The house availability predicate (CLAUDE.md, Traps). Callers branch on
    it; they never wrap the peer-credential read in a wider `except`.
    """
    return (
        sys.platform.startswith("linux")
        and _AF_UNIX is not None
        and _SO_PEERCRED is not None
        and pwd is not None
    )


def unavailable_reason() -> str:
    """One human sentence for the startup log, or "" when `available()`.

    Printed once by `server/__main__.py` when `--uds` is used on a platform
    that cannot do peer credentials, so the resulting `local` identity is a
    thing the operator was told about rather than a mystery.
    """
    if available():
        return ""
    if _AF_UNIX is None:
        return "this platform has no AF_UNIX sockets"
    if pwd is None:
        return "this platform has no `pwd` module to resolve a uid to a username"
    if not sys.platform.startswith("linux"):
        return (
            f"SO_PEERCRED is Linux-only and this is {sys.platform!r} "
            "(macOS spells it LOCAL_PEERCRED over a different struct, which "
            "dubIS does not implement)"
        )
    return "SO_PEERCRED is not exposed by this Python build"


def username_for_uid(uid: int) -> str:
    """`pwd.getpwuid(uid).pw_name`, or `uid:<n>` when there is no entry.

    A uid with no passwd entry is normal (a container user, an account
    deleted after the connection was made). `uid:<n>` keeps the one property
    the feature exists for — two different people never collapse into one
    `source` — without pretending to a name the system does not have. `:` is
    not a legal character in a POSIX portable username, so the two namespaces
    can never collide.
    """
    if pwd is None:  # pragma: no cover - Windows
        return f"uid:{uid}"
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid:{uid}"


def _unpack_ucred(raw: bytes) -> tuple[int, int] | None:
    """`(pid, uid)` from a raw `struct ucred`, or None if it cannot be trusted.

    Split out from the getsockopt call so the *judgement* — which is
    platform-independent — is testable on every platform, while only the
    syscall itself is Linux-only. The pid check is gate 3 of the three
    described in the module docstring: a zeroed struct (what a non-AF_UNIX
    socket yields on Linux) unpacks as uid 0, i.e. root, and must never be
    believed.
    """
    if len(raw) < _UCRED.size:
        logger.error("peercred: short SO_PEERCRED response (%d bytes)", len(raw))
        return None
    pid, uid, _gid = _UCRED.unpack_from(raw)
    if pid <= 0:
        logger.error("peercred: SO_PEERCRED reported pid=%d; refusing to trust it", pid)
        return None
    return pid, uid


def _peer_ucred(sock) -> tuple[int, int] | None:
    """`(pid, uid)` from `SO_PEERCRED` on *sock*, or None if untrustworthy.

    *sock* is whatever `transport.get_extra_info("socket")` handed back, and
    that is **not** necessarily a `socket.socket`: under uvloop (which uvicorn
    prefers whenever it is installed, as it is here) it is a
    `uvloop.PseudoSocket`, a deliberately restricted shim. So this dups the
    file descriptor and wraps the dup in a real `socket.socket` with the
    family stated explicitly — no reliance on CPython's fileno-based family
    detection, which needs `SO_DOMAIN` and therefore does not exist on every
    platform. Closing the wrapper closes only the dup; the transport's own
    descriptor is untouched.
    """
    try:
        dup_fd = os.dup(sock.fileno())
    except OSError:
        logger.error("peercred: could not duplicate the transport's socket fd")
        return None

    real = socket.socket(family=_AF_UNIX, type=socket.SOCK_STREAM, fileno=dup_fd)
    try:
        if _SO_DOMAIN is not None:
            domain = real.getsockopt(socket.SOL_SOCKET, _SO_DOMAIN)
            if domain != int(_AF_UNIX):
                logger.error(
                    "peercred: refusing a connection whose transport claimed AF_UNIX "
                    "but whose kernel SO_DOMAIN is %s", domain,
                )
                return None
        raw = real.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, _UCRED.size)
    except OSError as exc:
        # Not swallowed: this is the supported platform failing at the one
        # thing it advertised via available(), so it is an error, not a shrug.
        logger.error("peercred: SO_PEERCRED read failed (%s)", exc)
        return None
    finally:
        real.close()

    return _unpack_ucred(raw)


def identity_for_socket(sock) -> str | None:
    """The identity to attribute to a connection whose transport socket is
    *sock*.

    Returns None — meaning "this is not a Unix socket, I claim nothing" — for
    every TCP connection, which is what keeps `server/auth.py`'s existing
    resolution order byte-identical for them. See the module docstring for
    the three gates and for what each platform resolves to.
    """
    if sock is None:
        return None
    family = getattr(sock, "family", None)
    if _AF_UNIX is None or family is None:
        return None
    try:
        if int(family) != int(_AF_UNIX):
            return None
    except (TypeError, ValueError):  # pragma: no cover - family is always int-ish
        return None

    if not available():
        return LOCAL_IDENTITY

    cred = _peer_ucred(sock)
    if cred is None:
        # The socket *is* AF_UNIX, so the peer is on this machine — the
        # loopback trust class — we just could not name them.
        return LOCAL_IDENTITY
    _pid, uid = cred
    return username_for_uid(uid)


def identity_from_scope(scope) -> str | None:
    """Read back what `server/uds.py` stashed for this connection.

    `scope["state"]` is the ASGI lifespan-state dict, which uvicorn copies
    per request from a per-connection dict — see `server/uds.py` for how the
    per-connection copy is made. Read defensively (the key is absent for
    every TCP request, and for the Starlette TestClient) and never coerce:
    only a non-empty string counts.
    """
    state = scope.get("state")
    if not isinstance(state, dict):
        return None
    identity = state.get(PEER_IDENTITY_KEY)
    if isinstance(identity, str) and identity:
        return identity
    return None
