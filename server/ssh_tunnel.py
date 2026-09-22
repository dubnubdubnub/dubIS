"""Hub-managed SSH tunnels: a roster entry can be `ssh://...` instead of `http://...`.

A teammate reaching a remote dubIS used to hand-run
`ssh -N -L 17891:127.0.0.1:7891 me@box` and add `http://127.0.0.1:17891` as a
server. That tunnel died on the first laptop sleep and the server read as
"down" — correctly, and uselessly. The hub already does every outbound fetch to
a source (`server/sources.SourceClients`), so it is the thing that owns the
tunnel now: it spawns the system `ssh` lazily on first use of the source,
supervises it, and restarts it after it exits.

## URL grammar

    ssh://[user@]host[:sshport]/<remote target>

`<remote target>` is everything after the first `/` following the authority,
and it is exactly one of:

  * a remote **Unix socket** — an absolute path, written with its leading `/`:
    `ssh://me@box/run/dubis/dubis.sock` forwards to `/run/dubis/dubis.sock`.
    Preferred: with `python -m server --uds` on the box, sshd connects to the
    socket *as the ssh user*, so `server/peercred.py` attributes every mutation
    to that person, not to a shared `local`.
  * a remote **TCP** `host:port` — `ssh://me@box/127.0.0.1:7891`, or
    `ssh://me@box/[::1]:7891`, or `ssh://me@box/localhost:7891`.

The rule that tells them apart is a colon: **a target containing `:` is TCP
`host:port`; one without is a socket path.** It is unambiguous by construction,
because ssh's own `-L` syntax (`bind:port:remote_socket`) cannot carry a colon
inside a socket path — so no socket path this grammar rejects could have been
forwarded anyway. There is no default target: an `ssh://me@box` with nothing
after the host is refused, loudly, rather than guessed at.

`js/servers-logic.js`'s `parseSshUrl` implements the same grammar and the two
are pinned against one shared table of cases in the tests.

## Lifecycle

* **Lazy**: nothing is spawned until a request (or the `GET /v1/sources` probe)
  needs that source.
* **Supervised**: before each use the process is checked; one that has exited is
  restarted, reusing its old local port when it can so pooled connections and
  log lines stay recognizable. A failed start is retried with exponential
  backoff (1s, 2s, 4s ... 30s) — inside the backoff window the *last* failure is
  raised immediately rather than spawning ssh on every request.
* **Reported**: every failure carries a `kind` and a sentence built from ssh's
  own stderr (`Permission denied`, `Could not resolve hostname`, ...), and both
  surface in `GET /v1/sources` (`tunnel` + `detail`) so the picker's dot says
  why, instead of a bare red.
* **Reaped**: `close_all()` on hub shutdown (the app lifespan), again from
  `atexit`, `PR_SET_PDEATHSIG` on Linux so a SIGKILLed hub takes its children
  with it, and a pidfile (`<data_dir>/.ssh_tunnels.json`) that the next hub
  start reads to kill tunnels an earlier, hard-killed hub left behind.

`ssh` is run with `BatchMode=yes`: the hub has no terminal, so a key that needs
a passphrase must already be in `ssh-agent`. That case, like a key the box does
not accept, ends as `Permission denied`, and the message says both fixes.
"""

from __future__ import annotations

import atexit
import collections
import contextlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import anyio
import httpx

logger = logging.getLogger(__name__)

SCHEME = "ssh"
_SCHEME_RE = re.compile(r"^ssh://", re.IGNORECASE)

# How long a start may take before it is a failure. ssh's own ConnectTimeout
# is shorter, so an unreachable host normally reports itself first.
START_TIMEOUT_SECONDS = 15.0
SSH_CONNECT_TIMEOUT_SECONDS = 10
BACKOFF_INITIAL_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
STDERR_LINES_KEPT = 40
PIDFILE_NAME = ".ssh_tunnels.json"

# Failure kinds — stable strings, reported to the frontend.
KIND_NO_SSH = "no_ssh"
KIND_AUTH = "auth"
KIND_HOST_KEY = "host_key"
KIND_DNS = "dns"
KIND_UNREACHABLE = "unreachable"
KIND_REMOTE_TARGET = "remote_target"
KIND_TIMEOUT = "timeout"
KIND_EXITED = "exited"

STATE_IDLE = "idle"
STATE_STARTING = "starting"
STATE_UP = "up"
STATE_FAILED = "failed"


class SshUrlError(ValueError):
    """An `ssh://` roster URL that does not follow the grammar above."""


class TunnelError(httpx.TransportError):
    """The tunnel to an `ssh://` source could not carry this request.

    A `httpx.TransportError` on purpose: every caller of `SourceClients`
    already treats one as "this source is unreachable" — `server/proxy.py`
    turns it into a 502 `source_unavailable`, `server/fanout.py` into a failed
    per-source entry, `sources.probe` into a red dot — so a tunnel failure
    travels exactly the path a refused connection does, only with a reason
    worth reading attached.
    """

    def __init__(self, message: str, *, kind: str, request: httpx.Request | None = None) -> None:
        super().__init__(message, request=request)
        self.kind = kind


# ── URL grammar ─────────────────────────────────────────────────────────────

_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")
_BAD_PATH_CHAR_RE = re.compile(r"[\s\x00-\x1f\x7f:]")


@dataclass(frozen=True)
class SshTarget:
    """A parsed `ssh://` URL. `canonical` is what the roster stores."""

    user: str
    host: str
    port: int | None
    # Exactly one of these is set.
    remote_socket: str = ""
    remote_host: str = ""
    remote_port: int | None = None

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def remote(self) -> str:
        """The `-L` right-hand side: a socket path, or `host:port`."""
        if self.remote_socket:
            return self.remote_socket
        host = self.remote_host
        if ":" in host:  # IPv6 literal
            host = f"[{host}]"
        return f"{host}:{self.remote_port}"

    @property
    def canonical(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        auth = (f"{self.user}@" if self.user else "") + host
        if self.port is not None:
            auth += f":{self.port}"
        target = self.remote_socket or "/" + self.remote
        return f"ssh://{auth}{target}"


def is_ssh_url(url: object) -> bool:
    return bool(_SCHEME_RE.match(str(url or "").strip()))


def _parse_port(text: str, what: str) -> int:
    if not text.isdigit():
        raise SshUrlError(f"{what} must be a number (got {text!r})")
    port = int(text)
    if not 1 <= port <= 65535:
        raise SshUrlError(f"{what} must be between 1 and 65535 (got {port})")
    return port


def _split_host_port(text: str, what: str) -> tuple[str, str | None]:
    """`host`, `host:port`, `[v6]`, `[v6]:port` -> (host, port-or-None)."""
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            raise SshUrlError(f"{what} has an unclosed '[' (got {text!r})")
        host, rest = text[1:end], text[end + 1:]
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise SshUrlError(f"{what} [{host}] is not an IPv6 address") from exc
        if not rest:
            return host, None
        if not rest.startswith(":"):
            raise SshUrlError(f"unexpected {rest!r} after [{host}] in {what}")
        return host, rest[1:]
    if text.count(":") > 1:
        raise SshUrlError(f"{what} {text!r}: write an IPv6 address in brackets, e.g. [::1]")
    host, sep, port = text.partition(":")
    return host, (port if sep else None)


def parse_ssh_url(url: object) -> SshTarget:
    """Parse and validate an `ssh://` URL, or raise `SshUrlError` saying why.

    Validation is strict because every part becomes an argument to a process:
    a user or host starting with `-` would be read by ssh as an option.
    """
    text = str(url or "").strip()
    if not _SCHEME_RE.match(text):
        raise SshUrlError(f"not an ssh:// URL: {text!r}")
    rest = text[len("ssh://"):]
    if "?" in rest or "#" in rest:
        raise SshUrlError("an ssh:// source URL cannot have a query string or fragment")
    authority, slash, target = rest.partition("/")
    if not slash or not target.strip("/"):
        raise SshUrlError(
            "an ssh:// URL needs a remote target after the host: a socket path "
            "(ssh://user@host/run/dubis/dubis.sock) or host:port "
            "(ssh://user@host/127.0.0.1:7891)"
        )
    user = ""
    if "@" in authority:
        user, _, authority = authority.rpartition("@")
        if ":" in user:
            raise SshUrlError(
                "an ssh:// URL cannot carry a password — the hub runs ssh with "
                "BatchMode and authenticates with your key or ssh-agent"
            )
        if not _NAME_RE.match(user):
            raise SshUrlError(f"invalid ssh user name {user!r}")
    if not authority:
        raise SshUrlError("an ssh:// URL needs a host")
    host, port_text = _split_host_port(authority, "the ssh host")
    if ":" not in host and not _HOST_RE.match(host):
        raise SshUrlError(f"invalid ssh host {host!r}")
    port = _parse_port(port_text, "the ssh port") if port_text is not None else None

    target = target.rstrip("/")
    if ":" in target:
        if "/" in target:
            raise SshUrlError(
                f"remote target {target!r} has a ':' so it is read as host:port, but it "
                "also contains '/'. A socket path cannot contain ':' (ssh -L cannot "
                "forward one), and host:port has no '/'"
            )
        rhost, rport_text = _split_host_port(target, "the remote target")
        if rport_text is None or not rport_text:
            raise SshUrlError(f"remote target {target!r} needs a port, e.g. 127.0.0.1:7891")
        if ":" not in rhost and not _HOST_RE.match(rhost):
            raise SshUrlError(f"invalid remote host {rhost!r}")
        return SshTarget(user=user, host=host, port=port, remote_host=rhost,
                         remote_port=_parse_port(rport_text, "the remote port"))
    socket_path = "/" + target
    if _BAD_PATH_CHAR_RE.search(socket_path) or "//" in socket_path:
        raise SshUrlError(f"invalid remote socket path {socket_path!r}")
    return SshTarget(user=user, host=host, port=port, remote_socket=socket_path)


def normalize_ssh_url(url: object) -> str:
    """Canonical form of a valid `ssh://` URL, or "" (the `normalize_url` contract)."""
    try:
        return parse_ssh_url(url).canonical
    except SshUrlError:
        return ""


# ── availability ────────────────────────────────────────────────────────────


def find_ssh() -> str | None:
    """The system OpenSSH client, or None. macOS/Linux ship it; Windows 10+
    has it as an optional feature (C:\\Windows\\System32\\OpenSSH)."""
    return shutil.which("ssh")


def available() -> bool:
    """Can this hub open ssh:// tunnels at all? The `browser_page.available()`
    shape: callers branch on it rather than catching a spawn failure."""
    return find_ssh() is not None


# ── stderr -> reason ────────────────────────────────────────────────────────

_CLASSIFIERS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"permission denied|too many authentication failures", re.I), KIND_AUTH,
     "ssh key authentication to {host} was refused. The hub runs ssh with "
     "BatchMode, so it cannot prompt: make sure your public key is in "
     "~/.ssh/authorized_keys on {host}, and if your key has a passphrase, load "
     "it into ssh-agent first (ssh-add)"),
    (re.compile(r"host key verification failed|remote host identification has changed", re.I),
     KIND_HOST_KEY,
     "the host key for {host} does not match ~/.ssh/known_hosts — it changed "
     "since you last connected. Check with whoever runs {host}, then fix "
     "known_hosts (ssh-keygen -R {host})"),
    (re.compile(r"could not resolve hostname|name or service not known|nodename nor servname", re.I),
     KIND_DNS, "cannot resolve {host}"),
    (re.compile(r"connection refused|connection timed out|operation timed out|no route to host|"
                r"network is unreachable|connection closed by|connection reset", re.I),
     KIND_UNREACHABLE, "cannot reach {host} over ssh"),
    (re.compile(r"open failed|connect failed|forwarding failed|could not request local forwarding", re.I),
     KIND_REMOTE_TARGET,
     "ssh to {host} works, but nothing is listening at {remote} on {host} — is "
     "dubIS running there?"),
)


def classify(stderr_lines: Sequence[str], target: SshTarget, returncode: int | None) -> tuple[str, str]:
    """(kind, sentence) for a failed or broken tunnel, from ssh's stderr."""
    text = "\n".join(stderr_lines)
    last = next((ln.strip() for ln in reversed(stderr_lines) if ln.strip()), "")
    for pattern, kind, template in _CLASSIFIERS:
        if pattern.search(text):
            sentence = template.format(host=target.host, remote=target.remote)
            return kind, f"{sentence} (ssh: {last})" if last else sentence
    code = f"exited with code {returncode}" if returncode is not None else "failed"
    return KIND_EXITED, f"ssh to {target.host} {code}" + (f": {last}" if last else "")


# ── the tunnel ──────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _linux_pdeathsig() -> Callable[[], None] | None:
    """`preexec_fn` that asks the kernel to SIGTERM ssh when the hub dies.

    Linux-only (`prctl(PR_SET_PDEATHSIG)`); elsewhere the pidfile reap on the
    next hub start is what catches a hard-killed hub's children.

    **Trap:** the "parent" PDEATHSIG watches is the *thread* that forked, not
    the process. `ensure()` runs on anyio worker threads, which exit after a
    few idle seconds — so a tunnel forked there would be SIGTERMed moments
    after it came up, and look exactly like a flaky network. Every spawn
    therefore goes through `_spawn`, which forks from one long-lived thread.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        import ctypes  # noqa: PLC0415
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return None

    def _set() -> None:
        libc.prctl(1, 15)  # PR_SET_PDEATHSIG, SIGTERM

    return _set


_spawner_lock = threading.Lock()
_spawner: ThreadPoolExecutor | None = None


def _spawn(argv: list[str], kwargs: dict) -> subprocess.Popen[bytes]:
    """`subprocess.Popen`, forked from a thread that lives as long as the process
    whenever a `preexec_fn` (PDEATHSIG) is in play — see `_linux_pdeathsig`."""
    global _spawner
    if "preexec_fn" not in kwargs:
        return subprocess.Popen(argv, **kwargs)  # noqa: S603 — argv, no shell
    with _spawner_lock:
        if _spawner is None:
            # One worker, never idle-reaped: ThreadPoolExecutor workers live
            # until shutdown, unlike anyio's.
            _spawner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ssh-spawner")
        spawner = _spawner
    return spawner.submit(subprocess.Popen, argv, **kwargs).result()


class SshTunnel:
    """One supervised `ssh -N -L` process for one `ssh://` URL."""

    def __init__(
        self,
        target: SshTarget,
        *,
        ssh_command: Sequence[str] | None = None,
        on_change: Callable[[], None] | None = None,
        start_timeout: float = START_TIMEOUT_SECONDS,
    ) -> None:
        self.target = target
        self._ssh_command = list(ssh_command) if ssh_command else None
        self._on_change = on_change or (lambda: None)
        self._start_timeout = start_timeout
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=STDERR_LINES_KEPT)
        self._stderr_seen = 0  # lines already attributed to an earlier failure
        self.local_port: int | None = None
        self.state = STATE_IDLE
        self.error = ""
        self.kind = ""
        self.restarts = 0
        self._ever_up = False
        self._failures = 0
        self._retry_at = 0.0
        self._closed = False

    # -- public -------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def forward_spec(self) -> str:
        return f"127.0.0.1:{self.local_port}:{self.target.remote}"

    def status(self) -> dict:
        """What `GET /v1/sources` reports as this source's `tunnel`."""
        with self._lock:
            self._reconcile()
            return {
                "state": self.state,
                "local_url": f"http://127.0.0.1:{self.local_port}"
                if self.state == STATE_UP and self.local_port else "",
                "error": self.error,
                "kind": self.kind,
                "restarts": self.restarts,
            }

    def ensure(self) -> int:
        """Block until the tunnel is up; return its local port, or raise TunnelError."""
        with self._lock:
            if self._closed:
                raise TunnelError("the hub is shutting down", kind=KIND_EXITED)
            self._reconcile()
            if self.state == STATE_UP:
                return self.local_port  # type: ignore[return-value]
            if self.state == STATE_FAILED and time.monotonic() < self._retry_at:
                raise TunnelError(self.error, kind=self.kind)
            return self._start()

    async def aensure(self) -> int:
        return await anyio.to_thread.run_sync(self.ensure)

    def explain(self, exc: BaseException) -> TunnelError | None:
        """After a transport error through this tunnel: is there a better reason?

        A dead ssh process, or ssh reporting that the *remote* end refused the
        channel ("open failed" — the tunnel is up but dubIS is not listening
        there), explains a `ReadError`/`RemoteProtocolError` far better than
        the error itself. None when ssh has nothing to add.

        Runs on a worker thread (see `TunnelTransport`), so the short wait is
        affordable: ssh writes its "open failed" line a moment AFTER it drops
        the connection that produced our error, and reading stderr the instant
        the error arrives would usually miss it.
        """
        time.sleep(0.15)
        with self._lock:
            self._reconcile()
            if self.state == STATE_FAILED:
                return TunnelError(self.error, kind=self.kind)
            fresh = list(self._stderr)[self._stderr_seen:]
            if fresh:
                kind, message = classify(fresh, self.target, None)
                self._stderr_seen = len(self._stderr)
                if kind != KIND_EXITED:
                    # Recorded on the (still running) tunnel too, so
                    # `GET /v1/sources` can say "ssh works, dubIS is not
                    # there" instead of a clean `up`.
                    self.kind, self.error = kind, message
                    return TunnelError(message, kind=kind)
            return None

    def note_ok(self) -> None:
        """A request made it through: clear a remote-target error from earlier."""
        with self._lock:
            if self.state == STATE_UP and self.kind:
                self.kind, self.error = "", ""

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._terminate()
            self.state = STATE_IDLE

    # -- internals (lock held) ---------------------------------------------

    def _reconcile(self) -> None:
        """Notice an exited process and record why it went."""
        if self._proc is None or self.state != STATE_UP:
            return
        code = self._proc.poll()
        if code is None:
            return
        self._drain_stderr_thread()
        kind, message = classify(list(self._stderr), self.target, code)
        logger.warning("ssh tunnel to %s exited (%s): %s", self.target.canonical, code, message)
        self._proc = None
        self._fail(kind, message, immediate_retry=True)

    def _fail(self, kind: str, message: str, *, immediate_retry: bool = False) -> None:
        self.state = STATE_FAILED
        self.kind = kind
        self.error = message
        self._stderr_seen = len(self._stderr)
        if immediate_retry:
            # A tunnel that WAS up and died (laptop sleep, ServerAlive timeout)
            # is restarted on the very next use — that is the whole feature.
            self._failures = 0
            self._retry_at = 0.0
        else:
            delay = min(BACKOFF_MAX_SECONDS, BACKOFF_INITIAL_SECONDS * (2 ** self._failures))
            self._failures += 1
            self._retry_at = time.monotonic() + delay
        self._on_change()

    def _argv(self, port: int) -> list[str]:
        base = self._ssh_command or [find_ssh() or "ssh"]
        argv = [
            *base,
            "-N",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
            # Trust-on-first-use, exactly what a teammate typing "yes" at the
            # first interactive connection does. A host key that CHANGED is
            # still refused (and reported as KIND_HOST_KEY).
            "-o", "StrictHostKeyChecking=accept-new",
            # Never share or become a ControlMaster from a user's ssh config: a
            # multiplexed -L would outlive this process and escape supervision.
            "-o", "ControlMaster=no",
            "-o", "ControlPath=none",
            "-L", f"127.0.0.1:{port}:{self.target.remote}",
        ]
        if self.target.port is not None:
            argv += ["-p", str(self.target.port)]
        argv += ["--", self.target.destination]
        return argv

    def _start(self) -> int:
        if self._ssh_command is None and not available():
            self._fail(
                KIND_NO_SSH,
                "no ssh client found on this machine — ssh:// servers need OpenSSH "
                "(built into macOS and Linux; on Windows 10+ enable the "
                "\"OpenSSH Client\" optional feature)",
            )
            raise TunnelError(self.error, kind=self.kind)
        self.state = STATE_STARTING
        self._on_change()
        # Reuse the previous port when it is free: same URL in logs, and a
        # restart after sleep looks like the same tunnel. A port taken in the
        # gap between picking and ssh binding makes ssh exit with "Address
        # already in use" (ExitOnForwardFailure) — retried on a fresh port.
        candidates = [self.local_port] if self.local_port else []
        for attempt in range(3):
            port = candidates.pop() if candidates else _free_port()
            result = self._spawn_and_wait(port)
            if result is None:
                if self._ever_up:
                    self.restarts += 1
                    logger.info("ssh tunnel to %s restarted on port %d",
                                self.target.canonical, port)
                self._ever_up = True
                self.local_port = port
                self.state = STATE_UP
                self.error = ""
                self.kind = ""
                self._failures = 0
                self._on_change()
                return port
            kind, message, retry_port = result
            if not retry_port:
                break
        self._fail(kind, message)
        raise TunnelError(message, kind=kind)

    def _spawn_and_wait(self, port: int) -> tuple[str, str, bool] | None:
        """None on success; else (kind, message, retry-on-another-port)."""
        argv = self._argv(port)
        self._stderr.clear()
        self._stderr_seen = 0
        logger.info("ssh tunnel: starting %s", " ".join(argv))
        popen_kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
        }
        preexec = _linux_pdeathsig()
        if preexec is not None:
            popen_kwargs["preexec_fn"] = preexec
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = _spawn(argv, popen_kwargs)
        except OSError as exc:
            return KIND_NO_SSH, f"could not run ssh ({argv[0]}): {exc}", False
        self._proc = proc
        self._reader = threading.Thread(
            target=self._read_stderr, args=(proc,), daemon=True,
            name=f"ssh-stderr-{self.target.host}",
        )
        self._reader.start()
        self._on_change()

        deadline = time.monotonic() + self._start_timeout
        while time.monotonic() < deadline:
            code = proc.poll()
            if code is not None:
                self._drain_stderr_thread()
                lines = list(self._stderr)
                self._proc = None
                if any("address already in use" in ln.lower() for ln in lines):
                    return KIND_EXITED, f"local port {port} was taken", True
                kind, message = classify(lines, self.target, code)
                return kind, message, False
            # ssh binds the local listener only after authentication succeeds,
            # so an accepting port means the tunnel is genuinely up.
            if _port_accepts(port):
                return None
            time.sleep(0.05)
        self._terminate()
        return (KIND_TIMEOUT,
                f"ssh to {self.target.host} did not come up within "
                f"{self._start_timeout:.0f}s", False)

    def _read_stderr(self, proc: subprocess.Popen[bytes]) -> None:
        stream = proc.stderr
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self._stderr.append(line)
                logger.info("ssh[%s]: %s", self.target.host, line)
        with contextlib.suppress(OSError):
            stream.close()

    def _drain_stderr_thread(self) -> None:
        reader = getattr(self, "_reader", None)
        if reader is not None:
            reader.join(timeout=1.0)

    def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=2.0)
        self._on_change()


class TunnelManager:
    """Every tunnel this hub owns, keyed by canonical `ssh://` URL."""

    def __init__(
        self,
        *,
        ssh_command: Sequence[str] | None = None,
        state_dir: str = "",
        start_timeout: float = START_TIMEOUT_SECONDS,
    ) -> None:
        self._ssh_command = list(ssh_command) if ssh_command else None
        self._state_dir = state_dir
        self._start_timeout = start_timeout
        self._tunnels: dict[str, SshTunnel] = {}
        self._lock = threading.Lock()
        self._closed = False
        if state_dir:
            reap_stale(state_dir)
        atexit.register(self.close_all)

    def get(self, url: str) -> SshTunnel:
        target = parse_ssh_url(url)
        key = target.canonical
        with self._lock:
            tunnel = self._tunnels.get(key)
            if tunnel is None:
                tunnel = SshTunnel(target, ssh_command=self._ssh_command,
                                   on_change=self._write_pidfile,
                                   start_timeout=self._start_timeout)
                if self._closed:
                    tunnel.close()
                self._tunnels[key] = tunnel
            return tunnel

    def status(self, url: str) -> dict | None:
        """The tunnel's status, or None for a URL nothing has used yet —
        which reads as `idle`, not as a failure."""
        try:
            key = parse_ssh_url(url).canonical
        except SshUrlError:
            return None
        with self._lock:
            tunnel = self._tunnels.get(key)
        return tunnel.status() if tunnel is not None else None

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            tunnels = list(self._tunnels.values())
        for tunnel in tunnels:
            tunnel.close()
        self._write_pidfile()

    def _write_pidfile(self) -> None:
        if not self._state_dir:
            return
        with self._lock:
            tunnels = list(self._tunnels.values())
        live = [
            {"pid": t.pid, "forward": t.forward_spec}
            for t in tunnels if t.pid is not None and t.local_port
        ]
        path = os.path.join(self._state_dir, PIDFILE_NAME)
        try:
            if live:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(live, f)
                os.replace(tmp, path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.warning("ssh tunnel: could not write %s: %s", path, exc)


def _command_of(pid: int) -> str:
    """The command line of *pid*, or "" if unknowable (Windows, gone)."""
    if os.name != "posix":
        return ""
    try:
        out = subprocess.run(  # noqa: S603
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()


def reap_stale(state_dir: str) -> list[int]:
    """Kill tunnels a previous, hard-killed hub left running. Returns the pids.

    A pid alone is not proof — it may have been reused — so a process is killed
    only when its command line still contains `ssh` AND the exact `-L` forward
    spec that hub recorded. Where the command line cannot be read (Windows) the
    pid is left alone rather than risking someone else's process.
    """
    path = os.path.join(state_dir, PIDFILE_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("ssh tunnel: ignoring unreadable %s: %s", path, exc)
        entries = []
    killed: list[int] = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            pid = int(entry["pid"])
            forward = str(entry["forward"])
        except (KeyError, TypeError, ValueError):
            continue
        command = _command_of(pid)
        if "ssh" in command and forward in command:
            with contextlib.suppress(OSError):
                os.kill(pid, 15)
                killed.append(pid)
                logger.warning("ssh tunnel: killed stale tunnel pid %d (%s)", pid, forward)
    with contextlib.suppress(OSError):
        os.remove(path)
    return killed


# ── httpx transport ─────────────────────────────────────────────────────────


class TunnelTransport(httpx.AsyncBaseTransport):
    """Routes every request through the source's tunnel, starting it if needed.

    The pooled client for an ssh:// source has a placeholder base URL; this
    transport rewrites each request onto `http://127.0.0.1:<local port>` at
    send time. Resolving the port per request — rather than baking it into the
    client — is what lets a restarted tunnel on a different port be used by the
    same pooled client with nobody noticing.
    """

    def __init__(self, tunnel: SshTunnel, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._tunnel = tunnel
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            port = await self._tunnel.aensure()
        except TunnelError as exc:
            exc.request = request
            raise
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=port)
        request.headers["Host"] = f"127.0.0.1:{port}"
        try:
            response = await self._inner.handle_async_request(request)
        except httpx.TransportError as exc:
            better = await anyio.to_thread.run_sync(self._tunnel.explain, exc)
            if better is not None:
                better.request = request
                raise better from exc
            raise
        self._tunnel.note_ok()
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


# Placeholder authority for an ssh:// source's pooled client. `.invalid` is
# reserved (RFC 2606), so should a request ever escape the transport above it
# fails DNS instead of reaching some real host.
PLACEHOLDER_BASE_URL = "http://ssh-tunnel.invalid"
