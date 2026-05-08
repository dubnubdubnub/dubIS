"""Tests for scripts.ci_watcher.state."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci_watcher.state import State


@pytest.fixture
def state(tmp_path: Path) -> State:
    return State(tmp_path / "state.db")


def test_first_seen_returns_false_then_true(state: State) -> None:
    assert state.is_seen(run_id=1, attempt=1) is False
    state.mark_seen(run_id=1, attempt=1)
    assert state.is_seen(run_id=1, attempt=1) is True


def test_distinct_attempts_are_tracked_independently(state: State) -> None:
    state.mark_seen(run_id=1, attempt=1)
    assert state.is_seen(run_id=1, attempt=2) is False
    state.mark_seen(run_id=1, attempt=2)
    assert state.is_seen(run_id=1, attempt=2) is True


def test_mark_seen_is_idempotent(state: State) -> None:
    state.mark_seen(run_id=1, attempt=1)
    state.mark_seen(run_id=1, attempt=1)  # no-op
    assert state.is_seen(run_id=1, attempt=1) is True


def test_state_persists_across_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    State(db_path).mark_seen(run_id=42, attempt=1)
    assert State(db_path).is_seen(run_id=42, attempt=1) is True
