"""Guard tests for the self-hosted-runner hygiene story.

Two structural properties made the m4-air (macos) CI legs untrustworthy:

1. ``install-pw: false`` — the leg never installed Playwright browsers, so it
   ran against whatever happened to be cached on that one physical machine.
   ``playwright install`` treats an EXISTING revision directory as "already
   installed", so a half-finished download (observed: ``chromium-1208`` with 39
   files / 428 KB and a 52 KB stub binary) is permanent and invisible — the leg
   then reports spec failures instead of "my browser is broken".
2. ``checkout-clean: false`` — the workspace is reused, so untracked/ignored
   state (``test-results/``, ``cache.db``, the gitignored ``data/*.csv`` and
   ``data/*.json``) survives between runs and between branches. The ubuntu legs
   have none of it, so the two legs were not running the same test.

The fixes are ``scripts/check-playwright-browsers.mjs`` and
``scripts/ci-scrub-workspace.sh``. A CI-only shell/JS script cannot be proved
correct before merge on the leg it protects, and that leg is advisory — so a
regression there is quiet. These tests are where it stops being quiet:

- the scrub script's behaviour is exercised against a synthetic git repo;
- ``ci.yml`` is parsed and asserted to actually WIRE both scripts in, and to
  keep the single-physical-machine legs advisory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRUB = REPO_ROOT / "scripts" / "ci-scrub-workspace.sh"
PW_CHECK = REPO_ROOT / "scripts" / "check-playwright-browsers.mjs"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Ignored paths ci-scrub-workspace.sh must carry over between runs. Kept in
# sync with the script's own KEEP list by test_keep_list_matches_script.
KEEP = ("node_modules", ".venv", ".claude", "scripts/ci_watcher")


# ── scripts/ci-scrub-workspace.sh ────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )


def _run_scrub(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRUB), *args],
        cwd=str(cwd), capture_output=True, text=True, check=False,
    )


@pytest.fixture
def dirty_workspace(tmp_path: Path) -> Path:
    """A git repo littered exactly the way a reused CI workspace gets littered."""
    repo = tmp_path / "workspace"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    (repo / ".gitignore").write_text(
        "node_modules/\n.venv/\n.claude/\nscripts/ci_watcher/*.db\n"
        "data/*.csv\ndata/*.json\ntest-results/\n",
        encoding="utf-8",
    )
    # One tracked, generated-and-committed file — the class of file whose
    # staleness the drift assertion exists to catch.
    (repo / "js").mkdir()
    (repo / "js" / "api-map.js").write_text("export const API_MAP = {};\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "js/api-map.js")
    _git(repo, "-c", "user.email=ci@test", "-c", "user.name=ci", "commit", "-qm", "init")

    # Leftovers from previous runs / other branches.
    for rel in (
        "test-results/leftover-trace.zip",
        "data/inventory.csv",
        "data/preferences.json",
        "cache.db",
        "stale-file-from-another-branch.py",
    ):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("stale\n", encoding="utf-8")

    # Caches that must survive (that is the entire point of clean: false).
    for rel in (
        "node_modules/.package-lock.json",
        ".venv/bin/python",
        ".claude/worktrees/agent-x/marker",
        "scripts/ci_watcher/state.db",
    ):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("keep\n", encoding="utf-8")
    return repo


def test_scrub_removes_inherited_derived_state(dirty_workspace: Path) -> None:
    res = _run_scrub(dirty_workspace)
    assert res.returncode == 0, res.stdout + res.stderr
    for rel in (
        "test-results",
        "data/inventory.csv",
        "data/preferences.json",
        "cache.db",
        "stale-file-from-another-branch.py",
    ):
        assert not (dirty_workspace / rel).exists(), f"{rel} survived the scrub"


def test_scrub_keeps_the_caches_that_justify_clean_false(dirty_workspace: Path) -> None:
    res = _run_scrub(dirty_workspace)
    assert res.returncode == 0, res.stdout + res.stderr
    for rel in (
        "node_modules/.package-lock.json",
        ".venv/bin/python",
        ".claude/worktrees/agent-x/marker",
        # Documented in .gitignore as production state living on m4-air, i.e.
        # on this very machine. Tidying a test run must not destroy it.
        "scripts/ci_watcher/state.db",
    ):
        assert (dirty_workspace / rel).exists(), f"{rel} was wrongly deleted"


def test_scrub_leaves_tracked_files_alone(dirty_workspace: Path) -> None:
    res = _run_scrub(dirty_workspace)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (dirty_workspace / "js" / "api-map.js").read_text(encoding="utf-8") == \
        "export const API_MAP = {};\n"


def test_scrub_is_idempotent(dirty_workspace: Path) -> None:
    assert _run_scrub(dirty_workspace).returncode == 0
    second = _run_scrub(dirty_workspace)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "0 inherited path(s)" in second.stdout


def test_scrub_reports_what_it_inherited(dirty_workspace: Path) -> None:
    """The log line is the deliverable: a long list here means the workspace had
    been silently accumulating, which is the finding, not a side note."""
    res = _run_scrub(dirty_workspace)
    assert "inherited path(s) to remove" in res.stdout
    assert "cache.db" in res.stdout
    assert "carried over on purpose" in res.stdout


def test_scrub_dry_run_removes_nothing(dirty_workspace: Path) -> None:
    res = _run_scrub(dirty_workspace, "--dry-run")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (dirty_workspace / "cache.db").exists()
    assert "nothing removed" in res.stdout


def test_scrub_fails_loudly_on_tracked_file_drift(dirty_workspace: Path) -> None:
    """A modified tracked file means the checkout did not fully reset, so every
    staleness guard in that run is validating the wrong bytes. That must be an
    obvious early failure, not a confusing downstream one."""
    (dirty_workspace / "js" / "api-map.js").write_text("tampered\n", encoding="utf-8")
    res = _run_scrub(dirty_workspace)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "::error" in res.stdout
    assert "js/api-map.js" in res.stdout


def test_scrub_refuses_outside_a_git_work_tree(tmp_path: Path) -> None:
    """rm -rf-adjacent code must never run somewhere it cannot ask git what is
    disposable."""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    (plain / "precious.txt").write_text("data\n", encoding="utf-8")
    res = _run_scrub(plain)
    assert res.returncode == 1
    assert (plain / "precious.txt").exists()


def test_scrub_rejects_unknown_arguments(dirty_workspace: Path) -> None:
    res = _run_scrub(dirty_workspace, "--delete-everything")
    assert res.returncode == 2
    assert (dirty_workspace / "cache.db").exists()


def test_keep_list_matches_script() -> None:
    """Keep this test's expectations honest against the script itself."""
    text = SCRUB.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("KEEP="))
    assert tuple(line.split("=", 1)[1].strip('"').split()) == KEEP


# ── .github/workflows/ci.yml wiring ──────────────────────────────────

@pytest.fixture(scope="module")
def ci_jobs() -> dict:
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"]


def _run_steps(job: dict) -> list[str]:
    return [s["run"] for s in (job.get("steps") or []) if isinstance(s.get("run"), str)]


def _matrix_legs(job: dict) -> list[dict]:
    include = (job.get("strategy") or {}).get("matrix", {})
    if isinstance(include, dict):
        return [leg for leg in include.get("include", []) if isinstance(leg, dict)]
    return []


def test_every_reused_workspace_leg_scrubs_first(ci_jobs: dict) -> None:
    """Any leg that opts out of a clean checkout must converge the workspace back
    onto what a clean checkout would look like."""
    reused = [
        name for name, job in ci_jobs.items()
        if any(leg.get("checkout-clean") is False for leg in _matrix_legs(job))
    ]
    # If this drops to zero, either the properties were removed (fine — delete
    # this test with them) or a matrix key was renamed and this guard went blind.
    assert reused, "no job checks out with clean: false — has the matrix key been renamed?"
    for name in reused:
        runs = " ".join(_run_steps(ci_jobs[name]))
        assert "scripts/ci-scrub-workspace.sh" in runs, (
            f"job '{name}' reuses its workspace (checkout-clean: false) but never "
            f"runs scripts/ci-scrub-workspace.sh"
        )


def test_the_scrub_runs_before_anything_consumes_the_workspace(ci_jobs: dict) -> None:
    """Scrubbing after `npm install` would delete nothing useful and cost a
    reinstall; scrubbing after the tests would be pointless."""
    for name, job in ci_jobs.items():
        runs = _run_steps(job)
        if not any("ci-scrub-workspace.sh" in r for r in runs):
            continue
        scrub_at = next(i for i, r in enumerate(runs) if "ci-scrub-workspace.sh" in r)
        assert scrub_at == 0, (
            f"job '{name}' runs {runs[0]!r} before the workspace scrub"
        )


def test_every_playwright_leg_installs_and_verifies_its_browser(ci_jobs: dict) -> None:
    """`playwright install` alone is not enough: it no-ops on an existing (but
    truncated) revision directory, which is how a leg ends up silently running a
    broken browser forever."""
    pw_jobs = [
        name for name, job in ci_jobs.items()
        if any("playwright test" in r for r in _run_steps(job))
    ]
    assert pw_jobs, "no job runs `playwright test` — has the E2E wiring moved?"
    for name in pw_jobs:
        runs = " ".join(_run_steps(ci_jobs[name]))
        assert "playwright install" in runs, f"job '{name}' runs E2E without installing a browser"
        assert "check-playwright-browsers.mjs" in runs, (
            f"job '{name}' runs E2E without verifying the browser can actually launch"
        )


def test_browser_install_is_never_conditional_again(ci_jobs: dict) -> None:
    """The old `install-pw: false` matrix key made one leg trust a cache it never
    populated. Nothing may reintroduce a per-leg opt-out of installing.

    Asserted structurally, not by grepping the file: the comment explaining why
    the key is gone legitimately names it."""
    for name, job in ci_jobs.items():
        for leg in _matrix_legs(job):
            assert "install-pw" not in leg, (
                f"job '{name}' reintroduced an install-pw matrix key; installing browsers "
                f"is a no-op when they're present, so there is no leg that should skip it"
            )
    for name, job in ci_jobs.items():
        for step in job.get("steps") or []:
            run = step.get("run")
            if isinstance(run, str) and "playwright install" in run:
                assert "if" not in step, (
                    f"job '{name}' installs Playwright browsers conditionally ({step.get('if')!r})"
                )


def test_single_machine_legs_stay_advisory(ci_jobs: dict) -> None:
    """The m4-air legs are `continue-on-error` on purpose: one physical laptop
    must not be able to block every merge. Hardening the leg must not quietly
    promote it to a blocking check."""
    for name, job in ci_jobs.items():
        for leg in _matrix_legs(job):
            if leg.get("name") != "macos":
                continue
            advisory = leg.get("advisory") is True or job.get("continue-on-error") is True
            assert advisory, (
                f"job '{name}' has a macos leg that is not advisory — the m4-air is a "
                f"single physical machine and its outage must not block merges"
            )


def test_the_hygiene_scripts_exist_and_are_the_ones_referenced(ci_jobs: dict) -> None:
    assert SCRUB.is_file()
    assert PW_CHECK.is_file()
    text = CI_YML.read_text(encoding="utf-8")
    assert "scripts/ci-scrub-workspace.sh" in text
    assert "scripts/check-playwright-browsers.mjs" in text
