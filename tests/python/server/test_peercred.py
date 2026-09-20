"""Unix-domain-socket peer-credential identity (server/peercred.py,
server/uds.py, and the new first branch of server/auth.py::_resolve).

What these tests are actually defending
---------------------------------------
The feature's claim is "who has SSH access becomes who dubIS knows you are,
unforgeably, with no shared secret". Four things have to hold for that:

(a) a request over a Unix socket resolves to the peer's *username*;
(b) a TCP request is untouched and resolves exactly as it does today;
(c) a TCP client cannot reach the uid-derived path at all — no header, no
    cookie, no amount of peercred machinery being armed;
(d) `request.client is None` (which is what uvicorn reports for EVERY Unix
    socket connection) does not 401.

(d) is the one that looks like a broken server rather than a missing
feature, so it is tested from both sides: absent peer identity still 401s,
present peer identity does not.

Platform policy
---------------
`SO_PEERCRED` is Linux-only and this repo's developers are on macOS. Per
CLAUDE.md's test policy nothing here is skipped: the platform-dependent
syscall is isolated behind `peercred._peer_ucred`, which these tests
monkeypatch so the Linux behaviour is exercised everywhere, while
`test_real_platform_behavior_over_a_real_unix_socket` asserts the *real*,
unpatched answer on whichever platform it happens to be running — the
username on Linux, the documented `local` degradation elsewhere. Neither
branch is a skip; both assert something true.
"""

from __future__ import annotations

import csv
import getpass
import os
import socket
import struct
import tempfile
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from server import peercred, uds
from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger

FAKE_PID = 4242


# ── fixtures/helpers ─────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path):
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    yield inst
    inst.shutdown()


@pytest.fixture
def short_socket_path():
    """A socket path short enough to bind.

    `sockaddr_un.sun_path` is ~104 bytes on macOS / 108 on Linux, and pytest's
    `tmp_path` on macOS lives under `/private/var/folders/<long hash>/...`,
    which blows straight past that with `OSError: AF_UNIX path too long`. So
    these tests get their own short directory rather than tmp_path's.
    """
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="dbs") as d:
        yield os.path.join(d, "s")


def _unix_socketpair():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    return a, b


def _tcp_socketpair():
    """A genuinely connected AF_INET pair — `socket.socketpair` cannot make
    one, and a *connected* socket is what the production code sees."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(listener.getsockname())
    server_side, _ = listener.accept()
    listener.close()
    return client, server_side


def _arm_peercred(monkeypatch, uid, pid=FAKE_PID):
    """Make the Linux peer-credential path live on any platform.

    Only the syscall is faked. Everything that decides what to do with its
    answer — the AF_UNIX gate, the availability branch, the uid->username
    lookup, the scope plumbing, auth's resolution order — runs for real.
    """
    monkeypatch.setattr(peercred, "available", lambda: True)
    monkeypatch.setattr(peercred, "_peer_ucred", lambda sock: (pid, uid))


def _read_sources(adjustments_csv):
    with open(adjustments_csv, newline="", encoding="utf-8-sig") as f:
        return [row["source"] for row in csv.DictReader(f)]


# ── the AF_UNIX gate: (a) and (c) at the unit level ──────────────────────────


def test_unix_socket_resolves_to_the_peers_username(monkeypatch):
    """(a) A real AF_UNIX socket, a uid from the kernel -> that user's name."""
    _arm_peercred(monkeypatch, os.getuid())
    a, b = _unix_socketpair()
    try:
        assert peercred.identity_for_socket(a) == getpass.getuser()
    finally:
        a.close()
        b.close()


def test_tcp_socket_gets_no_identity_even_with_peercred_armed(monkeypatch):
    """(c) The spoofing path, closed at its root.

    The credential reader here is rigged to hand back uid 0 (root) to anyone
    who asks. A TCP connection must never get to ask: `identity_for_socket`
    checks the socket family FIRST, so the answer is None — "I claim
    nothing" — and server/auth.py carries on down its existing order.
    """
    _arm_peercred(monkeypatch, 0)
    client, server_side = _tcp_socketpair()
    try:
        assert peercred.identity_for_socket(client) is None
        assert peercred.identity_for_socket(server_side) is None
    finally:
        client.close()
        server_side.close()


def test_none_socket_claims_nothing():
    assert peercred.identity_for_socket(None) is None


def test_real_platform_behavior_over_a_real_unix_socket():
    """The unmonkeypatched truth on whichever platform this is running.

    Not a skip and not a tautology: on Linux this is the end-to-end proof
    that `SO_PEERCRED` really is being read (a socketpair's peer credentials
    are this very process's), and on macOS/Windows it pins the documented
    degradation — `local`, the same identity a loopback TCP caller gets —
    so a future change that silently 401s every macOS UDS caller fails here.
    """
    a, b = _unix_socketpair()
    try:
        identity = peercred.identity_for_socket(a)
    finally:
        a.close()
        b.close()

    if peercred.available():
        assert identity == getpass.getuser()
    else:
        assert identity == peercred.LOCAL_IDENTITY
        assert peercred.unavailable_reason()  # the operator gets told why


def test_unavailable_platform_degrades_to_local_not_to_none(monkeypatch):
    """A Unix peer is on this machine — the loopback trust class — even when
    we cannot name them. Returning None here would 401 every macOS UDS
    caller (`request.client` is None, so no later branch can match)."""
    monkeypatch.setattr(peercred, "available", lambda: False)
    a, b = _unix_socketpair()
    try:
        assert peercred.identity_for_socket(a) == peercred.LOCAL_IDENTITY
    finally:
        a.close()
        b.close()


def test_credential_read_failure_degrades_to_local(monkeypatch):
    """`available()` said yes but the syscall failed — still same-machine
    trust, never an escalation and never a 401."""
    _arm_peercred(monkeypatch, 0)
    monkeypatch.setattr(peercred, "_peer_ucred", lambda sock: None)
    a, b = _unix_socketpair()
    try:
        assert peercred.identity_for_socket(a) == peercred.LOCAL_IDENTITY
    finally:
        a.close()
        b.close()


# ── struct ucred validation ──────────────────────────────────────────────────


def test_zeroed_ucred_is_refused():
    """The concrete danger: on Linux, SO_PEERCRED against a non-AF_UNIX
    socket does not error — it returns a zeroed struct, which reads as uid 0
    (root). Gate 3 rejects it on pid."""
    assert peercred._unpack_ucred(struct.pack("iII", 0, 0, 0)) is None


def test_negative_pid_ucred_is_refused():
    assert peercred._unpack_ucred(struct.pack("iII", -1, 1000, 1000)) is None


def test_short_ucred_is_refused():
    assert peercred._unpack_ucred(b"\x00\x01") is None


def test_well_formed_ucred_unpacks_pid_and_uid():
    assert peercred._unpack_ucred(struct.pack("iII", 7, 1000, 1000)) == (7, 1000)


# ── uid -> name ──────────────────────────────────────────────────────────────


def test_uid_without_a_passwd_entry_becomes_uid_n(monkeypatch):
    """A container user or a deleted account must resolve to something
    honest and still per-user, not to a shared identity and not to a
    throw."""
    class _NoSuchUser:
        @staticmethod
        def getpwuid(uid):
            raise KeyError(uid)

    monkeypatch.setattr(peercred, "pwd", _NoSuchUser)
    assert peercred.username_for_uid(60123) == "uid:60123"


def test_uid_namespaces_cannot_collide():
    """`:` is not legal in a POSIX portable username, so `uid:<n>` can never
    be mistaken for a real account's name."""
    assert ":" in peercred.username_for_uid.__doc__
    assert peercred.username_for_uid(os.getuid()) == getpass.getuser()


# ── the uvicorn protocol subclass ────────────────────────────────────────────


class _FakeTransport:
    def __init__(self, sock):
        self._sock = sock

    def get_extra_info(self, name, default=None):
        if name == "socket":
            return self._sock
        return default


def _make_protocol(app_state):
    """A real PeerCredHTTPProtocol, constructed the way uvicorn constructs it."""
    import uvicorn
    from uvicorn.server import ServerState

    async def _app(scope, receive, send):  # pragma: no cover - never called
        raise AssertionError

    config = uvicorn.Config(_app, log_level="warning")
    config.load()
    return uds.PeerCredHTTPProtocol(
        config=config, server_state=ServerState(), app_state=app_state,
    )


def test_protocol_stamps_the_identity_for_a_unix_connection(monkeypatch):
    _arm_peercred(monkeypatch, os.getuid())
    shared = {"lifespan_thing": 1}
    proto = _make_protocol(shared)
    a, b = _unix_socketpair()
    try:
        proto.connection_made(_FakeTransport(a))
    finally:
        a.close()
        b.close()

    assert proto.app_state[peercred.PEER_IDENTITY_KEY] == getpass.getuser()
    # The shared lifespan dict must not have been mutated: it is common to
    # every other connection, and stamping one caller's name into it would
    # hand that identity to everybody else.
    assert shared == {"lifespan_thing": 1}
    assert proto.app_state["lifespan_thing"] == 1


def test_protocol_leaves_a_tcp_connection_untouched(monkeypatch):
    _arm_peercred(monkeypatch, 0)
    shared = {"lifespan_thing": 1}
    proto = _make_protocol(shared)
    client, server_side = _tcp_socketpair()
    try:
        proto.connection_made(_FakeTransport(server_side))
    finally:
        client.close()
        server_side.close()

    assert proto.app_state is shared
    assert peercred.PEER_IDENTITY_KEY not in proto.app_state


def test_protocol_throws_loudly_if_uvicorn_drops_app_state(monkeypatch):
    """Repo error policy: a broken assumption raises here rather than
    silently handing every Unix-socket caller a 401 that reads as an
    unrelated server bug."""
    _arm_peercred(monkeypatch, os.getuid())
    proto = _make_protocol({})
    proto.app_state = None  # simulate a future uvicorn moving it
    a, b = _unix_socketpair()
    try:
        with pytest.raises(RuntimeError, match="app_state"):
            proto.connection_made(_FakeTransport(a))
    finally:
        a.close()
        b.close()


# ── server/auth.py: resolution order, and the client-is-None trap ────────────


class _ScopeStamper:
    """An ASGI shim standing in for what server/uds.py does to a connection:
    put a peer identity in the scope's `state` dict. Lets the auth-order
    tests run under TestClient without a real socket."""

    def __init__(self, app, identity):
        self.app = app
        self.identity = identity

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            state = dict(scope.get("state") or {})
            state[peercred.PEER_IDENTITY_KEY] = self.identity
            scope = {**scope, "state": state}
        await self.app(scope, receive, send)


def test_uds_scope_does_not_401_when_client_is_none(api, monkeypatch):
    """(d) THE trap. uvicorn reports `request.client is None` for every Unix
    socket connection, so the loopback branch cannot match and, without the
    new branch, the request falls all the way through to 401."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    app = _ScopeStamper(create_app(api), "alice")
    with TestClient(app, client=None) as c:
        assert c.get("/v1/parts").status_code == 200


def test_client_none_without_a_peer_identity_still_401s(api, monkeypatch):
    """The other half of (d): the fix must be a peer-credential branch, not a
    blanket "client is None means trusted" hole."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    with TestClient(create_app(api), client=None) as c:
        assert c.get("/v1/parts").status_code == 401


def test_peer_identity_is_stamped_into_the_mutation_source(api, monkeypatch):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    app = _ScopeStamper(create_app(api), "alice")
    with TestClient(app, client=None) as c:
        r = c.post("/v1/parts/C100000/adjust",
                   json={"adj_type": "add", "quantity": 1, "source": "cli"})
    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == ["cli@alice"]


def test_peer_identity_outranks_a_forged_tailscale_header(api, monkeypatch):
    """The kernel's answer is the strongest evidence available, so nothing a
    caller sends may replace it with a different identity."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TRUSTED_PROXY_IPS", "10.42.2.176")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", "mallory@example.com")
    app = _ScopeStamper(create_app(api), "alice")
    with TestClient(app, client=None) as c:
        r = c.post("/v1/parts/C100000/adjust",
                   json={"adj_type": "add", "quantity": 1, "source": "cli"},
                   headers={"Tailscale-User-Login": "mallory@example.com"})
    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == ["cli@alice"]


def test_a_uds_peer_named_local_keeps_todays_loopback_privileges(api, monkeypatch):
    """The macOS/degraded path must behave like loopback, including
    `require_loopback`-gated routes and an unsuffixed mutation source."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    app = _ScopeStamper(create_app(api), peercred.LOCAL_IDENTITY)
    with TestClient(app, client=None) as c:
        r = c.post("/v1/parts/C100000/adjust",
                   json={"adj_type": "add", "quantity": 1, "source": "cli"})
    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == ["cli"]


def test_tcp_loopback_resolution_is_unchanged(api, monkeypatch):
    """(b) A TCP request still resolves exactly as it does today."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    with TestClient(create_app(api), client=("127.0.0.1", 51234)) as c:
        r = c.post("/v1/parts/C100000/adjust",
                   json={"adj_type": "add", "quantity": 1, "source": "cli"})
    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == ["cli"]


# ── end to end over a real Unix socket ───────────────────────────────────────


def _serve(app, **config_kwargs):
    import uvicorn

    config_kwargs.setdefault("timeout_graceful_shutdown", 3)
    config = uvicorn.Config(app, log_level="warning", **config_kwargs)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn did not start in time"
    return server, thread


def _stop(server, thread):
    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_end_to_end_unix_socket_stamps_the_real_user(api, monkeypatch,
                                                     short_socket_path):
    """The whole path, with no ASGI shim anywhere: a real uvicorn bound to a
    real Unix socket, a real httpx UDS client, the real auth middleware, and
    the adjustment row that comes out of it on disk.

    Only `peercred._peer_ucred` is monkeypatched, so that the assertion is
    the same on Linux and macOS; the socket really is AF_UNIX and every gate
    in front of that syscall runs for real.
    """
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    _arm_peercred(monkeypatch, os.getuid())

    server, thread = _serve(create_app(api), uds=short_socket_path,
                            http=uds.PeerCredHTTPProtocol)
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=short_socket_path),
                          base_url="http://localhost") as c:
            assert c.get("/v1/health").json() == {"ok": True}
            r = c.post("/v1/parts/C100000/adjust",
                       json={"adj_type": "add", "quantity": 1, "source": "cli"})
    finally:
        _stop(server, thread)
        uds.remove_socket_path(short_socket_path)

    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == [f"cli@{getpass.getuser()}"]


def test_end_to_end_tcp_is_unaffected_by_the_same_protocol_class(api, monkeypatch):
    """(b) + (c) end to end: the very same protocol class, with the
    credential reader rigged to answer "root" to anyone who asks, over TCP.
    The AF_UNIX gate means the request resolves to `local` exactly as it
    does today — no `@root` suffix, no identity from the kernel at all."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    _arm_peercred(monkeypatch, 0)

    server, thread = _serve(create_app(api), host="127.0.0.1", port=0,
                            http=uds.PeerCredHTTPProtocol)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/v1/parts/C100000/adjust",
                       json={"adj_type": "add", "quantity": 1, "source": "cli"},
                       timeout=10)
    finally:
        _stop(server, thread)

    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == ["cli"]


def test_end_to_end_unix_socket_in_auth_off_mode_is_unchanged(api, monkeypatch,
                                                              short_socket_path):
    """`off` mode installs no middleware at all, so a Unix socket is just a
    transport there — the source must stay byte-identical to today's."""
    monkeypatch.delenv("DUBIS_AUTH_MODE", raising=False)
    _arm_peercred(monkeypatch, os.getuid())

    server, thread = _serve(create_app(api), uds=short_socket_path,
                            http=uds.PeerCredHTTPProtocol)
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=short_socket_path),
                          base_url="http://localhost") as c:
            r = c.post("/v1/parts/C100000/adjust",
                       json={"adj_type": "add", "quantity": 1, "source": "cli"})
    finally:
        _stop(server, thread)
        uds.remove_socket_path(short_socket_path)

    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == ["cli"]


# ── socket-file lifecycle ────────────────────────────────────────────────────


def test_prepare_socket_path_removes_a_stale_socket(short_socket_path):
    """A hard-killed server leaves its socket file behind; without this, the
    next `--uds` start would fail with a bare EADDRINUSE forever."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(short_socket_path)
    s.close()  # bound, then died — file remains, nothing listening
    assert os.path.exists(short_socket_path)

    uds.prepare_socket_path(short_socket_path)

    assert not os.path.exists(short_socket_path)


def test_prepare_socket_path_refuses_a_live_socket(short_socket_path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(short_socket_path)
    s.listen(1)
    try:
        with pytest.raises(uds.SocketPathInUseError, match="already accepting"):
            uds.prepare_socket_path(short_socket_path)
    finally:
        s.close()
        uds.remove_socket_path(short_socket_path)


def test_prepare_socket_path_refuses_a_regular_file(short_socket_path):
    """A typo in `--uds` must not turn into data loss."""
    with open(short_socket_path, "w", encoding="utf-8") as f:
        f.write("important")

    with pytest.raises(uds.SocketPathInUseError, match="not a socket"):
        uds.prepare_socket_path(short_socket_path)

    assert os.path.exists(short_socket_path)


def test_prepare_socket_path_is_a_noop_when_nothing_is_there(short_socket_path):
    uds.prepare_socket_path(short_socket_path)  # must not raise
    assert not os.path.exists(short_socket_path)


# ── a named peer is not `local` ──────────────────────────────────────────────


def test_a_named_unix_peer_cannot_read_the_servers_disk(api, monkeypatch):
    """`require_loopback` gates `/v1/import/parse`'s server-local `path`
    branch on identity `local`, so a *named* Unix-socket peer is refused —
    deliberately, and worth pinning rather than leaving incidental.

    On a shared box the server may well run as a different account than the
    caller. Letting `bob` ask it to read `/home/alice/secrets.csv` is exactly
    what that gate exists to stop: "the caller is on this machine" is not the
    same claim as "the caller is the account this server runs as". The
    browser-upload (`file_b64`) branch stays open to everyone, which is what
    a remote client already uses.
    """
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    app = _ScopeStamper(create_app(api), "bob")
    with TestClient(app, client=None) as c:
        r = c.post("/v1/import/parse", json={"path": "/etc/hostname"})
    assert r.status_code == 403
    assert r.json()["code"] == "loopback_only"


def test_a_degraded_unix_peer_keeps_loopback_file_access(api, monkeypatch):
    """The other side of the same coin: where peer credentials are
    unavailable the identity is `local`, and `local` is exactly what
    `require_loopback` admits — so the macOS/degraded path does not quietly
    take a capability away from a single-user machine."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    app = _ScopeStamper(create_app(api), peercred.LOCAL_IDENTITY)
    with TestClient(app, client=None) as c:
        r = c.post("/v1/import/parse", json={"path": "/nonexistent/not-a-file.csv"})
    # Past the gate: the failure is now about the missing file, not the caller.
    assert r.status_code != 403
