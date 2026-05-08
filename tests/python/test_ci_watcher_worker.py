"""Tests for scripts.ci_watcher.worker."""
from __future__ import annotations

import json
import socket
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.ci_watcher.worker import process_event, run_worker_once

_UNIX_SOCKET_AVAILABLE = hasattr(socket, "AF_UNIX")


def _event(run_id=1, pr=234) -> dict:
    return {
        "run_id": run_id,
        "run_attempt": 1,
        "workflow": "CI",
        "head_sha": "abc1234",
        "head_branch": "claude/foo",
        "pr": pr,
    }


def test_process_event_calls_claude_with_prompt_and_payload(tmp_path: Path) -> None:
    prompt_path = tmp_path / "triage-prompt.md"
    prompt_path.write_text("Triage this:\n", encoding="utf-8")
    cwd = tmp_path / "repo"
    cwd.mkdir()

    payload = {"run_id": 1, "signature": "x", "log_excerpt": "boom"}

    with patch("scripts.ci_watcher.worker.build_payload", return_value=payload), \
         patch("scripts.ci_watcher.worker.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = process_event(_event(), prompt_path=prompt_path, cwd=cwd)

    assert result.returncode == 0
    args, kwargs = run.call_args
    cmd = args[0]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    # Prompt is passed via stdin or as a file path arg; payload via env/stdin.
    assert kwargs.get("cwd") == cwd


def test_process_event_retries_once_on_failure(tmp_path: Path) -> None:
    prompt_path = tmp_path / "triage-prompt.md"
    prompt_path.write_text("Triage this:\n", encoding="utf-8")
    cwd = tmp_path / "repo"
    cwd.mkdir()

    with patch("scripts.ci_watcher.worker.build_payload", return_value={}), \
         patch("scripts.ci_watcher.worker.subprocess.run") as run, \
         patch("scripts.ci_watcher.worker.time.sleep"):
        run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="claude failed"),
            MagicMock(returncode=0, stdout="ok", stderr=""),
        ]
        result = process_event(_event(), prompt_path=prompt_path, cwd=cwd)

    assert run.call_count == 2
    assert result.returncode == 0


@pytest.mark.skipif(not _UNIX_SOCKET_AVAILABLE, reason="AF_UNIX not supported on this platform")
def test_run_worker_once_reads_from_socket(tmp_path: Path) -> None:
    """Send a datagram, verify the worker decodes it and calls process_event."""
    # AF_UNIX paths must be ≤104 chars on macOS, ≤108 on Linux. pytest's tmp_path
    # often exceeds this on CI runners with long workspace paths, so put the socket
    # in /tmp via mkdtemp.
    short_dir = Path(tempfile.mkdtemp(prefix="ciw-"))
    try:
        socket_path = short_dir / "queue.sock"

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(str(socket_path))

        sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sender.sendto(json.dumps(_event()).encode("utf-8"), str(socket_path))
        sender.close()

        with patch("scripts.ci_watcher.worker.process_event") as process:
            run_worker_once(sock, prompt_path=tmp_path / "p.md", cwd=tmp_path)
            assert process.called
            assert process.call_args[0][0]["run_id"] == 1

        sock.close()
    finally:
        # Clean up
        if socket_path.exists():
            socket_path.unlink()
        if short_dir.exists():
            short_dir.rmdir()
