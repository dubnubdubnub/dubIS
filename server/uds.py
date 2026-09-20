"""Unix-domain-socket transport for the `/v1` server.

Two jobs, both only ever used by `server/__main__.py --uds`:

1. `PeerCredHTTPProtocol` — the uvicorn HTTP protocol subclass that resolves
   the connecting user *once per connection* and makes the answer visible to
   `server/auth.py`.
2. `prepare_socket_path` / `remove_socket_path` — the socket file's lifecycle,
   which uvicorn does not manage for you (it neither cleans up on shutdown nor
   distinguishes a stale socket file from a live one).

Why a protocol subclass, and not middleware
-------------------------------------------
Peer credentials live on the *socket*, and the socket is not in the ASGI
scope. This was verified against the installed uvicorn (0.52.4) rather than
assumed: both HTTP implementations build their scope in one literal —
`h11_impl.py` in `handle_events`, `httptools_impl.py` in `on_message_begin` —
and neither includes the transport, the raw socket, or an `extensions` entry
for them. `scope["client"]` is `None` for a Unix socket, because uvicorn's
`get_remote_addr()` returns None when `getpeername()` is not a `(host, port)`
tuple, which it never is for AF_UNIX. So no ASGI middleware can reach the
credential, however it is written.

What both implementations *do* share is `self.app_state`, assigned once in
`__init__` and copied into every scope this connection produces
(`"state": self.app_state.copy()`). Rebinding that attribute in
`connection_made` therefore decorates exactly the requests of one connection
and nothing else — no global, no contextvar, no per-request lookup table
keyed by something forgeable. Starlette surfaces the same dict as
`request.state`, which is how `server/auth.py` reads it back.

The rebind is a copy, never a mutation: `app_state` is otherwise shared
across every connection (it is the ASGI lifespan state), and stamping one
caller's username into the shared dict would leak it to everybody else's
requests. dubIS puts nothing in lifespan state today, so the loss of sharing
for UDS connections costs nothing; if that ever changes, this is the comment
that explains the copy.

Why not `--uds` in `server/run.py`
-----------------------------------
`server/run.py::start_server` is the desktop app's path. The desktop app's
pywebview window navigates to an `http://` URL, and a Unix socket has no URL
a browser can load, so the desktop can never use this transport. Rather than
add a parameter nothing passes, `start_server()` is left exactly as it was;
only the `.v1_uds` discovery-file helpers live alongside their `.v1_port`
counterparts there, because that is where every discovery file is written.
"""

from __future__ import annotations

import logging
import os
import socket
import stat

from uvicorn.protocols.http.auto import AutoHTTPProtocol

from server import peercred

logger = logging.getLogger(__name__)

_MISSING = object()


class PeerCredHTTPProtocol(AutoHTTPProtocol):
    """uvicorn's HTTP protocol, plus a per-connection peer identity.

    Passed as `uvicorn.Config(http=...)`, which is public uvicorn API. The
    base class is whatever `uvicorn.protocols.http.auto` resolved to
    (httptools when installed, else h11) so this inherits the same
    implementation a default run would have used.

    A TCP connection is left completely untouched:
    `peercred.identity_for_socket` returns None for anything that is not
    AF_UNIX, and nothing is stashed. That is deliberate — this class is safe
    to install unconditionally, and the AF_UNIX check, not the caller's
    choice of when to install it, is what closes the spoofing path.
    """

    def connection_made(self, transport) -> None:
        super().connection_made(transport)

        identity = peercred.identity_for_socket(transport.get_extra_info("socket"))
        if identity is None:
            return  # TCP (or an unreadable transport): behave exactly as before.

        app_state = getattr(self, "app_state", _MISSING)
        if not isinstance(app_state, dict):
            # uvicorn moved the per-connection ASGI state out from under us.
            # Throw loudly (repo error policy) rather than carry on and hand
            # every Unix-socket caller a silent 401 that looks like a broken
            # server.
            raise RuntimeError(
                "server/uds.py: this uvicorn build has no dict `app_state` on its HTTP "
                "protocol, so the peer identity cannot be attached to the ASGI scope. "
                "Unix-socket identity is broken until server/uds.py is updated for it."
            )
        self.app_state = {**app_state, peercred.PEER_IDENTITY_KEY: identity}


class SocketPathInUseError(OSError):
    """`--uds <path>` where something is already listening on that path.

    Distinct from a stale socket file, which `prepare_socket_path` removes:
    this one means a live process owns the path and starting would either
    fail with EADDRINUSE or (worse) steal traffic from it.
    """


def prepare_socket_path(path: str) -> None:
    """Make *path* bindable, or raise saying why it is not.

    uvicorn's `create_unix_server` refuses to bind a path that already
    exists, and a server that did not exit cleanly leaves its socket file
    behind — so without this, one such exit makes every later `--uds` start
    fail with a bare EADDRINUSE.

    "Did not exit cleanly" is not rare, and not only SIGKILL: uvicorn's
    `capture_signals` re-raises the signal it caught once shutdown is
    complete, so a SIGTERM (systemd, `docker stop`, `kill`) kills the process
    with the default handler and **no atexit hook runs** — the same reason a
    TCP server's `.v1_port` survives a SIGTERM today. SIGINT (Ctrl-C on a
    foreground run) raises KeyboardInterrupt instead and does run the
    teardown. So this function, not the teardown, is what makes `--uds`
    reliably restartable.

    The standard disambiguation is to try connecting:

    - nothing there -> nothing to do;
    - a socket file that refuses the connection -> stale, unlink it;
    - a socket file that accepts -> a live server, raise;
    - anything that is not a socket -> raise, and never unlink. Deleting a
      regular file because it was named as `--uds` would be a typo turning
      into data loss.

    Only called after `server/__main__.py` has taken the data-dir lock, so a
    second dubIS server on the same data dir has already been refused by
    `server/lockfile.py` before we get anywhere near unlinking anything.
    """
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        return

    if not stat.S_ISSOCK(mode):
        raise SocketPathInUseError(
            f"--uds {path!r} exists and is not a socket; refusing to replace it"
        )

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        probe.connect(path)
    except (ConnectionRefusedError, FileNotFoundError):
        logger.warning("removing stale socket file %s", path)
        os.unlink(path)
        return
    except OSError as exc:
        raise SocketPathInUseError(
            f"--uds {path!r} exists and could not be probed ({exc}); "
            "remove it by hand if you are sure no server is using it"
        ) from exc
    else:
        raise SocketPathInUseError(
            f"--uds {path!r} is already accepting connections — another server "
            "is listening there"
        )
    finally:
        probe.close()


def remove_socket_path(path: str) -> None:
    """Unlink the socket file on shutdown. Best-effort, like
    `server/run.py::_remove_port_file` — uvicorn does not do this itself, and
    a missing file (never bound, already cleaned up) is not an error."""
    try:
        os.unlink(path)
    except OSError:
        pass
