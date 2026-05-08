"""Tests for scripts.ci_watcher.triage_payload."""
from __future__ import annotations

import json
from unittest.mock import patch

from scripts.ci_watcher.triage_payload import (
    build_payload,
    derive_signature,
    trim_log,
)


def test_trim_log_keeps_last_n_chars() -> None:
    text = "a" * 100 + "TARGET" + "b" * 100
    trimmed = trim_log(text, max_chars=50)
    assert "TARGET" in trimmed or trimmed.endswith("b" * 50)
    assert len(trimmed) <= 100  # 50 + truncation marker


def test_trim_log_passes_through_short_input() -> None:
    assert trim_log("hello", max_chars=8000) == "hello"


def test_trim_log_adds_marker_when_truncated() -> None:
    text = "x" * 9000
    trimmed = trim_log(text, max_chars=100)
    assert trimmed.startswith("[truncated")


def test_derive_signature_strips_timestamps_and_pids() -> None:
    log = (
        "2026-05-06T14:23:11.123Z [pid 12345] Error: ECONNRESET reading from api.github.com\n"
        "    at /Users/runner/work/dubIS/dubIS/scripts/foo.py:42\n"
    )
    sig = derive_signature(workflow="CI", job="lint", log_excerpt=log)
    assert sig.startswith("CI|lint|")
    assert "12345" not in sig
    assert "2026-05-06" not in sig
    assert "ECONNRESET" in sig


def test_derive_signature_strips_absolute_paths() -> None:
    log = "AssertionError at /Users/runner/work/dubIS/dubIS/tests/foo.py:99\n"
    sig = derive_signature(workflow="CI", job="lint", log_excerpt=log)
    assert "/Users/runner" not in sig


def test_build_payload_calls_gh_with_correct_args() -> None:
    with patch("scripts.ci_watcher.triage_payload._run_gh") as run_gh:
        run_gh.side_effect = [
            json.dumps({
                "status": "completed",
                "conclusion": "failure",
                "headSha": "abc1234",
                "headBranch": "claude/foo",
                "event": "pull_request",
                "workflowName": "CI",
                "jobs": [{"name": "lint", "conclusion": "failure"}],
            }),
            "fake log content",
            json.dumps({
                "number": 234,
                "headRefName": "claude/foo",
                "title": "feat: foo",
                "author": {"login": "isaac"},
                "labels": [],
            }),
        ]
        payload = build_payload(run_id=1234, pr=234)

    assert payload["run_id"] == 1234
    assert payload["pr"] == 234
    assert payload["workflow"] == "CI"
    assert payload["head_sha"] == "abc1234"
    assert payload["log_excerpt"] == "fake log content"
    assert payload["signature"].startswith("CI|lint|")


def test_build_payload_handles_no_pr() -> None:
    """Non-PR runs (push to main, schedule) should still yield a payload."""
    with patch("scripts.ci_watcher.triage_payload._run_gh") as run_gh:
        run_gh.side_effect = [
            json.dumps({
                "status": "completed",
                "conclusion": "failure",
                "headSha": "abc1234",
                "headBranch": "main",
                "event": "push",
                "workflowName": "CI",
                "jobs": [{"name": "lint", "conclusion": "failure"}],
            }),
            "fake log content",
        ]
        payload = build_payload(run_id=1234, pr=None)

    assert payload["pr"] is None
    assert "pr_meta" not in payload or payload["pr_meta"] is None
