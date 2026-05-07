"""Flask webhook receiver for GitHub workflow_run events.

Verifies HMAC, filters on action=completed AND conclusion=failure, dedupes
via sqlite, then writes the event to a Unix socket the worker reads from.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
from pathlib import Path
from typing import Any

from flask import Flask, request

from scripts.ci_watcher.state import State


def _send_to_socket(socket_path: Path, payload: dict[str, Any]) -> None:
    """Send a JSON-encoded event to the worker via Unix datagram socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.settimeout(5)
        sock.sendto(json.dumps(payload).encode("utf-8"), str(socket_path))
    finally:
        sock.close()


def _verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


def create_app(*, secret: str, socket_path: Path, state_path: Path) -> Flask:
    app = Flask(__name__)
    state = State(state_path)

    @app.get("/health")
    def health():
        return {"ok": True}, 200

    @app.post("/webhook")
    def webhook():
        body = request.get_data()
        if not _verify_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
            app.logger.warning("rejected: bad/missing signature from %s", request.remote_addr)
            return {"error": "bad signature"}, 401

        event = request.headers.get("X-GitHub-Event", "")
        if event != "workflow_run":
            return {"ignored": "non-workflow_run event"}, 200

        payload = json.loads(body)
        if payload.get("action") != "completed":
            return {"ignored": "action != completed"}, 200

        run = payload.get("workflow_run") or {}
        if run.get("conclusion") != "failure":
            return {"ignored": "conclusion != failure"}, 200

        run_id = int(run.get("id", 0))
        attempt = int(run.get("run_attempt", 1))
        if state.is_seen(run_id=run_id, attempt=attempt):
            return {"ignored": "duplicate"}, 200

        event_to_worker = {
            "run_id": run_id,
            "run_attempt": attempt,
            "workflow": run.get("name", ""),
            "head_sha": run.get("head_sha", ""),
            "head_branch": run.get("head_branch", ""),
            "pr": _extract_pr(run),
        }
        try:
            _send_to_socket(socket_path, event_to_worker)
        except Exception:
            app.logger.exception("failed to enqueue run_id=%s; will retry on GitHub redelivery", run_id)
            raise  # let Flask 500 so GitHub retries

        state.mark_seen(run_id=run_id, attempt=attempt)
        return {"queued": True, "run_id": run_id}, 200

    return app


def _extract_pr(run: dict[str, Any]) -> int | None:
    """workflow_run payloads include pull_requests[] for PR-triggered runs."""
    prs = run.get("pull_requests") or []
    if prs and isinstance(prs[0], dict):
        return int(prs[0].get("number", 0)) or None
    return None


if __name__ == "__main__":
    secret = Path("/etc/ci-watcher/secret").read_text(encoding="utf-8").strip()
    socket_path = Path("/var/run/ci-watcher.sock")
    state_path = Path("/var/lib/ci-watcher/state.db")
    app = create_app(secret=secret, socket_path=socket_path, state_path=state_path)
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "9090")))
