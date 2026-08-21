"""Relaunching the desktop app.

The ordering constraint is the whole point: `Launcher._cleanup()` releases the
data-dir lock LAST so a second instance cannot start writing while this one is
closing. A restart that spawned its replacement any earlier would hand it a lock
and a WebView2 profile the dying process still holds.
"""

import sys

import pytest

import app_restart


def test_the_relaunch_preserves_the_script_path():
    """argv[0] carries the .pyw extension, and on Windows that is what keeps the
    console window suppressed — losing it changes how the app looks on relaunch."""
    cmd = app_restart.relaunch_argv("/usr/bin/python", ["app.pyw"])
    assert cmd == ["/usr/bin/python", "app.pyw"]


def test_the_relaunch_carries_flags_through():
    """A restart from a --debug session is still a --debug session; dropping the
    flag would make the restart itself look like it changed behaviour."""
    cmd = app_restart.relaunch_argv("/usr/bin/python", ["app.pyw", "--debug"])
    assert cmd[-1] == "--debug"


def test_an_empty_argv_is_a_programming_error():
    with pytest.raises(ValueError, match="at least the script path"):
        app_restart.relaunch_argv("/usr/bin/python", [])


def test_the_replacement_is_detached_from_this_process():
    """It has to outlive us: _do_exit calls _hard_exit immediately after
    spawning, and a child in our process group would be killed with us."""
    kwargs = app_restart.spawn_kwargs()
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        assert kwargs["creationflags"] == 0x00000008 | 0x00000200
    else:
        assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True


def test_dubis_url_is_stripped_from_the_replacement_environment():
    """DUBIS_URL outranks the preference (remote_mode.resolve_remote_base_url),
    so leaving it set would make restart-to-apply silently ignore the URL the
    user just saved — the one thing the restart exists to apply."""
    env = app_restart.relaunch_env({"DUBIS_URL": "https://old.example", "PATH": "/usr/bin"})
    assert "DUBIS_URL" not in env
    assert env["PATH"] == "/usr/bin"


def test_the_pinned_server_port_is_stripped_too():
    """The process being replaced may not have released it yet."""
    assert "DUBIS_SERVER_PORT" not in app_restart.relaunch_env({"DUBIS_SERVER_PORT": "8123"})


def test_every_other_environment_variable_survives():
    env = app_restart.relaunch_env({
        "DUBIS_TOKEN": "keep-me", "HOME": "/home/isaac", "DUBIS_AUTH_MODE": "on",
    })
    assert env == {"DUBIS_TOKEN": "keep-me", "HOME": "/home/isaac", "DUBIS_AUTH_MODE": "on"}


def test_relaunch_env_does_not_mutate_the_environment_it_was_given():
    original = {"DUBIS_URL": "https://old.example", "PATH": "/usr/bin"}
    app_restart.relaunch_env(original)
    assert "DUBIS_URL" in original
