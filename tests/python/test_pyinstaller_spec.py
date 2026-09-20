"""Guard: every path ``dubIS.spec`` feeds PyInstaller must survive a clean clone.

PyInstaller resolves ``Analysis(datas=...)`` before it does anything else and
aborts the whole build on the first source path it cannot find::

    ERROR: Unable to find '<repo>/data/preferences.json' when adding binary
    and data files.

That makes a gitignored source path a build-breaking bug that is *invisible to
whoever introduced it*: the file exists on their machine because the app wrote
it at runtime. ``data/preferences.json`` sat in ``datas`` exactly that way —
ignored by .gitignore's ``data/*.json`` rule, absent from every fresh clone,
and no CI job builds the spec, so nothing noticed.

Plain existence is therefore the wrong assertion — it would have passed on the
machine that shipped the bug. These tests demand the stronger property the
build actually needs: the path is **tracked in git**, so it is in every clone.

The spec is read with ``ast`` rather than executed, because executing it
requires PyInstaller, which is not (and need not be) a dev dependency.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "dubIS.spec"


def _analysis_call() -> ast.Call:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        ):
            return node
    raise AssertionError(f"no Analysis(...) call found in {SPEC}")


def _literal_list(node: ast.expr, what: str) -> list:
    try:
        return ast.literal_eval(node)
    except ValueError as exc:  # pragma: no cover - only on a spec rewrite
        raise AssertionError(
            f"{SPEC.name}'s {what} is no longer a literal list, so this guard "
            f"can no longer check it. Keep it literal, or teach the guard the "
            f"new shape — do not leave the paths unchecked: {exc}",
        ) from exc


def _spec_scripts() -> list[str]:
    call = _analysis_call()
    assert call.args, "Analysis(...) has no scripts argument"
    return _literal_list(call.args[0], "scripts list")


def _spec_datas() -> list[tuple[str, str]]:
    call = _analysis_call()
    for kw in call.keywords:
        if kw.arg == "datas":
            return _literal_list(kw.value, "datas")
    raise AssertionError("Analysis(...) has no datas= keyword")


def _tracked(rel: str) -> list[str]:
    """Paths git tracks under ``rel`` (the path itself, or a directory's files)."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", rel],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def _spec_paths() -> list[str]:
    return [*_spec_scripts(), *(src for src, _dest in _spec_datas())]


def test_spec_declares_the_paths_this_guard_expects() -> None:
    """A rewrite that empties datas must not silently empty the guard too."""
    paths = _spec_paths()
    assert len(paths) >= 6, f"suspiciously few paths parsed out of {SPEC.name}: {paths}"
    assert "app.pyw" in paths


@pytest.mark.parametrize("rel", _spec_paths())
def test_spec_path_exists(rel: str) -> None:
    assert (REPO_ROOT / rel).exists(), (
        f"{SPEC.name} feeds PyInstaller '{rel}', which does not exist — "
        f"the build aborts before Analysis with "
        f"\"Unable to find ... when adding binary and data files\"."
    )


@pytest.mark.parametrize("rel", _spec_paths())
def test_spec_path_is_tracked_in_git(rel: str) -> None:
    """Existing locally is not enough — it must exist in a fresh clone."""
    assert _tracked(rel), (
        f"{SPEC.name} feeds PyInstaller '{rel}', which git does not track "
        f"(gitignored, or never added). It may exist on your machine — the app "
        f"writes some of these at runtime — but a clean checkout will not have "
        f"it and the build will abort. Either commit it, or drop it from the "
        f"spec's datas."
    )


def test_runtime_written_preferences_are_not_shipped() -> None:
    """The specific regression: data/preferences.json must stay out of datas.

    It is user state the app writes, its defaults live in code (a missing file
    makes ``load_preferences()`` return ``{}``), and shipping a seed copy would
    be a second set of defaults free to drift from the real ones.
    """
    sources = [src for src, _dest in _spec_datas()]
    assert "data/preferences.json" not in sources
