"""Tests for scripts.ci_watcher.audit."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts.ci_watcher.audit import AuditLog, TriageRecord


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "ci-watcher-log.jsonl"


def _record(**overrides) -> TriageRecord:
    base = dict(
        ts="2026-05-06T14:23:11Z",
        run_id=1,
        run_attempt=1,
        workflow="CI",
        job="playwright",
        pr=234,
        head_sha="abc1234",
        classification="pipeline",
        signature="ci|playwright|browser launch",
        action="rerun",
        rerun_count=1,
        fix_pushed=False,
        comment_url=None,
        claude_run_dur_sec=27,
    )
    base.update(overrides)
    return TriageRecord(**base)


def test_append_and_read_round_trip(log_path: Path) -> None:
    log = AuditLog(log_path)
    log.append(_record())
    log.append(_record(run_id=2))
    records = list(log.read_all())
    assert len(records) == 2
    assert records[0].run_id == 1
    assert records[1].run_id == 2


def test_append_creates_valid_jsonl(log_path: Path) -> None:
    AuditLog(log_path).append(_record())
    raw = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 1
    parsed = json.loads(raw[0])
    assert parsed["run_id"] == 1
    assert parsed["classification"] == "pipeline"


def test_count_signature_filters_by_pr_and_window(log_path: Path) -> None:
    log = AuditLog(log_path)
    now = int(time.time())
    week_ago_plus = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 8 * 86400))
    in_window = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))

    sig = "ci|playwright|launch"
    log.append(_record(signature=sig, pr=1, ts=in_window))
    log.append(_record(signature=sig, pr=2, ts=in_window))
    log.append(_record(signature=sig, pr=3, ts=in_window))
    log.append(_record(signature=sig, pr=4, ts=week_ago_plus))  # outside window
    log.append(_record(signature="other", pr=5, ts=in_window))  # different sig

    distinct = log.distinct_prs_with_signature(sig, within_seconds=7 * 86400)
    assert distinct == 3


def test_count_reruns_for_pr(log_path: Path) -> None:
    log = AuditLog(log_path)
    sig = "ci|playwright|launch"
    log.append(_record(signature=sig, pr=234, action="rerun"))
    log.append(_record(signature=sig, pr=234, action="rerun"))
    log.append(_record(signature=sig, pr=234, action="comment-only"))
    log.append(_record(signature=sig, pr=999, action="rerun"))  # different PR

    assert log.rerun_count_for_pr_signature(pr=234, signature=sig) == 2
