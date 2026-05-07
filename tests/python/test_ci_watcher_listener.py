"""Tests for scripts.ci_watcher.listener."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.ci_watcher.listener import create_app

SECRET = "test-secret"


@pytest.fixture
def client(tmp_path: Path):
    socket_path = tmp_path / "queue.sock"
    state_path = tmp_path / "state.db"
    app = create_app(
        secret=SECRET,
        socket_path=socket_path,
        state_path=state_path,
    )
    app.testing = True
    return app.test_client()


def _sign(body: bytes) -> str:
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _payload(action="completed", conclusion="failure", run_id=1, attempt=1) -> dict:
    return {
        "action": action,
        "workflow_run": {
            "id": run_id,
            "run_attempt": attempt,
            "conclusion": conclusion,
            "name": "CI",
            "head_sha": "abc1234",
            "head_branch": "claude/foo",
        },
        "repository": {"full_name": "dubnubdubnub/dubIS"},
    }


def test_health_returns_200(client) -> None:
    rv = client.get("/health")
    assert rv.status_code == 200


def test_valid_signature_with_failure_enqueues(client) -> None:
    body = json.dumps(_payload()).encode()
    with patch("scripts.ci_watcher.listener._send_to_socket") as send:
        rv = client.post(
            "/webhook",
            data=body,
            headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "workflow_run"},
        )
    assert rv.status_code == 200
    assert send.called


def test_invalid_signature_rejected(client) -> None:
    body = json.dumps(_payload()).encode()
    rv = client.post(
        "/webhook",
        data=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "X-GitHub-Event": "workflow_run"},
    )
    assert rv.status_code == 401


def test_missing_signature_rejected(client) -> None:
    body = json.dumps(_payload()).encode()
    rv = client.post("/webhook", data=body, headers={"X-GitHub-Event": "workflow_run"})
    assert rv.status_code == 401


def test_success_conclusion_filtered(client) -> None:
    body = json.dumps(_payload(conclusion="success")).encode()
    with patch("scripts.ci_watcher.listener._send_to_socket") as send:
        rv = client.post(
            "/webhook",
            data=body,
            headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "workflow_run"},
        )
    assert rv.status_code == 200
    send.assert_not_called()


def test_action_requested_filtered(client) -> None:
    body = json.dumps(_payload(action="requested")).encode()
    with patch("scripts.ci_watcher.listener._send_to_socket") as send:
        rv = client.post(
            "/webhook",
            data=body,
            headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "workflow_run"},
        )
    assert rv.status_code == 200
    send.assert_not_called()


def test_dedupe_drops_repeats(client) -> None:
    body = json.dumps(_payload(run_id=99, attempt=1)).encode()
    headers = {"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "workflow_run"}
    with patch("scripts.ci_watcher.listener._send_to_socket") as send:
        rv1 = client.post("/webhook", data=body, headers=headers)
        rv2 = client.post("/webhook", data=body, headers=headers)
    assert rv1.status_code == rv2.status_code == 200
    assert send.call_count == 1


def test_non_workflow_run_event_ignored(client) -> None:
    body = json.dumps({"action": "opened"}).encode()
    with patch("scripts.ci_watcher.listener._send_to_socket") as send:
        rv = client.post(
            "/webhook",
            data=body,
            headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
        )
    assert rv.status_code == 200
    send.assert_not_called()
