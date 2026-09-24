"""Every credential file dubIS writes is `0600` — and is git-ignored.

Two ways the same three files leak, so both are guarded here: readable by
another account on the machine, and committable by `git add -A`.

Three plaintext secrets live in the data dir — a live DigiKey session
(`digikey_cookies.json`), a Mouser API key (`mouser_credentials.json`) and one
live JLC session per account (`jlc_sessions.json`). All three used to be
written at the process umask's default, i.e. typically world-readable: on a
shared Linux box (exactly the setup `server/peercred.py` exists for) any other
user could read a login. `secret_store.write_private_json` is the one place all
three get their mode from, so this file tests the rule once per call site.

`docs/plans/2026-09-20-extension-credential-capture.md` rule 8.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

import digikey_session
import jlc_session
import secret_store
from mouser_client import MouserClient

COOKIES = [{"name": "dkuhint", "value": "1", "domain": ".digikey.com"}]
JLC_COOKIE = {"name": "JLCPCB_SESSION_ID", "value": "uuid", "domain": ".jlcpcb.com"}


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ── The shared helper ────────────────────────────────────────────────────────


class TestWritePrivateJson:
    def test_new_file_is_0600(self, tmp_path):
        path = str(tmp_path / "secret.json")
        secret_store.write_private_json(path, {"a": 1})
        assert _mode(path) == 0o600
        assert json.loads(open(path, encoding="utf-8").read()) == {"a": 1}

    def test_an_existing_loose_file_is_tightened(self, tmp_path):
        # `os.open`'s mode applies only on CREATE, so without the explicit
        # chmod a file that already exists at 0644 would keep those bits
        # forever — which is exactly the state every pre-existing install is in.
        path = tmp_path / "secret.json"
        path.write_text("{}")
        os.chmod(path, 0o644)
        secret_store.write_private_json(str(path), {"a": 1})
        assert _mode(path) == 0o600

    def test_truncates_rather_than_appending(self, tmp_path):
        path = str(tmp_path / "secret.json")
        secret_store.write_private_json(path, {"long": "x" * 200})
        secret_store.write_private_json(path, {"a": 1})
        assert json.loads(open(path, encoding="utf-8").read()) == {"a": 1}

    def test_is_private_predicate(self, tmp_path):
        path = tmp_path / "secret.json"
        secret_store.write_private_json(str(path), {})
        assert secret_store.is_private(str(path))
        os.chmod(path, 0o644)
        assert not secret_store.is_private(str(path))

    def test_harden_on_a_missing_file_warns_instead_of_raising(self, tmp_path, caplog):
        secret_store.harden(str(tmp_path / "gone.json"))
        assert "Could not restrict permissions" in caplog.text


# ── The three call sites ─────────────────────────────────────────────────────


def test_digikey_cookie_file_is_0600(tmp_path):
    path = str(tmp_path / "digikey_cookies.json")
    digikey_session.save_cookies_to_file(COOKIES, path)
    assert _mode(path) == 0o600
    assert digikey_session.load_cookies_from_file(path) == COOKIES


def test_digikey_cookie_file_tightens_an_existing_loose_one(tmp_path):
    path = tmp_path / "digikey_cookies.json"
    path.write_text("[]")
    os.chmod(path, 0o644)
    digikey_session.save_cookies_to_file(COOKIES, str(path))
    assert _mode(path) == 0o600


def test_digikey_save_with_no_path_is_a_no_op():
    digikey_session.save_cookies_to_file(COOKIES, None)  # must not raise


def test_mouser_credentials_file_is_0600(tmp_path):
    path = str(tmp_path / "mouser_credentials.json")
    client = MouserClient(credentials_file=path)
    client.set_api_key("abc-123")
    assert _mode(path) == 0o600
    assert client.get_api_key() == "abc-123"


def test_mouser_rewrite_tightens_an_existing_loose_file(tmp_path):
    path = tmp_path / "mouser_credentials.json"
    path.write_text(json.dumps({"api_key": "old"}))
    os.chmod(path, 0o644)
    MouserClient(credentials_file=str(path)).set_api_key("new")
    assert _mode(path) == 0o600


def test_mouser_clearing_still_removes_the_file(tmp_path):
    path = tmp_path / "mouser_credentials.json"
    client = MouserClient(credentials_file=str(path))
    client.set_api_key("abc-123")
    client.set_api_key("   ")
    assert not path.exists()


def test_jlc_session_store_is_0600(tmp_path):
    path = jlc_session.store_path(str(tmp_path))
    jlc_session.store_session(path, account="12625901A", cookies=[JLC_COOKIE])
    assert _mode(path) == 0o600


@pytest.mark.parametrize("writer", ["digikey", "mouser", "jlc"])
def test_no_credential_file_is_group_or_world_readable(tmp_path, writer):
    """The property that actually matters, stated once per file."""
    if writer == "digikey":
        path = str(tmp_path / "digikey_cookies.json")
        digikey_session.save_cookies_to_file(COOKIES, path)
    elif writer == "mouser":
        path = str(tmp_path / "mouser_credentials.json")
        MouserClient(credentials_file=path).set_api_key("k")
    else:
        path = jlc_session.store_path(str(tmp_path))
        jlc_session.store_session(path, account="A", cookies=[JLC_COOKIE])
    assert _mode(path) & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH) == 0


# ── …and no credential file is committable, wherever it lands ────────────────
#
# `0600` protects a credential from the other users of the machine. This
# protects it from `git add -A`, which is the other way all three leak — and
# the one that publishes them.
#
# The rule used to be by LOCATION only: `.gitignore`'s `data/*.json` covers the
# data dir. But `--data-dir` defaults to `"."` (server/__main__.py), so the
# documented standalone path — `python -m server` or `dubis serve` from the
# repo root — writes these files to the REPO ROOT, where that rule does not
# reach. Found live on 2026-09-20: a real JLC session cookie sat at the root of
# a worktree as an untracked file, one `git add -A` from a public commit. The
# fix was three by-NAME rules; this is the test that keeps them.

CREDENTIAL_FILENAMES = (
    "jlc_sessions.json",
    "digikey_cookies.json",
    "mouser_credentials.json",
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_git_ignored(relative_path: str) -> bool:
    """`git check-ignore -q` — exit 0 ignored, 1 not, ≥2 a real error.

    Asks git rather than re-implementing gitignore matching, which is the only
    way to be sure a later negation (`!data/constants.json` and friends) has
    not re-included one of these.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", relative_path],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode in (0, 1), (
        f"git check-ignore failed on {relative_path}: "
        f"rc={result.returncode} {result.stderr.strip()}"
    )
    return result.returncode == 0


@pytest.mark.parametrize("name", CREDENTIAL_FILENAMES)
@pytest.mark.parametrize("directory", ["", "data"])
def test_credential_files_are_git_ignored_wherever_they_are_written(name, directory):
    relative = f"{directory}/{name}" if directory else name
    assert _is_git_ignored(relative), (
        f"{relative} is NOT git-ignored, so a live credential written there "
        "can be committed.\n"
        "The repo root is not a hypothetical location for these: --data-dir "
        'defaults to "." (server/__main__.py), so `python -m server` or '
        "`dubis serve` run from the repo root writes them exactly there. "
        "`.gitignore` must match all three BY NAME, not only under `data/`."
    )


def test_the_by_name_rules_do_not_ignore_ordinary_config():
    """The by-name rules are narrow: they must not have been written as a glob
    that swallows the committed config files beside them."""
    for tracked in ("data/constants.json", "data/pnp_part_map.json", "package.json"):
        assert not _is_git_ignored(tracked), (
            f"{tracked} became git-ignored — a credential rule was written too "
            "broadly. Match the three credential filenames exactly."
        )
