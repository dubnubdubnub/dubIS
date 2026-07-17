"""Sync HTTP client for the dubIS /v1 API, with server discovery.

Discovery order (binding: docs/plans/2026-07-16-phase2-mcp-server-design.md):
  1. ``DUBIS_URL`` env var — explicit override (e.g. a tailnet server later).
  2. Port file ``<repo_root>/data/.v1_port`` — health-checked via
     ``GET /v1/health`` returning exactly ``{"ok": true}`` (JSON-validated,
     not status-only — a stale file left behind by a crashed server points at
     a dead or unrelated port and must be ignored, not trusted).
  3. Spawned fallback: ``python -m server --data-dir <repo_root>/data --port 0``,
     parsed off the child's ``READY:<port>`` stdout line (the same contract
     ``server/__main__.py`` already prints for Playwright). The child is kept
     as a module-global handle and terminated at atexit — ``shutdown_spawned()``
     is also exposed for callers/tests that want to tear it down eagerly
     rather than waiting for process exit.

The spawn step never overrides the subprocess's cwd: it inherits this
process's cwd, which must be the dubIS repo root for `-m server` to resolve
— the same assumption tools/dev-tools-mcp/server.py makes via Path.cwd().
"""

from __future__ import annotations

import atexit
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx


class V1Error(Exception):
    """Raised for any non-2xx /v1 response; carries the server's message."""

    def __init__(self, message: str, status: int):
        super().__init__(f"/v1 error {status}: {message}")
        self.message = message
        self.status = status


class V1Client:
    """Thin sync HTTP client over the /v1 API.

    Attaches ``Authorization: Bearer <token>`` to every request when a token
    is passed (or, via `connect()`, when the ``DUBIS_TOKEN`` env var is set) —
    Phase 1c Task 7 (docs/plans/2026-07-16-phase1c-remote-deploy-design.md
    §7): "MCP server: v1client gains an Authorization header from DUBIS_TOKEN
    env when set". This is for headless clients only (CI, OpenPnP, MCP) —
    browsers reach a tailnet-fronted server via tailnet identity instead; see
    app.pyw's remote-mode comment for that split.
    """

    def __init__(
        self,
        base_url: str,
        discovered_via: str = "env",
        timeout: float = 10.0,
        token: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.discovered_via = discovered_via
        headers = {"Authorization": f"Bearer {token}"} if token else None
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers)

    def get(self, path: str, **params):
        resp = self._client.get(path, params=params or None)
        return _unwrap(resp)

    def post(self, path: str, json: dict | list | None = None):
        resp = self._client.post(path, json=json)
        return _unwrap(resp)

    def close(self) -> None:
        self._client.close()


def _unwrap(resp: httpx.Response):
    if resp.status_code >= 400:
        message = resp.text
        try:
            body = resp.json()
            if isinstance(body, dict):
                message = body.get("error") or body.get("detail") or message
        except ValueError:
            pass
        raise V1Error(message, resp.status_code)
    if not resp.content:
        return {}
    return resp.json()


def _is_healthy(base_url: str, timeout: float = 1.0) -> bool:
    try:
        resp = httpx.get(f"{base_url}/v1/health", timeout=timeout)
    except httpx.TransportError:
        return False
    if resp.status_code != 200:
        return False
    try:
        return resp.json() == {"ok": True}
    except ValueError:
        return False


def _port_file_path(data_dir: str) -> Path:
    return Path(data_dir) / ".v1_port"


def _read_port_file(data_dir: str) -> int | None:
    try:
        return int(_port_file_path(data_dir).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


_spawned_process: subprocess.Popen | None = None
_spawn_lock = threading.Lock()
_atexit_registered = False


def _spawn_server(data_dir: str, timeout: float = 30.0) -> str:
    """Spawn `python -m server --data-dir <data_dir> --port 0`, parse the
    READY:<port> stdout line, and return its base_url.

    Reads stdout on a background thread into a queue so the wait loop can
    respect `timeout` even if the child never writes anything (a blocking
    readline() in the main thread would hang forever instead).
    """
    global _spawned_process, _atexit_registered

    os.makedirs(data_dir, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "server", "--data-dir", data_dir, "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    with _spawn_lock:
        _spawned_process = proc
        if not _atexit_registered:
            atexit.register(shutdown_spawned)
            _atexit_registered = True

    lines: queue.Queue[str] = queue.Queue()
    captured: list[str] = []

    def _reader() -> None:
        try:
            for line in proc.stdout:
                captured.append(line)
                lines.put(line)
        except ValueError:
            pass  # pipe closed underneath us during shutdown

    threading.Thread(target=_reader, name="dubis-mcp-spawn-reader", daemon=True).start()

    deadline = time.monotonic() + timeout
    port: int | None = None
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.2).strip()
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line.startswith("READY:"):
            port = int(line.split(":", 1)[1])
            break

    if port is None:
        shutdown_spawned()
        output = "".join(captured).rstrip()
        detail = f"; captured output:\n{output}" if output else "; no output was captured"
        raise RuntimeError(
            f"spawned `python -m server --data-dir {data_dir}` never printed "
            f"READY:<port> within the timeout{detail}"
        )

    return f"http://127.0.0.1:{port}"


def shutdown_spawned() -> None:
    """Terminate the spawned server child, if any. Idempotent; best-effort —
    Windows child-process cleanup is a known atexit-doesn't-run-on-hard-kill
    limitation (acceptable here, see the design doc's Risks section)."""
    global _spawned_process
    with _spawn_lock:
        proc = _spawned_process
        _spawned_process = None
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def connect(repo_root: str) -> V1Client:
    """Discover a running /v1 server and return a connected V1Client.

    Order: DUBIS_URL env -> <repo_root>/data/.v1_port (health-checked) ->
    spawned fallback (`python -m server --data-dir <repo_root>/data --port 0`).
    """
    token = os.environ.get("DUBIS_TOKEN") or None

    env_url = os.environ.get("DUBIS_URL")
    if env_url:
        return V1Client(env_url, discovered_via="env", token=token)

    data_dir = os.path.join(repo_root, "data")
    port = _read_port_file(data_dir)
    if port is not None:
        base_url = f"http://127.0.0.1:{port}"
        if _is_healthy(base_url):
            return V1Client(base_url, discovered_via="port_file", token=token)
        # Stale port file (dead port, or a crashed server's leftover) — ignore
        # and fall through to spawning rather than trusting it.

    base_url = _spawn_server(data_dir)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not _is_healthy(base_url):
        time.sleep(0.1)
    return V1Client(base_url, discovered_via="spawned", token=token)
