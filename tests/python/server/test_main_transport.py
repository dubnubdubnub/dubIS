"""`python -m server` transport selection: `--uds` vs `--host`/`--port`, and
what each transport advertises once it has bound.

Two rules are pinned here.

1. The two transports are mutually exclusive, and saying both is a hard
   usage error. An operator who asks for a Unix socket and silently gets a
   TCP server back would see every caller resolve to `local` again, with
   nothing in the output saying why — the exact failure the peer-credential
   feature exists to remove. Checked before the data-dir lock is taken and
   before anything is bound.

2. A UDS server writes `<data_dir>/.v1_uds`, never `.v1_port`. Writing the
   port file would be an outright lie: for an AF_UNIX listener uvicorn's
   `sockets[0].getsockname()` is the socket *path* string, so the usual
   `[1]` would yield the path's second character, and every
   `tools/dubis_client` probe would then health-check a TCP port nothing is
   listening on.
"""

from __future__ import annotations

import os
import sys

import pytest

from server.__main__ import _print_ready_when_started, main


def _main_exit_code(monkeypatch, argv) -> int:
    monkeypatch.setattr(sys, "argv", ["server", *argv])
    with pytest.raises(SystemExit) as exc_info:
        main()
    return exc_info.value.code


class _FakeSocket:
    def __init__(self, name):
        self._name = name

    def getsockname(self):
        return self._name


class _FakeListener:
    def __init__(self, name):
        self.sockets = [_FakeSocket(name)]


class _FakeServer:
    """A uvicorn.Server far enough along for `_print_ready_when_started`."""

    def __init__(self, name):
        self.started = True
        self.servers = [_FakeListener(name)]


# ── mutual exclusion ─────────────────────────────────────────────────────────


def test_uds_with_port_is_a_usage_error(monkeypatch, tmp_path, capsys):
    code = _main_exit_code(monkeypatch, ["--uds", "/tmp/x.sock", "--port", "7941",
                                         "--data-dir", str(tmp_path)])
    assert code == 2
    assert "--port" in capsys.readouterr().err


def test_uds_with_host_is_a_usage_error(monkeypatch, tmp_path, capsys):
    code = _main_exit_code(monkeypatch, ["--uds", "/tmp/x.sock", "--host", "0.0.0.0",
                                         "--data-dir", str(tmp_path)])
    assert code == 2
    assert "--host" in capsys.readouterr().err


def test_uds_with_both_names_both(monkeypatch, tmp_path, capsys):
    code = _main_exit_code(monkeypatch, ["--uds", "/tmp/x.sock", "--host", "0.0.0.0",
                                         "--port", "7942", "--data-dir", str(tmp_path)])
    assert code == 2
    err = capsys.readouterr().err
    assert "--host" in err
    assert "--port" in err


def test_port_zero_counts_as_given(monkeypatch, tmp_path, capsys):
    """`--port 0` means "auto-assign", not "unset". The flag defaults are
    None precisely so the two stay distinguishable — with argparse's old
    `default=7891` there would be no way to tell."""
    code = _main_exit_code(monkeypatch, ["--uds", "/tmp/x.sock", "--port", "0",
                                         "--data-dir", str(tmp_path)])
    assert code == 2
    assert "--port" in capsys.readouterr().err


def test_rollback_on_exit_still_requires_test_source(monkeypatch, tmp_path, capsys):
    """The pre-existing usage rule must not have been disturbed by moving
    --host/--port onto None defaults."""
    code = _main_exit_code(monkeypatch, ["--rollback-on-exit", "--data-dir", str(tmp_path)])
    assert code == 2
    assert "--test-source" in capsys.readouterr().err


# ── what each transport advertises ───────────────────────────────────────────


def test_tcp_writes_the_port_file(tmp_path, capsys):
    _print_ready_when_started(_FakeServer(("127.0.0.1", 7945)), 7945,
                              str(tmp_path), None, None)

    assert (tmp_path / ".v1_port").read_text(encoding="utf-8") == "7945"
    assert not (tmp_path / ".v1_uds").exists()
    assert "READY:7945" in capsys.readouterr().out


def test_uds_writes_the_uds_file_and_no_port_file(tmp_path, capsys):
    sock = str(tmp_path / "v1.sock")

    _print_ready_when_started(_FakeServer(sock), 7891, str(tmp_path), None, sock)

    assert (tmp_path / ".v1_uds").read_text(encoding="utf-8") == os.path.abspath(sock)
    assert not (tmp_path / ".v1_port").exists()
    assert f"READY:uds:{os.path.abspath(sock)}" in capsys.readouterr().out


def test_uds_never_claims_a_port_on_the_lockfile(tmp_path):
    """`lock.update_port` would have to be handed a port that does not exist.
    The lock's pid is what a contention message actually needs; its port
    stays the None it was acquired with."""

    class _Lock:
        def update_port(self, port):  # pragma: no cover - must not be called
            raise AssertionError(f"update_port({port!r}) called for a UDS server")

    sock = str(tmp_path / "v1.sock")
    _print_ready_when_started(_FakeServer(sock), 7891, str(tmp_path), _Lock(), sock)


# ── the data-dir lock is about data, not about transport ─────────────────────


def test_uds_start_still_hits_the_data_dir_lock(monkeypatch, tmp_path, capsys):
    """`server/lockfile.py` takes an exclusive lock on `<data_dir>/.dubis_lock`
    and knows nothing about sockets or ports, so it must gate a `--uds` start
    exactly as it gates a TCP one — and it must do so BEFORE anything is
    bound, so a refused start leaves no socket file behind.

    Confirmed rather than assumed, because "the lock is unaffected by the
    transport" is the kind of claim that is only true until someone moves
    `acquire_lock` below the transport branch.
    """
    from server.lockfile import acquire_lock

    sock = str(tmp_path / "v1.sock")
    held = acquire_lock(str(tmp_path))
    try:
        code = _main_exit_code(monkeypatch, ["--uds", sock, "--data-dir", str(tmp_path)])
    finally:
        held.release()

    assert code == 1
    assert "Another dubIS server is already running" in capsys.readouterr().err
    assert not os.path.exists(sock), "a refused start must not have bound a socket"
