"""The token file must never be committable. `dubnubdubnub/dubIS` is public.

This is the test the task brief asked for: proof that the credential is not
written where it must not go. The two other halves of that proof live in
`test_token_store.py` (it is not in `preferences.json` and not in any response
body) — this one covers the third: git.

Asked of git itself rather than of a hand-parsed `.gitignore`, because the
question is not "does a line exist" but "would this path be committed", and only
`git check-ignore` answers that — it applies the full precedence rules,
including the `!data/…` re-includes directly above the entry, which are exactly
what could silently un-ignore it.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from server import token_store

# tests/python/server/<this file> -> four levels up is the repo root.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("path", [
    f"data/{token_store.TOKEN_FILENAME}",
    # The atomic-write temp file `save_tokens` renames from. It exists for
    # microseconds, but a `git add -A` racing one would commit a live token,
    # and the pattern above does not cover a `.tmp` suffix on its own.
    f"data/{token_store.TOKEN_FILENAME}.tmp",
])
def test_the_token_file_is_ignored_by_git(path):
    result = _git("check-ignore", "-q", path)
    assert result.returncode == 0, (
        f"{path} is NOT gitignored. This repo is public and that file holds bearer "
        f"tokens for other people's dubIS servers. Check the `!data/*.json` "
        f"re-includes in .gitignore — one of them is shadowing it."
    )


def test_the_token_file_is_not_already_tracked():
    """A file that is ignored but already tracked keeps being committed — the
    ignore rule does nothing for it. Both conditions have to hold."""
    tracked = _git("ls-files", f"data/{token_store.TOKEN_FILENAME}").stdout.strip()
    assert tracked == "", f"{tracked} is tracked by git; it holds credentials"


def test_preferences_is_ignored_too():
    """Pinned alongside, because the brief for this work assumed the opposite
    ("data/preferences.json itself IS tracked") and the design would have been
    different had that been true. It is ignored by `data/*.json`, and it is not
    in the re-include list. If that ever changes, the roster's urls and the
    whole preferences file start shipping in the public repo — and this test is
    the place that says so."""
    assert _git("check-ignore", "-q", "data/preferences.json").returncode == 0
    assert _git("ls-files", "data/preferences.json").stdout.strip() == ""
