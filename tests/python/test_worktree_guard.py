"""scripts/guard-primary-checkout.sh — the PreToolUse hook that keeps feature
work out of the shared primary checkout.

Worth testing because it already failed open once: an apostrophe in the deny
message ("session's") closed the single-quoted jq program, so the script was a
bash syntax error — but *only on the deny path*, since every allow path returns
before reaching it. The guard looked fine in casual use and would have let
through exactly the edits it exists to stop. `test_script_parses` is the direct
regression guard for that class of bug; the rest pin the decisions.

Hermetic: builds throwaway git repos in tmp_path rather than reading the real
checkout, so the tests do not depend on where this suite happens to run from
(itself a worktree, most of the time).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "guard-primary-checkout.sh"


def run_guard(payload: dict, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    # Clear the escape hatch unless a test sets it, so a developer who exported
    # it in their own shell does not turn every deny assertion green.
    env.pop("DUBIS_ALLOW_MAIN_EDITS", None)
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def assert_allowed(result: subprocess.CompletedProcess) -> None:
    """An allowed edit is silence: exit 0, nothing on stdout."""
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"expected silence, got: {result.stdout}"
    assert result.stderr.strip() == "", f"unexpected stderr: {result.stderr}"


def assert_denied(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == "", f"unexpected stderr: {result.stderr}"
    body = json.loads(result.stdout)
    out = body["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    return out


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def primary(tmp_path: Path) -> Path:
    """A primary checkout with one commit (a worktree needs a commit to branch)."""
    root = tmp_path / "primary"
    root.mkdir()
    git("init", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    (root / "seed.txt").write_text("seed\n")
    git("add", "seed.txt", cwd=root)
    git("commit", "-m", "seed", cwd=root)
    return root


@pytest.fixture
def linked(primary: Path, tmp_path: Path) -> Path:
    """A linked worktree of that checkout — the place work is supposed to happen."""
    wt = tmp_path / "linked"
    git("worktree", "add", "-b", "feature", str(wt), cwd=primary)
    return wt


def test_script_parses():
    """The whole guard must be syntactically valid, not just its allow paths.

    `bash -n` reaches the deny branch that runtime allow-path tests never
    execute — the exact blind spot that let the apostrophe bug ship.
    """
    result = subprocess.run(["bash", "-n", str(GUARD)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_denies_edit_in_primary_checkout(primary: Path):
    out = assert_denied(run_guard({"tool_input": {"file_path": str(primary / "a.js")}}))
    reason = out["permissionDecisionReason"]
    # The message has to be actionable, not just a refusal: it names where you
    # are and gives the command that fixes it.
    assert "git worktree add" in reason
    assert str(primary) in reason
    assert "DUBIS_ALLOW_MAIN_EDITS=1" in reason


def test_allows_edit_in_linked_worktree(linked: Path):
    assert_allowed(run_guard({"tool_input": {"file_path": str(linked / "a.js")}}))


def test_allows_write_to_a_path_that_does_not_exist_yet(linked: Path):
    # Write targets a new file, so the guard must resolve the nearest existing
    # ancestor directory rather than the file itself.
    target = linked / "deep" / "nested" / "new.js"
    assert_allowed(run_guard({"tool_input": {"file_path": str(target)}}))


def test_denies_a_new_file_in_the_primary_checkout(primary: Path):
    # Same nonexistent-path handling must not become an accidental bypass.
    assert_denied(run_guard({"tool_input": {"file_path": str(primary / "new" / "x.js")}}))


def test_allows_paths_outside_any_repo(tmp_path: Path):
    loose = tmp_path / "not-a-repo"
    loose.mkdir()
    assert_allowed(run_guard({"tool_input": {"file_path": str(loose / "a.txt")}}))


def test_escape_hatch_allows_primary_checkout(primary: Path):
    assert_allowed(
        run_guard(
            {"tool_input": {"file_path": str(primary / "a.js")}},
            env_extra={"DUBIS_ALLOW_MAIN_EDITS": "1"},
        )
    )


def test_escape_hatch_only_honors_exactly_one(primary: Path):
    # "0", "true", "" must not disable the guard — only the documented value.
    for value in ("0", "", "true", "yes"):
        assert_denied(
            run_guard(
                {"tool_input": {"file_path": str(primary / "a.js")}},
                env_extra={"DUBIS_ALLOW_MAIN_EDITS": value},
            )
        )


def test_notebook_edit_target_is_checked(primary: Path):
    # NotebookEdit names its target notebook_path, not file_path; reading only
    # file_path would leave notebooks as an unguarded hole.
    assert_denied(run_guard({"tool_input": {"notebook_path": str(primary / "n.ipynb")}}))


def test_payload_without_a_path_is_allowed():
    assert_allowed(run_guard({"tool_input": {}}))
    assert_allowed(run_guard({}))


def test_deny_reason_survives_a_branch_name_with_shell_metacharacters(primary: Path):
    # Branch names reach the message and the suggested command. A name with
    # quotes or $ must not break the JSON or inject into the shell.
    git("checkout", "-b", "fix/it's-$(broken)", cwd=primary)
    out = assert_denied(run_guard({"tool_input": {"file_path": str(primary / "a.js")}}))
    reason = out["permissionDecisionReason"]
    assert "it's-$(broken)" in reason
    # The slug used in the suggested command is sanitized to path-safe chars.
    suggested = [ln for ln in reason.splitlines() if "git worktree add" in ln][0]
    assert "$(" not in suggested
