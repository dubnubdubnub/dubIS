"""Exclusive OS lock on a data directory, so two dubIS servers can never run
against the same data directory at once.

Design (see docs/plans/2026-07-16-phase1c-remote-deploy-design.md section 2):

`<data_dir>/.dubis_lock` is opened once per server process and held with an
OS-level exclusive lock (Windows: `msvcrt.locking`; POSIX: `fcntl.flock`) for
the lifetime of the process. The lock is never released explicitly on crash
— the OS drops it automatically when the holding process dies (or exits
without releasing), so the lock itself can never go stale. What CAN go stale
is the informational content of the file (a `{"pid": ..., "port": ...}` JSON
blob) — a crashed process leaves that content behind, but since the lock
itself isn't held, the next `acquire_lock()` call succeeds and overwrites it.

The lock byte and the content live at different offsets in the file
specifically so a losing caller can still read the winner's pid/port even
on Windows, where `msvcrt.locking` is a MANDATORY lock that blocks ALL
access (not just writes) to the locked byte range from other file handles.
Byte 0 is a sentinel byte used only to hold the lock; the JSON content
starts at byte 1, which is never locked, so it's always readable.

Note on POSIX vs Windows locking granularity: `msvcrt.locking` on Windows
locks exactly the byte range requested (here, byte 0 only) — that's why the
byte/content split above works at all on that platform. `fcntl.flock` on
POSIX has no byte-range concept: it locks the *entire underlying file*,
not just byte 0. The byte/content split is harmless there (flock still
only blocks other *processes'* flock() calls, not raw reads via a
different fd/open()), but don't read the POSIX branch as "locking byte 0"
the way the Windows branch literally does.
"""

from __future__ import annotations

import errno
import json
import os
import sys

from dubis_errors import DataDirLockedError

_LOCK_FILENAME = ".dubis_lock"
_LOCK_BYTE_LEN = 1

# errno values that mean "already locked by someone else" — i.e. genuine
# lock contention, as opposed to some other OSError (permission denied on
# the containing directory, disk full, antivirus holding the file open,
# etc.) that happens to be raised from the same call. Only these should be
# translated into DataDirLockedError; everything else must propagate
# unchanged so it isn't misreported as "another dubIS server is running."
#
# Windows: msvcrt.locking() on a already-locked region raises OSError with
# errno EACCES (13, the common case) or occasionally EDEADLK (36, seen when
# the same process/thread re-locks). POSIX: fcntl.flock(..., LOCK_NB) on a
# held lock raises BlockingIOError, a OSError subclass, with errno EAGAIN
# or (on some platforms) EWOULDBLOCK — CPython normalizes both spellings to
# the same errno on the vast majority of systems, but both are listed for
# safety since the C constants aren't guaranteed equal everywhere.
_WIN_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EDEADLK})
_POSIX_CONTENTION_ERRNOS = frozenset({errno.EAGAIN, errno.EWOULDBLOCK})


def _lock_path(data_dir: str) -> str:
    return os.path.join(data_dir, _LOCK_FILENAME)


def _is_lock_contention(exc: OSError) -> bool:
    """True if *exc* (raised by `_lock_region`) represents genuine lock
    contention (the region is already held by another process), as opposed
    to an unrelated OSError that happens to share the exception type."""
    if sys.platform == "win32":
        return exc.errno in _WIN_CONTENTION_ERRNOS
    return isinstance(exc, BlockingIOError) or exc.errno in _POSIX_CONTENTION_ERRNOS


def _lock_region(f) -> None:
    """Attempt to acquire a non-blocking exclusive lock on byte 0 of *f*.

    Raises OSError (or a subclass, e.g. BlockingIOError on POSIX) if the
    region is already locked by another process. Callers on both platforms
    can catch plain OSError, but should narrow with `_is_lock_contention`
    before treating it as "locked by someone else" — see that function's
    docstring for why."""
    f.seek(0)
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTE_LEN)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_region(f) -> None:
    if sys.platform == "win32":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTE_LEN)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _ensure_sentinel_byte(f) -> None:
    """Make sure byte 0 exists so the lock region call has something to
    lock — a brand-new/empty lock file has no bytes at all yet."""
    f.seek(0, os.SEEK_END)
    if f.tell() < _LOCK_BYTE_LEN:
        f.write(b"\0" * _LOCK_BYTE_LEN)
        f.flush()


def _read_content(f) -> dict | None:
    """Read the JSON content starting at byte 1 (never the locked byte 0).

    Returns None if there's no content yet or it's not valid JSON (e.g. a
    lock file created but never written to)."""
    f.seek(_LOCK_BYTE_LEN)
    raw = f.read()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _write_content(f, content: dict) -> None:
    data = json.dumps(content).encode("utf-8")
    f.truncate(_LOCK_BYTE_LEN)
    f.seek(_LOCK_BYTE_LEN)
    f.write(data)
    f.flush()
    os.fsync(f.fileno())


class LockHandle:
    """A held lock on a data directory's `.dubis_lock` file.

    Returned by `acquire_lock()`. Call `update_port()` once the server's
    actual bound port is known (it usually isn't yet at acquire time — see
    server/run.py, where port 0 means "OS-assigned"), and `release()` on
    clean shutdown."""

    def __init__(self, file_obj, path: str, data_dir: str) -> None:
        self._file = file_obj
        self.path = path
        self.data_dir = data_dir
        self._released = False

    def update_port(self, port: int) -> None:
        """Rewrite the lock file's content with the current pid and *port*.

        Safe to call multiple times (e.g. once at acquire with port=None,
        again once the socket is actually bound)."""
        if self._released:
            raise DubISLockAlreadyReleasedError(self.path)
        _write_content(self._file, {"pid": os.getpid(), "port": port})

    def release(self) -> None:
        """Release the OS lock and close the file handle.

        Idempotent — calling twice is a no-op, matching stop_server()'s
        best-effort cleanup style elsewhere in server/run.py."""
        if self._released:
            return
        _unlock_region(self._file)
        self._file.close()
        self._released = True


class DubISLockAlreadyReleasedError(RuntimeError):
    """Raised by LockHandle.update_port() after release() — a programming
    error (calling the API out of order), not an expected runtime state,
    so it's a plain RuntimeError rather than a DubISError."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Lock handle for {path!r} already released")


def acquire_lock(data_dir: str) -> LockHandle:
    """Acquire the exclusive lock on `<data_dir>/.dubis_lock`.

    Raises DataDirLockedError (naming the other process's pid/port, read
    from the lock file's content) if another live process already holds it.
    A lock file with stale content but no live holder (the previous process
    crashed or was killed — the OS already dropped its lock) is acquired
    normally and its content is overwritten with this process's pid.
    """
    os.makedirs(data_dir, exist_ok=True)
    path = _lock_path(data_dir)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    f = os.fdopen(fd, "r+b")
    _ensure_sentinel_byte(f)

    try:
        _lock_region(f)
    except OSError as exc:
        if not _is_lock_contention(exc):
            # Not contention — permission denied, disk full, antivirus, etc.
            # Must NOT be relabeled as "another server is running"; close
            # the handle we just opened (nothing acquired it) and let the
            # real error propagate unchanged, per this repo's error policy
            # (throw, don't silently swallow/misreport).
            f.close()
            raise
        content = _read_content(f) or {}
        f.close()
        pid = content.get("pid")
        port = content.get("port")
        raise DataDirLockedError(
            f"Another dubIS server is already running against this data "
            f"directory (pid={pid}, port={port}). Data dir: {data_dir}. "
            f"Stop that server (or remove {path} if it's stale) before "
            f"starting a new one.",
            pid=pid,
            port=port,
            data_dir=data_dir,
        ) from exc

    try:
        _write_content(f, {"pid": os.getpid(), "port": None})
    except Exception:
        # We hold the OS lock at this point — release it and close the
        # handle rather than leaking both on this (rare) write failure.
        _unlock_region(f)
        f.close()
        raise
    return LockHandle(f, path, data_dir)
