"""Tests for server/lockfile.py's data-dir exclusive lock.

See docs/plans/2026-07-16-phase1c-remote-deploy-design.md section 2 and
.superpowers/sdd/task-1-brief.md for the requirements this covers:
second acquire on a locked dir fails fast naming the holder's pid/port,
a released handle allows re-acquire, a crashed process's stale lock-file
content doesn't block a fresh acquire, and two real `python -m server`
processes pointed at the same --data-dir race correctly (second exits
non-zero with the error on stderr).
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import time

import pytest

from dubis_errors import DataDirLockedError
from server.lockfile import acquire_lock


def test_acquire_creates_lock_file_with_own_pid(tmp_path):
    handle = acquire_lock(str(tmp_path))
    try:
        assert os.path.exists(tmp_path / ".dubis_lock")
    finally:
        handle.release()


def test_second_acquire_on_locked_dir_raises_with_pid_and_port(tmp_path):
    handle = acquire_lock(str(tmp_path))
    handle.update_port(12345)
    try:
        with pytest.raises(DataDirLockedError) as exc_info:
            acquire_lock(str(tmp_path))
        err = exc_info.value
        assert err.pid == os.getpid()
        assert err.port == 12345
        assert str(os.getpid()) in str(err)
        assert "12345" in str(err)
    finally:
        handle.release()


def test_released_handle_allows_reacquire(tmp_path):
    handle = acquire_lock(str(tmp_path))
    handle.release()

    handle2 = acquire_lock(str(tmp_path))
    try:
        assert handle2.path == handle.path
    finally:
        handle2.release()


def test_reacquire_after_release_overwrites_content(tmp_path):
    handle = acquire_lock(str(tmp_path))
    handle.update_port(1111)
    handle.release()

    handle2 = acquire_lock(str(tmp_path))
    handle2.update_port(2222)
    try:
        # A third acquire attempt should now fail with the SECOND handle's
        # port, proving content was overwritten rather than left stale.
        with pytest.raises(DataDirLockedError) as exc_info:
            acquire_lock(str(tmp_path))
        assert exc_info.value.port == 2222
    finally:
        handle2.release()


def test_crashed_process_stale_content_acquires_fine(tmp_path):
    """A lock file can have leftover {pid, port} JSON content from a
    process that died without ever explicitly releasing (e.g. killed) —
    simulated here by writing content directly to the file WITHOUT ever
    taking the OS lock. Since no lock is actually held, acquire_lock must
    succeed normally and overwrite the stale content."""
    lock_path = tmp_path / ".dubis_lock"
    lock_path.write_bytes(b"\0" + json.dumps({"pid": 999999, "port": 55555}).encode())

    handle = acquire_lock(str(tmp_path))
    try:
        assert handle.path == str(lock_path)
    finally:
        handle.release()


def test_update_port_after_release_raises(tmp_path):
    handle = acquire_lock(str(tmp_path))
    handle.release()
    with pytest.raises(RuntimeError):
        handle.update_port(999)


def test_release_is_idempotent(tmp_path):
    handle = acquire_lock(str(tmp_path))
    handle.release()
    handle.release()  # must not raise


def test_unrelated_oserror_propagates_unchanged(tmp_path, monkeypatch):
    """Regression for the review finding that acquire_lock() used to catch
    plain `OSError` around `_lock_region` and relabel ANY OSError (not just
    genuine lock contention) as DataDirLockedError with pid=None — masking
    real failures like permission-denied or disk-full as a bogus "another
    server is running" message. A non-contention OSError must propagate
    unchanged."""
    import server.lockfile as lockfile_mod

    def _boom(f):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(lockfile_mod, "_lock_region", _boom)

    with pytest.raises(OSError) as exc_info:
        acquire_lock(str(tmp_path))
    assert not isinstance(exc_info.value, DataDirLockedError)
    assert exc_info.value.errno == errno.ENOSPC


def test_genuine_contention_still_raises_data_dir_locked_error(tmp_path):
    """Sanity check alongside the narrowing fix above: real contention
    (the common case — second acquire on an already-held lock) must still
    map to DataDirLockedError, not propagate as a raw OSError."""
    handle = acquire_lock(str(tmp_path))
    try:
        with pytest.raises(DataDirLockedError):
            acquire_lock(str(tmp_path))
    finally:
        handle.release()


def test_write_content_failure_after_lock_closes_handle_and_unlocks(tmp_path, monkeypatch):
    """Regression for the review's minor finding: if `_write_content` raises
    AFTER `_lock_region` already succeeded, acquire_lock() used to leak both
    the file handle and the held OS lock (never reachable again, since the
    LockHandle was never constructed/returned). It must close the handle
    and release the OS lock so a subsequent acquire can still succeed."""
    import server.lockfile as lockfile_mod

    def _boom(f, content):
        raise ValueError("simulated write failure")

    monkeypatch.setattr(lockfile_mod, "_write_content", _boom)

    with pytest.raises(ValueError):
        acquire_lock(str(tmp_path))

    # The lock must have been released (not leaked) — a fresh acquire (with
    # the real _write_content restored) succeeds.
    monkeypatch.undo()
    handle = acquire_lock(str(tmp_path))
    handle.release()


def test_two_live_servers_second_process_exits_nonzero(tmp_path):
    """End-to-end: spawn a real `python -m server` against a tmp data dir,
    then spawn a second one against the SAME data dir — the second process
    must exit non-zero with the lock error on stderr, and must NOT print
    READY: (it never got to bind a port)."""
    data_dir = str(tmp_path)
    env = {**os.environ, "PYTHONPATH": os.getcwd()}

    first = subprocess.Popen(
        [sys.executable, "-m", "server", "--data-dir", data_dir, "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        # Wait for the first server to actually acquire the lock and bind.
        deadline = time.monotonic() + 15
        ready = False
        line = ""
        while time.monotonic() < deadline:
            line = first.stdout.readline()
            if line.startswith("READY:"):
                ready = True
                break
        assert ready, f"first server never printed READY: (last line: {line!r})"

        second = subprocess.run(
            [sys.executable, "-m", "server", "--data-dir", data_dir, "--port", "0"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert second.returncode != 0, (
            f"second server should have exited non-zero; stdout={second.stdout!r} "
            f"stderr={second.stderr!r}"
        )
        assert "READY:" not in second.stdout
        assert str(first.pid) in second.stderr, second.stderr
    finally:
        first.terminate()
        try:
            first.wait(timeout=10)
        except subprocess.TimeoutExpired:
            first.kill()
            first.wait(timeout=10)
