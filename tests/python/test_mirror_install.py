import sys
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
    if sys.platform == "win32":
        assert isinstance(base.get_installer(), WindowsInstaller)
    else:
        with pytest.raises(NotImplementedError):
            base.get_installer()


def test_windows_install_builds_schtasks_create():
    inst = WindowsInstaller()
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.install(_cfg())
    args = run.call_args_list[0].args[0]
    assert args[0] == "schtasks" and "/Create" in args and TASK_NAME in args
    joined = " ".join(args)
    assert "inventory_mirror.py" in joined and "7893" in joined and "owner@x.com" in joined


def test_windows_uninstall_builds_schtasks_delete():
    inst = WindowsInstaller()
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.uninstall()
    args = run.call_args_list[0].args[0]
    assert "/Delete" in args and TASK_NAME in args


def test_tailscale_not_available_raises_on_enable():
    with mock.patch("shutil.which", return_value=None):
        assert tailscale.is_available() is False


def test_tailscale_logged_in_parses_status():
    with mock.patch("shutil.which", return_value="/usr/bin/tailscale"), \
         mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout='{"BackendState":"Running"}', stderr="")
        assert tailscale.is_logged_in() is True
        run.return_value = mock.Mock(returncode=0, stdout='{"BackendState":"NeedsLogin"}', stderr="")
        assert tailscale.is_logged_in() is False
