from unittest import mock

import pytest

from mirror_install import base, tailscale
from mirror_install.windows import TASK_NAME, WindowsInstaller


def _cfg():
    return base.MirrorConfig(
        push_port=7892, read_port=7893, token_file="data/mirror_token",
        snapshot_file="data/inventory_mirror.json", allowlist=["owner@x.com"],
        python_exe="C:/py/pythonw.exe", daemon_script="C:/app/inventory_mirror.py",
    )


def test_get_installer_dispatch():
    with mock.patch("mirror_install.base.sys.platform", "win32"):
        assert isinstance(base.get_installer(), WindowsInstaller)
    with mock.patch("mirror_install.base.sys.platform", "linux"):
        with pytest.raises(NotImplementedError):
            base.get_installer()


def test_windows_install_builds_schtasks_create():
    inst = WindowsInstaller()
    with mock.patch("mirror_install.windows.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.install(_cfg())
    args = run.call_args_list[0].args[0]
    assert args[0] == "schtasks" and "/Create" in args and TASK_NAME in args
    joined = " ".join(args)
    assert "inventory_mirror.py" in joined and "7893" in joined and "owner@x.com" in joined


def test_windows_uninstall_builds_schtasks_delete():
    inst = WindowsInstaller()
    with mock.patch("mirror_install.windows.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.uninstall()
    args = run.call_args_list[0].args[0]
    assert "/Delete" in args and TASK_NAME in args


def test_enable_serve_raises_when_unavailable():
    with mock.patch("mirror_install.tailscale.shutil.which", return_value=None):
        assert tailscale.is_available() is False
        with pytest.raises(RuntimeError, match="not found on PATH"):
            tailscale.enable_serve(7893)


def test_enable_serve_raises_when_not_logged_in():
    with mock.patch("mirror_install.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         mock.patch("mirror_install.tailscale.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout='{"BackendState":"NeedsLogin"}', stderr="")
        assert tailscale.is_logged_in() is False
        with pytest.raises(RuntimeError, match="not logged in"):
            tailscale.enable_serve(7893)


def test_tailscale_logged_in_parses_status():
    with mock.patch("mirror_install.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         mock.patch("mirror_install.tailscale.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout='{"BackendState":"Running"}', stderr="")
        assert tailscale.is_logged_in() is True
        run.return_value = mock.Mock(returncode=0, stdout='{"BackendState":"NeedsLogin"}', stderr="")
        assert tailscale.is_logged_in() is False


_SELF_LOGIN_STATUS = """{
  "Self": {"UserID": 123},
  "User": {"123": {"LoginName": "alice@example.com"}},
  "BackendState": "Running"
}"""


def test_self_login_returns_login_name():
    with mock.patch("mirror_install.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         mock.patch("mirror_install.tailscale.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout=_SELF_LOGIN_STATUS, stderr="")
        assert tailscale.self_login() == "alice@example.com"


def test_self_login_returns_empty_when_unavailable():
    with mock.patch("mirror_install.tailscale.shutil.which", return_value=None):
        assert tailscale.self_login() == ""


def test_self_login_returns_empty_on_nonzero_exit():
    with mock.patch("mirror_install.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         mock.patch("mirror_install.tailscale.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="error")
        assert tailscale.self_login() == ""


def test_self_login_returns_empty_on_missing_keys():
    with mock.patch("mirror_install.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         mock.patch("mirror_install.tailscale.subprocess.run") as run:
        # Missing User map entry for the UserID
        run.return_value = mock.Mock(
            returncode=0,
            stdout='{"Self": {"UserID": 999}, "User": {}, "BackendState": "Running"}',
            stderr="",
        )
        assert tailscale.self_login() == ""


def test_self_login_returns_empty_on_invalid_json():
    with mock.patch("mirror_install.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         mock.patch("mirror_install.tailscale.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="not-json", stderr="")
        assert tailscale.self_login() == ""
