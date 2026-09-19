"""No symlink may be tracked in this repository.

This is not hypothetical tidiness. `8e013f1` committed `.venv` and
`node_modules` as symlinks pointing at an absolute path inside one developer's
home directory:

    .venv        -> /Users/<someone>/Documents/GitHub/dubIS/.venv
    node_modules -> /Users/<someone>/Documents/GitHub/dubIS/node_modules

On that developer's own machine those paths ARE the checkout, so the next
`git pull` replaced the real directories with links pointing at themselves.
Every `.venv/bin/...` invocation then failed with "Too many levels of symbolic
links", and the virtualenv had to be rebuilt from scratch. On any other machine
they are simply dangling links into a stranger's home directory.

Two things had to go wrong together, and both are easy to repeat:

1. A worktree symlinks `.venv` / `node_modules` at the main checkout's copies
   to avoid duplicating them — a normal, useful thing to do.
2. `.gitignore` said `node_modules/` and `.venv/`. A pattern with a trailing
   slash matches a DIRECTORY only, so once those names were symlinks (files),
   the ignore rules stopped matching and `git add -A` staged them.

The `.gitignore` entries are slashless now, which closes that specific hole.
This test closes the general one: no tracked symlink at all, whatever its name.
An absolute-target symlink is never portable, and a relative one is rarely what
anyone means inside a repo that is also checked out as worktrees.

If a genuine need for a tracked symlink ever arises, add it to ALLOWED below
with a comment justifying it — deliberately, not by `git add -A` sweeping it up.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Paths intentionally tracked as symlinks. Empty, and should stay that way.
ALLOWED: set[str] = set()

# git's mode for a symlink blob.
SYMLINK_MODE = "120000"


def tracked_symlinks() -> list[str]:
    """Every path git has staged as a symlink, via the index rather than the
    filesystem — the filesystem cannot answer this for a path whose real
    directory currently shadows a tracked link."""
    out = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = []
    for line in out.splitlines():
        if not line.startswith(SYMLINK_MODE + " "):
            continue
        # "<mode> <sha> <stage>\t<path>"
        found.append(line.split("\t", 1)[1])
    return sorted(p for p in found if p not in ALLOWED)


def test_no_tracked_symlinks():
    found = tracked_symlinks()
    assert not found, (
        "These paths are tracked as symlinks:\n  "
        + "\n  ".join(found)
        + "\n\nA tracked symlink with an absolute target breaks every other "
        "checkout, and one pointing into its own repository makes itself "
        "self-referential on the next pull (see this file's docstring). "
        "Untrack with `git rm --cached <path>` and make sure .gitignore "
        "matches it WITHOUT a trailing slash, so the rule covers the symlink "
        "form as well as the directory form."
    )


def test_dependency_dirs_are_ignored_as_files_not_just_directories():
    """The specific hole that let this through.

    `git check-ignore` is asked about the plain names. A trailing-slash rule
    answers "not ignored" for them, which is exactly how the symlinks got
    staged. --no-index so the answer does not depend on whether the paths
    happen to exist right now.
    """
    for name in ("node_modules", ".venv"):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", name],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"{name!r} is not ignored when treated as a file. Its .gitignore "
            f"rule probably has a trailing slash, which matches directories "
            f"only — so a symlink named {name!r} would be committable."
        )
