import plistlib
from unittest import mock

import pytest

from mirror_install import base, tailscale
from mirror_install.linux import UNIT_NAME, LinuxInstaller
from mirror_install.macos import LABEL, MacOSInstaller
from mirror_install.windows import TASK_NAME, WindowsInstaller


def _cfg():
    return base.MirrorConfig(
        push_port=7892, read_port=7893, token_file="data/mirror_token",
        snapshot_file="data/inventory_mirror.json", allowlist=["owner@x.com"],
        python_exe="C:/py/pythonw.exe", daemon_script="C:/app/inventory_mirror.py",
    )


def test_get_installer_dispatch():
    with mock.patch("mirror_install.base.sys.platform", "win32"):
        from mirror_install.windows import WindowsInstaller as WI
        assert isinstance(base.get_installer(), WI)
    with mock.patch("mirror_install.base.sys.platform", "darwin"):
        from mirror_install.macos import MacOSInstaller
        assert isinstance(base.get_installer(), MacOSInstaller)
    with mock.patch("mirror_install.base.sys.platform", "linux"):
        from mirror_install.linux import LinuxInstaller
        assert isinstance(base.get_installer(), LinuxInstaller)
    with mock.patch("mirror_install.base.sys.platform", "sunos5"):
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


# ── macOS tests ──────────────────────────────────────────────────────────────


def test_macos_install_writes_plist_and_loads(tmp_path):
    inst = MacOSInstaller()
    plist_path = tmp_path / "com.dubis.inventory-mirror.plist"
    with mock.patch.object(inst, "_plist_path", return_value=str(plist_path)), \
         mock.patch("mirror_install.macos.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.install(_cfg())

    assert plist_path.exists()
    with open(plist_path, "rb") as f:
        pl = plistlib.load(f)
    args = pl["ProgramArguments"]
    assert "C:/app/inventory_mirror.py" in args
    assert "--read-port" in args
    assert "7893" in args
    assert "owner@x.com" in args
    assert pl["RunAtLoad"] is True
    assert pl["KeepAlive"] is True

    # Confirm launchctl load -w was called
    calls = [c.args[0] for c in run.call_args_list]
    load_call = next((c for c in calls if "load" in c and "-w" in c), None)
    assert load_call is not None
    assert str(plist_path) in load_call


def test_macos_install_raises_on_nonzero(tmp_path):
    inst = MacOSInstaller()
    plist_path = tmp_path / "com.dubis.inventory-mirror.plist"
    with mock.patch.object(inst, "_plist_path", return_value=str(plist_path)), \
         mock.patch("mirror_install.macos.subprocess.run") as run:
        def side(cmd, **_):
            if "load" in cmd and "-w" in cmd:
                return mock.Mock(returncode=1, stdout="", stderr="load failed")
            return mock.Mock(returncode=0, stdout="", stderr="")
        run.side_effect = side
        with pytest.raises(RuntimeError, match="launchctl load"):
            inst.install(_cfg())


def test_macos_uninstall_unloads_and_removes(tmp_path):
    inst = MacOSInstaller()
    plist_path = tmp_path / "com.dubis.inventory-mirror.plist"
    plist_path.write_bytes(b"")
    with mock.patch.object(inst, "_plist_path", return_value=str(plist_path)), \
         mock.patch("mirror_install.macos.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.uninstall()

    calls = [c.args[0] for c in run.call_args_list]
    unload_call = next((c for c in calls if "unload" in c), None)
    assert unload_call is not None
    assert not plist_path.exists()


def test_macos_is_installed(tmp_path):
    inst = MacOSInstaller()
    plist_path = tmp_path / "com.dubis.inventory-mirror.plist"
    with mock.patch.object(inst, "_plist_path", return_value=str(plist_path)):
        assert inst.is_installed() is False
        plist_path.write_bytes(b"")
        assert inst.is_installed() is True


def test_macos_is_running():
    inst = MacOSInstaller()
    with mock.patch("mirror_install.macos.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout=f"123\t0\t{LABEL}\n", stderr="")
        assert inst.is_running() is True

    with mock.patch("mirror_install.macos.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="123\t0\tsome.other.label\n", stderr="")
        assert inst.is_running() is False

    with mock.patch("mirror_install.macos.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="")
        assert inst.is_running() is False


# ── Linux tests ──────────────────────────────────────────────────────────────


def test_linux_install_writes_unit_and_enables(tmp_path):
    inst = LinuxInstaller()
    unit_path = tmp_path / "dubis-inventory-mirror.service"
    with mock.patch.object(inst, "_unit_path", return_value=str(unit_path)), \
         mock.patch("mirror_install.linux.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.install(_cfg())

    assert unit_path.exists()
    content = unit_path.read_text()
    # Paths must appear quoted in ExecStart
    assert '"C:/app/inventory_mirror.py"' in content
    assert "--read-port" in content
    assert "7893" in content
    assert "owner@x.com" in content
    assert "Restart=on-failure" in content

    calls = [c.args[0] for c in run.call_args_list]
    enable_call = next(
        (c for c in calls if "systemctl" in c and "enable" in c and "--now" in c), None
    )
    assert enable_call is not None
    assert UNIT_NAME in enable_call
    reload_call = next(
        (c for c in calls if "systemctl" in c and "daemon-reload" in c), None
    )
    assert reload_call is not None


def test_linux_install_raises_on_nonzero(tmp_path):
    inst = LinuxInstaller()
    unit_path = tmp_path / "dubis-inventory-mirror.service"
    with mock.patch.object(inst, "_unit_path", return_value=str(unit_path)), \
         mock.patch("mirror_install.linux.subprocess.run") as run:
        def side(cmd, **_):
            if "enable" in cmd:
                return mock.Mock(returncode=1, stdout="", stderr="enable failed")
            return mock.Mock(returncode=0, stdout="", stderr="")
        run.side_effect = side
        with pytest.raises(RuntimeError, match="systemctl enable"):
            inst.install(_cfg())


def test_linux_uninstall_disables_removes_reloads(tmp_path):
    inst = LinuxInstaller()
    unit_path = tmp_path / "dubis-inventory-mirror.service"
    unit_path.write_text("[Unit]\n")
    with mock.patch.object(inst, "_unit_path", return_value=str(unit_path)), \
         mock.patch("mirror_install.linux.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.uninstall()

    calls = [c.args[0] for c in run.call_args_list]
    disable_call = next(
        (c for c in calls if "systemctl" in c and "disable" in c), None
    )
    assert disable_call is not None
    assert not unit_path.exists()
    reload_calls = [c for c in calls if "systemctl" in c and "daemon-reload" in c]
    assert len(reload_calls) >= 1


def test_linux_is_installed(tmp_path):
    inst = LinuxInstaller()
    unit_path = tmp_path / "dubis-inventory-mirror.service"
    with mock.patch.object(inst, "_unit_path", return_value=str(unit_path)):
        assert inst.is_installed() is False
        unit_path.write_text("[Unit]\n")
        assert inst.is_installed() is True


def test_linux_is_running():
    inst = LinuxInstaller()
    with mock.patch("mirror_install.linux.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0)
        assert inst.is_running() is True

    with mock.patch("mirror_install.linux.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=3)
        assert inst.is_running() is False


def test_linux_execstart_quotes_paths_with_spaces(tmp_path):
    """ExecStart paths containing spaces must be quoted so systemd doesn't misparse them."""
    inst = LinuxInstaller()
    unit_path = tmp_path / "dubis-inventory-mirror.service"
    cfg = base.MirrorConfig(
        push_port=7892,
        read_port=7893,
        token_file="/home/Jane Doe/data/mirror_token",
        snapshot_file="/home/Jane Doe/data/inventory_mirror.json",
        allowlist=["owner@x.com"],
        python_exe="/usr/bin/python3",
        daemon_script="/home/Jane Doe/dubIS/inventory_mirror.py",
    )
    with mock.patch.object(inst, "_unit_path", return_value=str(unit_path)), \
         mock.patch("mirror_install.linux.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        inst.install(cfg)

    content = unit_path.read_text()
    # The full spaced path must appear as a single quoted token in ExecStart
    assert '"/home/Jane Doe/dubIS/inventory_mirror.py"' in content
    assert '"/home/Jane Doe/data/mirror_token"' in content
    # Must NOT appear as bare unquoted (would be split by systemd)
    execstart_line = next(line for line in content.splitlines() if line.startswith("ExecStart="))
    # Verify the spaced path is fully enclosed in quotes on the ExecStart line
    assert "/home/Jane Doe/dubIS/inventory_mirror.py" in execstart_line
    # Ensure it's quoted, not bare (a bare space would look like 'Doe/dubIS/' split off)
    assert '"' in execstart_line


def test_linux_install_raises_on_daemon_reload_failure(tmp_path):
    """install() must raise RuntimeError when daemon-reload returns non-zero."""
    inst = LinuxInstaller()
    unit_path = tmp_path / "dubis-inventory-mirror.service"
    with mock.patch.object(inst, "_unit_path", return_value=str(unit_path)), \
         mock.patch("mirror_install.linux.subprocess.run") as run:
        def side(cmd, **_):
            if "daemon-reload" in cmd:
                return mock.Mock(returncode=1, stdout="", stderr="reload failed")
            return mock.Mock(returncode=0, stdout="", stderr="")
        run.side_effect = side
        with pytest.raises(RuntimeError, match="daemon-reload"):
            inst.install(_cfg())


def test_linux_uninstall_daemon_reload_failure_is_nonfatal(tmp_path):
    """uninstall() daemon-reload failure should log a warning but not raise."""
    inst = LinuxInstaller()
    unit_path = tmp_path / "dubis-inventory-mirror.service"
    unit_path.write_text("[Unit]\n")
    with mock.patch.object(inst, "_unit_path", return_value=str(unit_path)), \
         mock.patch("mirror_install.linux.subprocess.run") as run, \
         mock.patch("mirror_install.linux.logger") as mock_logger:
        def side(cmd, **_):
            if "daemon-reload" in cmd:
                return mock.Mock(returncode=1, stdout="", stderr="reload failed")
            return mock.Mock(returncode=0, stdout="", stderr="")
        run.side_effect = side
        # Should not raise
        inst.uninstall()
    mock_logger.warning.assert_called_once()
