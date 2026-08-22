"""Sync HTTP client for the dubIS /v1 API, with server discovery.

Discovery order:
  1. ``DUBIS_URL`` env var — explicit override (e.g. a tailnet server).
  2. Port file ``<data_dir>/.v1_port`` — health-checked via ``GET /v1/health``
     returning exactly ``{"ok": true}`` (JSON-validated, not status-only — a
     stale file left behind by a crashed server points at a dead or unrelated
     port and must be ignored, not trusted).

There is deliberately NO third "spawn a server" step. The retired
tools/dubis-mcp had one, and it was correct there: an MCP server is one
long-lived process per agent session, so a single spawn amortized across
every tool call. A CLI is one process per *invocation*, which inverts the
cost model:

  * Every command would pay spawn->ready->teardown (~0.57s floor: interpreter,
    import graph, uvicorn boot, teardown) before doing any work.
  * Worse, every spawn takes the data-dir lock (server/lockfile.py). Two
    concurrently-invoked commands — routine when an agent issues parallel
    tool calls — would make the second fail with DataDirLockedError against a
    server the first one just started. Phase 1c's lockfile closed the
    data-corruption race; it does not close this one, which is created by the
    short process lifetime, not by shared data.
  * ``atexit`` does not run on hard kill, so a Ctrl-C'd command could leave a
    lock-holding orphan behind and break the next desktop launch with no
    visible cause.

So discovery fails loudly instead: ``connect()`` raises NoServerFoundError,
which the CLI maps to exit 4 with a message naming ``dubis serve``.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx


class V1Error(Exception):
    """Raised for any non-2xx /v1 response; carries the server's message."""

    def __init__(self, message: str, status: int):
        super().__init__(f"/v1 error {status}: {message}")
        self.message = message
        self.status = status


class NoServerFoundError(Exception):
    """Raised by connect() when no running /v1 server can be discovered.

    Carries the data_dir that was probed so the CLI's exit-4 message can name
    it. This is the first error a user hits on a fresh machine, so the wording
    is load-bearing — it is asserted in tests, not left to drift.
    """

    def __init__(self, data_dir: str):
        super().__init__(
            "no /v1 server found. start one with `dubis serve`, or set DUBIS_URL "
            f"(probed {os.path.join(data_dir, '.v1_port')})"
        )
        self.data_dir = data_dir


class V1Client:
    """Thin sync HTTP client over the /v1 API.

    Attaches ``Authorization: Bearer <token>`` to every request when a token
    is passed (or, via `connect()`, when the ``DUBIS_TOKEN`` env var is set).
    This is for headless clients only (CI, OpenPnP, the CLI) — browsers reach
    a tailnet-fronted server via tailnet identity instead; see app.pyw's
    remote-mode comment for that split.
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

    def put(self, path: str, json: dict | list | None = None):
        resp = self._client.put(path, json=json)
        return _unwrap(resp)

    def patch(self, path: str, json: dict | list | None = None):
        resp = self._client.patch(path, json=json)
        return _unwrap(resp)

    def delete(self, path: str, **params):
        resp = self._client.request("DELETE", path, params=params or None)
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


def default_data_dir(repo_root: str) -> str:
    """The data directory every dubIS client agrees on: ``<repo_root>/data``.

    Exported rather than inlined because `dubis serve` must start a server in
    the SAME directory `connect()` probes. `python -m server` defaults its own
    --data-dir to "." (the repo root), so a serve that let that default stand
    would write `.v1_port`/`.dubis_lock` one level above where every other
    command looks — the two would never find each other, and the repo root
    would collect stray lock files.
    """
    return os.path.join(repo_root, "data")


def _port_file_path(data_dir: str) -> Path:
    return Path(data_dir) / ".v1_port"


def _read_port_file(data_dir: str) -> int | None:
    try:
        return int(_port_file_path(data_dir).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def connect(repo_root: str, data_dir: str | None = None) -> V1Client:
    """Discover a running /v1 server and return a connected V1Client.

    Order: ``DUBIS_URL`` env -> ``<data_dir>/.v1_port`` (health-checked).
    Raises NoServerFoundError if neither resolves — this never starts a
    server; see the module docstring for why.

    *data_dir* defaults to ``<repo_root>/data``, and is overridable so
    ``dubis --data-dir X`` probes the same directory it would serve.
    """
    token = os.environ.get("DUBIS_TOKEN") or None

    env_url = os.environ.get("DUBIS_URL")
    if env_url:
        return V1Client(env_url, discovered_via="env", token=token)

    resolved_dir = data_dir if data_dir is not None else default_data_dir(repo_root)
    port = _read_port_file(resolved_dir)
    if port is not None:
        base_url = f"http://127.0.0.1:{port}"
        if _is_healthy(base_url):
            return V1Client(base_url, discovered_via="port_file", token=token)
        # Stale port file (dead port, or a crashed server's leftover) — ignore
        # it rather than handing back a client pointed at nothing.

    raise NoServerFoundError(resolved_dir)
