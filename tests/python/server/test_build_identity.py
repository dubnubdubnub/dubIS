"""Build identity: the resolver's precedence, and `/v1/health` reporting it.

`/v1/health` must say which build is answering, so verifying a fremont roll
is one curl instead of six cluster commands. The resolver
(server/build_identity.py) never guesses — baked image value, then
DUBIS_BUILD_SHA, then `git rev-parse HEAD`, then the literal "unknown" — and
this file pins each tier beating the ones below it, plus the route's field.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import server as server_pkg
from server import build_identity

BAKED = "a" * 40
FROM_ENV = "b" * 40


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return out.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    """A real throwaway repo with one empty commit -> (root, HEAD sha).

    Real git, not a stub: the git tier's contract is "whatever
    `rev-parse HEAD` prints in a source tree", and only a real repo proves
    the subprocess wiring (cwd, stdout capture, returncode) works.
    """
    _git(tmp_path, "init")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "x")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


# ── Precedence: each tier beats every tier below it ─────────────────────────


def test_baked_beats_env_and_git(git_repo):
    root, _head = git_repo
    got = build_identity.resolve_build_sha(
        baked=BAKED, environ={"DUBIS_BUILD_SHA": FROM_ENV}, source_root=root
    )
    assert got == BAKED


def test_env_beats_git(git_repo):
    root, _head = git_repo
    got = build_identity.resolve_build_sha(
        baked="", environ={"DUBIS_BUILD_SHA": FROM_ENV}, source_root=root
    )
    assert got == FROM_ENV


def test_git_beats_unknown(git_repo):
    root, head = git_repo
    got = build_identity.resolve_build_sha(baked="", environ={}, source_root=root)
    assert got == head


def test_unknown_when_nothing_applies(tmp_path):
    assert (
        build_identity.resolve_build_sha(baked="", environ={}, source_root=tmp_path)
        == build_identity.UNKNOWN
    )


def test_whitespace_inputs_fall_through_never_become_the_answer(git_repo):
    # An identity that is only spaces is no identity — and it must not be
    # *returned* as one either. With both upper tiers whitespace-only, the
    # answer is the git sha below them, not "   " and not "".
    root, head = git_repo
    got = build_identity.resolve_build_sha(
        baked="   ",
        environ={"DUBIS_BUILD_SHA": "   "},
        source_root=root,
    )
    assert got == head


def test_unknown_is_distinguishable_and_never_empty():
    assert build_identity.UNKNOWN == "unknown"
    assert build_identity.UNKNOWN.strip() == build_identity.UNKNOWN
    # A real sha can never *be* the sentinel: shas are hex, the sentinel is not.
    assert not all(c in "0123456789abcdef" for c in build_identity.UNKNOWN)


# ── Tier 1: the baked module ────────────────────────────────────────────────


def test_baked_module_absent_in_a_source_checkout():
    # `server/_build_id.py` exists only inside a built image (the Dockerfile
    # generates it from ARG GIT_SHA). Its absence here is what makes tiers
    # 2-4 reachable at all, so pin it: a committed _build_id.py would pin
    # every dev machine to a stale sha, outranking git HEAD.
    assert build_identity._baked_sha() == ""


def test_baked_module_is_read_when_present(monkeypatch):
    fake = types.ModuleType("server._build_id")
    fake.BUILD_SHA = BAKED
    monkeypatch.setitem(sys.modules, "server._build_id", fake)
    monkeypatch.setattr(server_pkg, "_build_id", fake, raising=False)
    assert build_identity._baked_sha() == BAKED


def test_empty_baked_value_is_not_an_identity(monkeypatch):
    # A generated module with BUILD_SHA = "" must fall through, not report
    # the empty string as this build's identity.
    fake = types.ModuleType("server._build_id")
    fake.BUILD_SHA = ""
    monkeypatch.setitem(sys.modules, "server._build_id", fake)
    monkeypatch.setattr(server_pkg, "_build_id", fake, raising=False)
    got = build_identity.resolve_build_sha(
        environ={"DUBIS_BUILD_SHA": FROM_ENV}, source_root=Path("/nonexistent")
    )
    assert got == FROM_ENV


# ── Tier 3 failure modes ────────────────────────────────────────────────────


def test_git_tier_says_nothing_outside_a_repo(tmp_path):
    assert build_identity._git_head(tmp_path) == ""


def test_git_tier_says_nothing_for_a_repo_without_commits(tmp_path):
    _git(tmp_path, "init")
    assert build_identity._git_head(tmp_path) == ""


def test_git_tier_says_nothing_for_a_missing_directory(tmp_path):
    assert build_identity._git_head(tmp_path / "nope") == ""


# ── The route ───────────────────────────────────────────────────────────────


def test_health_reports_the_resolved_build_sha(client, monkeypatch):
    monkeypatch.setenv("DUBIS_BUILD_SHA", FROM_ENV)
    build_identity.get_build_sha.cache_clear()
    try:
        body = client.get("/v1/health").json()
    finally:
        build_identity.get_build_sha.cache_clear()
    assert body["build_sha"] == FROM_ENV


def test_health_build_sha_is_never_empty_in_a_source_checkout(client):
    # No baked module, no env here, so this resolves through the git tier —
    # the worktree the tests run in always has a HEAD.
    build_identity.get_build_sha.cache_clear()
    try:
        body = client.get("/v1/health").json()
    finally:
        build_identity.get_build_sha.cache_clear()
    assert body["build_sha"] == build_identity.resolve_build_sha()
    assert body["build_sha"] != ""


def test_get_build_sha_is_resolved_once_per_process(monkeypatch):
    # /v1/health is polled every 30s by the container HEALTHCHECK and by
    # every picker probe; the tiers cannot change under a running process,
    # so the answer is cached — a later env change must NOT be visible.
    monkeypatch.setenv("DUBIS_BUILD_SHA", FROM_ENV)
    build_identity.get_build_sha.cache_clear()
    try:
        assert build_identity.get_build_sha() == FROM_ENV
        monkeypatch.setenv("DUBIS_BUILD_SHA", "c" * 40)
        assert build_identity.get_build_sha() == FROM_ENV
    finally:
        build_identity.get_build_sha.cache_clear()
