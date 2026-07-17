"""Run the /v1 server: in-process daemon thread (desktop) or standalone."""

from __future__ import annotations

import os
import threading
import time

import uvicorn

from server.app import create_app
from server.lockfile import acquire_lock


def _port_file_path(data_dir: str) -> str:
    return os.path.join(data_dir, ".v1_port")


def _write_port_file(data_dir: str, port: int) -> None:
    """Atomically write the bound port as plain int text to <data_dir>/.v1_port.

    Write-to-temp-then-os.replace avoids a reader (the MCP client's discovery
    probe) ever observing a partially-written file.
    """
    path = _port_file_path(data_dir)
    tmp_path = f"{path}.tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(str(port))
    os.replace(tmp_path, path)


def _remove_port_file(data_dir: str) -> None:
    """Best-effort removal — a missing file (never started, already removed,
    or a concurrent cleanup) is not an error."""
    try:
        os.remove(_port_file_path(data_dir))
    except OSError:
        pass


def wait_until_started(server: "uvicorn.Server", timeout: float, poll: float = 0.02) -> bool:
    """Poll until *server* has actually bound its socket, or *timeout* elapses.

    Shared by _write_port_file_when_started (here) and
    server/__main__.py::_print_ready_when_started — both need to know when
    the socket is bound before reading
    server.servers[0].sockets[0].getsockname(); factored out so the two
    near-identical poll loops don't drift independently."""
    deadline = time.monotonic() + timeout
    while not server.started and time.monotonic() < deadline:
        time.sleep(poll)
    return server.started


def _write_port_file_when_started(
    server: "uvicorn.Server", data_dir: str, lock=None,
) -> None:
    """Background-thread target: wait for uvicorn to actually bind its socket,
    then write the resolved port to the port file (and, if *lock* is given,
    update the data-dir lockfile's content with that port too — it's
    acquired with port=None in start_server() since the actual port isn't
    known until the socket is bound). Runs off the caller's thread since
    start_server() itself is non-blocking (server.run() runs on its own
    daemon thread) — the bound port isn't known until server.started flips
    true."""
    if not wait_until_started(server, timeout=15):
        return
    port = server.servers[0].sockets[0].getsockname()[1]
    _write_port_file(data_dir, port)
    if lock is not None:
        lock.update_port(port)


def start_server(
    api,
    host: str = "127.0.0.1",
    port: int = 7891,
    static_dir: str | None = None,
    data_dir: str | None = None,
) -> "uvicorn.Server":
    """Start the /v1 server. When *data_dir* is given, acquires the
    exclusive data-dir lock (server/lockfile.py) BEFORE spinning up
    uvicorn — raises DataDirLockedError synchronously if another server
    already holds it, so a second in-process caller (or app.pyw's boot
    thread) fails fast instead of silently racing the first server's
    writes to the same CSVs/cache.db."""
    lock = acquire_lock(data_dir) if data_dir is not None else None
    config = uvicorn.Config(create_app(api, static_dir=static_dir), host=host, port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    server._dubis_lock = lock
    thread = threading.Thread(target=server.run, name="dubis-v1-server", daemon=True)
    thread.start()
    if data_dir is not None:
        threading.Thread(
            target=_write_port_file_when_started,
            args=(server, data_dir, lock),
            name="dubis-v1-port-file",
            daemon=True,
        ).start()
    return server


def stop_server(server: "uvicorn.Server", data_dir: str | None = None) -> None:
    server.should_exit = True
    if data_dir is not None:
        _remove_port_file(data_dir)
    lock = getattr(server, "_dubis_lock", None)
    if lock is not None:
        lock.release()
