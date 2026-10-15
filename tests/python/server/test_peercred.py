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

#: "stamp no uid at all", which is what server/uds.py does on a platform that
#: cannot read one — distinct from stamping uid 0.
_NO_UID = object()


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


def test_uid_for_username_is_the_inverse_of_username_for_uid():
    """`DUBIS_UDS_PROXY_UIDS=tailscale` is a convenience over this one lookup,
    so the two directions have to agree — and an unknown account answers None
    rather than a guess (the caller decides that is an error)."""
    assert peercred.uid_for_username(getpass.getuser()) == os.getuid()
    assert peercred.uid_for_username("no-such-account-xyz") is None


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
    # The raw uid travels with it: it is the only evidence that can answer
    # "is this peer a proxy?" without guessing from a name.
    assert proto.app_state[peercred.PEER_UID_KEY] == os.getuid()
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
    # Neither the uid: with no identity there is no uid, which is what keeps
    # the proxy-hop branch unreachable for a TCP connection.
    assert peercred.PEER_UID_KEY not in proto.app_state


def test_protocol_stamps_no_uid_when_the_platform_cannot_read_one(monkeypatch):
    """The degraded platform still gets an identity (`local`, so it is not
    401ed) but NO uid — and `server/auth.py` requires a uid for the proxy hop,
    so a box that cannot read `SO_PEERCRED` cannot take that branch even if an
    operator configures it there."""
    monkeypatch.setattr(peercred, "available", lambda: False)
    proto = _make_protocol({})
    a, b = _unix_socketpair()
    try:
        proto.connection_made(_FakeTransport(a))
    finally:
        a.close()
        b.close()

    assert proto.app_state[peercred.PEER_IDENTITY_KEY] == peercred.LOCAL_IDENTITY
    assert peercred.PEER_UID_KEY not in proto.app_state


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
    put a peer identity (and, when the kernel reported one, its uid) in the
    scope's `state` dict. Lets the auth-order tests run under TestClient
    without a real socket."""

    def __init__(self, app, identity, uid=_NO_UID):
        self.app = app
        self.identity = identity
        self.uid = uid

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            state = dict(scope.get("state") or {})
            state[peercred.PEER_IDENTITY_KEY] = self.identity
            if self.uid is not _NO_UID:
                state[peercred.PEER_UID_KEY] = self.uid
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


# ── reading the uid back out of the scope ────────────────────────────────────


def test_credential_for_socket_reports_the_uid_alongside_the_name(monkeypatch):
    _arm_peercred(monkeypatch, 0)
    a, b = _unix_socketpair()
    try:
        credential = peercred.credential_for_socket(a)
    finally:
        a.close()
        b.close()

    assert credential is not None
    assert credential.uid == 0
    assert credential.identity == peercred.username_for_uid(0)


def test_credential_for_a_tcp_socket_is_still_no_credential_at_all(monkeypatch):
    """Not "a credential with no uid" — no credential. `None` is what makes
    `server/uds.py` stamp nothing for a TCP connection."""
    _arm_peercred(monkeypatch, 0)
    client, server_side = _tcp_socketpair()
    try:
        assert peercred.credential_for_socket(client) is None
    finally:
        client.close()
        server_side.close()


@pytest.mark.parametrize("state", [
    pytest.param({}, id="absent"),
    pytest.param({peercred.PEER_UID_KEY: "0"}, id="string-not-int"),
    pytest.param({peercred.PEER_UID_KEY: True}, id="bool-is-not-a-uid"),
    pytest.param({peercred.PEER_UID_KEY: -1}, id="negative"),
    pytest.param({peercred.PEER_UID_KEY: None}, id="none"),
])
def test_peer_uid_from_scope_refuses_anything_that_is_not_a_uid(state):
    assert peercred.peer_uid_from_scope({"state": state}) is None


def test_peer_uid_from_scope_reads_a_real_uid_and_survives_a_missing_state():
    assert peercred.peer_uid_from_scope(
        {"state": {peercred.PEER_UID_KEY: 0}}) == 0
    assert peercred.peer_uid_from_scope({}) is None
    assert peercred.peer_uid_from_scope({"state": "not-a-dict"}) is None


# ── the proxy hop: a socket whose peer is tailscaled, not a person ───────────
#
# The bug these defend: `tailscale serve` proxying into the Unix socket makes
# the kernel peer `tailscaled` (uid 0), so every tailnet caller is stamped
# `@root` and the store cannot answer who moved the stock. The fix is a hop,
# not a downgrade: the kernel's answer still wins unless an operator named this
# uid as a proxy AND the proxy vouched for an allowlisted tailnet login.

PROXY_UID = 0
TAILNET_LOGIN = "alice@example.com"


def _arm_proxy_hop(monkeypatch, *, proxy_uids="0", trust="1", allowlist=TAILNET_LOGIN):
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", trust)
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", allowlist)
    if proxy_uids is None:
        monkeypatch.delenv("DUBIS_UDS_PROXY_UIDS", raising=False)
    else:
        monkeypatch.setenv("DUBIS_UDS_PROXY_UIDS", proxy_uids)


def _adjust_over_socket(api, monkeypatch, *, identity, uid, headers=None):
    """One adjustment from a Unix-socket caller, returning the source it wrote."""
    app = _ScopeStamper(create_app(api), identity, uid)
    with TestClient(app, client=None) as c:
        r = c.post("/v1/parts/C100000/adjust",
                   json={"adj_type": "add", "quantity": 1, "source": "cli"},
                   headers=headers or {})
    assert r.status_code == 200, r.json()
    return _read_sources(api.adjustments_csv)[-1]


def test_a_named_proxy_uid_resolves_the_tailnet_login(api, monkeypatch):
    """The whole point: on aspen the kernel says uid 0, tailscaled says
    'this is alice over the tailnet', and the ledger row names alice."""
    _arm_proxy_hop(monkeypatch)
    sources = _adjust_over_socket(api, monkeypatch, identity="root", uid=PROXY_UID,
                                  headers={"Tailscale-User-Login": TAILNET_LOGIN})
    assert sources == f"cli@{TAILNET_LOGIN}"


def test_a_proxy_uid_with_no_header_falls_back_to_the_unix_username(api, monkeypatch):
    """A root process that reaches the socket itself (no proxy, so no header)
    is still `root` — today's behavior, unchanged."""
    _arm_proxy_hop(monkeypatch)
    assert _adjust_over_socket(api, monkeypatch, identity="root", uid=PROXY_UID) == "cli@root"


def test_a_proxy_uid_with_a_login_off_the_allowlist_falls_back(api, monkeypatch):
    """An off-allowlist login is not a reason to 401 a caller the kernel has
    already named: it is a reason to ignore the header and use the uid."""
    _arm_proxy_hop(monkeypatch)
    sources = _adjust_over_socket(api, monkeypatch, identity="root", uid=PROXY_UID,
                                  headers={"Tailscale-User-Login": "mallory@example.com"})
    assert sources == "cli@root"


def test_the_hop_needs_the_tailnet_trust_flag_even_for_a_named_proxy_uid(api, monkeypatch):
    """`DUBIS_TRUST_TAILSCALE_HEADER` stays the master switch for believing any
    `Tailscale-User-Login`, socket included. Naming a uid is not the same as
    opting into header trust."""
    _arm_proxy_hop(monkeypatch, trust="0")
    sources = _adjust_over_socket(api, monkeypatch, identity="root", uid=PROXY_UID,
                                  headers={"Tailscale-User-Login": TAILNET_LOGIN})
    assert sources == "cli@root"


def test_the_hop_is_inert_when_nobody_opted_in(api, monkeypatch):
    """Default-off. Everything else is configured and correct, and the uid is
    0 — and nothing happens, because no rule anywhere infers 'uid 0 is a
    proxy'."""
    _arm_proxy_hop(monkeypatch, proxy_uids=None)
    sources = _adjust_over_socket(api, monkeypatch, identity="root", uid=PROXY_UID,
                                  headers={"Tailscale-User-Login": TAILNET_LOGIN})
    assert sources == "cli@root"


def test_a_peer_with_its_own_uid_cannot_be_upgraded_by_a_header(api, monkeypatch, caplog):
    """THE property the hop must not lose.

    A teammate in the socket directory's group who reaches it directly (their
    own uid, `ssh -L` or a local curl) and attaches somebody else's tailnet
    login gets their OWN username. The header is dropped, and the attempt is
    logged rather than silently discarded.
    """
    import logging as _logging

    _arm_proxy_hop(monkeypatch)
    with caplog.at_level(_logging.WARNING):
        sources = _adjust_over_socket(api, monkeypatch, identity="bob", uid=1000,
                                      headers={"Tailscale-User-Login": TAILNET_LOGIN})
    assert sources == "cli@bob"
    assert any("Ignoring Tailscale-User-Login from Unix-socket peer bob" in rec.message
               for rec in caplog.records if rec.levelno == _logging.WARNING), \
        "the dropped header should be visible in the log, not silently discarded"


def test_a_peer_with_no_uid_cannot_take_the_hop(api, monkeypatch):
    """The degraded path (no `SO_PEERCRED`, or an unreadable credential): the
    identity is `local` and there is NO uid, so the hop is unreachable rather
    than merely unused — a caller cannot claim the hop by having no uid."""
    _arm_proxy_hop(monkeypatch)
    sources = _adjust_over_socket(api, monkeypatch, identity=peercred.LOCAL_IDENTITY,
                                  uid=None, headers={"Tailscale-User-Login": TAILNET_LOGIN})
    assert sources == "cli", "`local` keeps today's unsuffixed source too"


def test_a_tcp_caller_cannot_reach_the_hop_end_to_end(api, monkeypatch):
    """(c) end to end, with every star aligned: the credential reader rigged to
    answer uid 0, the proxy uid configured, trust on, the login allowlisted, and
    the header sent. Over TCP the scope carries no peer identity and no uid, so
    the request resolves to `local` exactly as it does today."""
    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    monkeypatch.setenv("DUBIS_TRUST_TAILSCALE_HEADER", "1")
    monkeypatch.setenv("DUBIS_TAILNET_ALLOWLIST", TAILNET_LOGIN)
    monkeypatch.setenv("DUBIS_UDS_PROXY_UIDS", "0")
    _arm_peercred(monkeypatch, PROXY_UID)

    server, thread = _serve(create_app(api), host="127.0.0.1", port=0,
                            http=uds.PeerCredHTTPProtocol)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/v1/parts/C100000/adjust",
                       json={"adj_type": "add", "quantity": 1, "source": "cli"},
                       headers={"Tailscale-User-Login": TAILNET_LOGIN}, timeout=10)
        who = httpx.get(f"http://127.0.0.1:{port}/v1/whoami", timeout=10)
    finally:
        _stop(server, thread)

    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == ["cli"]
    assert who.json()["mechanism"] == "loopback"
    assert who.json()["socket_uid"] is None


def test_a_tcp_caller_with_no_credentials_is_still_refused(api, monkeypatch):
    """The hop must not become a hole for a network peer that sends the header
    and nothing else: no peer identity in scope means the branch is never even
    entered, and the request falls through to the 401 it always got."""
    _arm_proxy_hop(monkeypatch)
    with TestClient(create_app(api), client=("100.64.1.2", 51234)) as c:
        r = c.get("/v1/parts", headers={"Tailscale-User-Login": TAILNET_LOGIN})
    assert r.status_code == 401


def test_the_hop_survives_a_real_unix_socket(api, monkeypatch, short_socket_path):
    """The aspen shape, with no ASGI shim: a real uvicorn on a real Unix socket
    whose peer uid is the proxy's, and the tailnet header riding on it. Only
    `peercred._peer_ucred` is faked, because this suite runs on macOS too."""
    _arm_proxy_hop(monkeypatch)
    _arm_peercred(monkeypatch, PROXY_UID)

    server, thread = _serve(create_app(api), uds=short_socket_path,
                            http=uds.PeerCredHTTPProtocol)
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=short_socket_path),
                          base_url="http://localhost") as c:
            r = c.post("/v1/parts/C100000/adjust",
                       json={"adj_type": "add", "quantity": 1, "source": "cli"},
                       headers={"Tailscale-User-Login": TAILNET_LOGIN})
            # The hop applies per request, on the evidence that request carries:
            # with the proxy's header it names the person, without it the kernel's
            # answer stands. Both report the same socket uid, which is what makes
            # the difference legible to an operator with `curl --unix-socket`.
            plain = c.get("/v1/whoami").json()
            vouched = c.get("/v1/whoami",
                            headers={"Tailscale-User-Login": TAILNET_LOGIN}).json()
    finally:
        _stop(server, thread)
        uds.remove_socket_path(short_socket_path)

    assert r.status_code == 200
    assert _read_sources(api.adjustments_csv) == [f"cli@{TAILNET_LOGIN}"]
    assert plain["mechanism"] == "uds_peer_uid"
    assert plain["identity"] == peercred.username_for_uid(PROXY_UID)
    assert plain["socket_uid"] == PROXY_UID
    assert vouched["mechanism"] == "uds_proxy_hop"
    assert vouched["identity"] == TAILNET_LOGIN
    assert vouched["socket_uid"] == PROXY_UID


# ── parsing and warning about the opt-in ──────────────────────────────────


def test_proxy_uids_accept_numbers_and_account_names(monkeypatch):
    """`0` and `tailscale` are the same entry written two ways; the operator
    writes whichever they know."""
    from server.auth import _parse_proxy_uids

    monkeypatch.setattr(peercred, "uid_for_username", lambda name: 999)
    assert _parse_proxy_uids("") == frozenset()
    assert _parse_proxy_uids(" 0 , 999 ") == frozenset({0, 999})
    assert _parse_proxy_uids("tailscale") == frozenset({999})


def test_a_real_account_name_resolves_to_its_real_uid():
    """Unmonkeypatched, so it proves the passwd lookup is really wired up —
    the account running this test always has an entry."""
    from server.auth import _parse_proxy_uids

    assert _parse_proxy_uids(getpass.getuser()) == frozenset({os.getuid()})


@pytest.mark.parametrize("raw", ["not-a-user", "-1", "0,ghost", " 0x1 "])
def test_a_proxy_uid_entry_that_is_neither_a_uid_nor_an_account_raises(monkeypatch, raw):
    """Fail-loud, like every other auth list: an entry that is silently dropped
    leaves the operator believing a hop is configured when nothing is."""
    import pytest as _pytest

    from server.auth import _parse_proxy_uids

    monkeypatch.setattr(peercred, "uid_for_username", lambda name: None)
    with _pytest.raises(ValueError, match="DUBIS_UDS_PROXY_UIDS"):
        _parse_proxy_uids(raw)


def test_an_unworkable_proxy_uid_config_warns_once(api, monkeypatch, caplog):
    """The opt-in is armed but cannot fire (here: the trust flag is off). The
    config is honored exactly as written — nothing is loosened — and the
    operator is told once why every caller will keep resolving to `root`."""
    import logging as _logging

    _arm_proxy_hop(monkeypatch, trust="0", allowlist="")
    with caplog.at_level(_logging.WARNING):
        create_app(api)

    warnings = [rec for rec in caplog.records
                if rec.levelno == _logging.WARNING and "DUBIS_UDS_PROXY_UIDS" in rec.message]
    assert len(warnings) == 1
    assert "DUBIS_TRUST_TAILSCALE_HEADER" in warnings[0].message
    assert "DUBIS_TAILNET_ALLOWLIST" in warnings[0].message


def test_a_workable_proxy_uid_config_says_nothing(api, monkeypatch, caplog):
    """The other half: aspen configured correctly gets no warning, so a warning
    always means something rather than being noise to ignore."""
    import logging as _logging

    _arm_proxy_hop(monkeypatch)
    monkeypatch.setattr(peercred, "available", lambda: True)
    with caplog.at_level(_logging.WARNING):
        create_app(api)

    assert not [rec for rec in caplog.records
                if rec.levelno == _logging.WARNING and "DUBIS_UDS_PROXY_UIDS" in rec.message]


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
            assert c.get("/v1/health").json()["ok"] is True
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
