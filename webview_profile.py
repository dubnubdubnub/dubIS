"""WebView2 persistent-profile self-heal.

The persistent profile (``data/webview2``, see ``app.pyw``) lets WebView2 reuse
its HTTP / V8 / shader caches across launches for a faster cold start. But the
app closes via ``TerminateProcess`` (a hard kill — see ``app.pyw._hard_exit``,
chosen to avoid a ~2s DLL-detach hang), which never lets WebView2 flush and
close its on-disk LevelDB stores. Enough unclean shutdowns corrupt the profile,
and a *corrupt profile makes WebView2 hang during page initialisation*: the
window shows the startup skeleton, but the JS bridge never comes up, so
``whenPywebviewReady()`` never resolves and the user is trapped on the skeleton.

This module makes that non-fatal:

* :func:`prepare_for_launch` — called before the window opens. If a sentinel
  from a prior launch is still present, that launch never reached a live bridge,
  so the profile is suspect: wipe it. Then (re)write the sentinel.
* :func:`mark_ready` — called once JS confirms the bridge is live; removes the
  sentinel so the *next* launch knows this one succeeded.
* A watchdog in ``app.pyw`` uses :func:`should_heal_and_relaunch` to wipe and
  relaunch if the bridge never signals ready within a timeout, recovering within
  the same session rather than on the next launch.
* :func:`kill_child_webview_processes` terminates our own ``msedgewebview2.exe``
  descendants. Called on close (so the hard kill doesn't leave orphans that keep
  the profile locked) and during a heal (so the wipe isn't blocked by a lock).
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sys

logger = logging.getLogger(__name__)

SENTINEL_FILENAME = ".webview2_launching"
_WEBVIEW_PROCESS_NAME = "msedgewebview2.exe"


def prepare_for_launch(profile_dir: str, sentinel_path: str) -> bool:
    """Prepare the persistent profile before opening the window.

    If ``sentinel_path`` already exists, the previous launch wrote it but never
    called :func:`mark_ready` — i.e. it never reached a live JS bridge. Treat the
    profile as corrupt and remove it so this launch starts from a clean one.
    Always (re)writes the sentinel afterwards.

    Returns ``True`` if a wipe occurred. Best-effort: filesystem errors are
    logged, never raised — a self-heal failure must not block startup.
    """
    wiped = False
    if os.path.exists(sentinel_path):
        logger.warning(
            "WebView2 launch sentinel from a prior run is still present; that "
            "launch never reached a live bridge. Clearing possibly-corrupt "
            "profile at %s",
            profile_dir,
        )
        shutil.rmtree(profile_dir, ignore_errors=True)
        wiped = True

    try:
        os.makedirs(os.path.dirname(sentinel_path) or ".", exist_ok=True)
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write("launching")
    except OSError as exc:
        logger.warning("Could not write WebView2 launch sentinel: %s", exc)

    return wiped


def mark_ready(sentinel_path: str) -> None:
    """Remove the launch sentinel: this launch reached a live JS bridge.

    Idempotent and best-effort — a missing sentinel is fine, and any other
    error is logged rather than raised.
    """
    try:
        os.remove(sentinel_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove WebView2 launch sentinel: %s", exc)


def should_heal_and_relaunch(*, ready: bool, persist_profile: bool, already_healed: bool) -> bool:
    """Decide whether the startup watchdog should heal + relaunch.

    Heal only when: we're using the persistent profile (an ephemeral one can't
    carry corruption across launches), the bridge never signalled ready, and we
    have not already healed once this chain (loop guard — a fresh profile that
    *still* hangs is not a profile problem, so don't relaunch forever).
    """
    return persist_profile and not ready and not already_healed


# ── orphan reaping (Windows) ────────────────────────────────────────────────

def _descendant_pids(parent_pid: int, processes: list[tuple[int, int, str]]) -> set[int]:
    """PIDs transitively descended from ``parent_pid``.

    ``processes`` is a list of ``(pid, ppid, name)``. The parent itself is never
    included. Self-parenting / cyclic entries are handled (a PID already seen is
    not expanded again).
    """
    children: dict[int, list[int]] = {}
    for pid, ppid, _name in processes:
        if pid == ppid:
            continue  # ignore self-parenting (e.g. PID 0/4 quirks)
        children.setdefault(ppid, []).append(pid)

    descendants: set[int] = set()
    stack = list(children.get(parent_pid, []))
    while stack:
        pid = stack.pop()
        if pid in descendants or pid == parent_pid:
            continue
        descendants.add(pid)
        stack.extend(children.get(pid, []))
    return descendants


def _webview_child_pids(parent_pid: int, processes: list[tuple[int, int, str]]) -> set[int]:
    """Descendant PIDs of ``parent_pid`` whose image is msedgewebview2.exe."""
    by_pid = {pid: name for pid, _ppid, name in processes}
    return {
        pid
        for pid in _descendant_pids(parent_pid, processes)
        if by_pid.get(pid, "").lower() == _WEBVIEW_PROCESS_NAME
    }


def _enumerate_processes() -> list[tuple[int, int, str]]:
    """Snapshot of ``(pid, ppid, name)`` for every process via Toolhelp32.

    Windows only; returns ``[]`` elsewhere or on any failure. Dependency-free
    (ctypes) so it adds nothing to the runtime requirements.
    """
    if sys.platform != "win32":
        return []

    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return []

    procs: list[tuple[int, int, str]] = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not k.Process32First(snapshot, ctypes.byref(entry)):
            return []
        while True:
            name = entry.szExeFile.decode("ascii", "replace")
            procs.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), name))
            if not k.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        k.CloseHandle(snapshot)
    return procs


def kill_child_webview_processes(parent_pid: int) -> int:
    """Terminate this process's ``msedgewebview2.exe`` descendants (Windows only).

    Used on close — so the ``TerminateProcess`` hard kill doesn't strand WebView2
    children that keep the persistent profile locked across launches — and during
    a heal, so the profile wipe isn't blocked by a live lock. Returns the count
    terminated. Best-effort: failures are logged, never raised.
    """
    if sys.platform != "win32":
        return 0

    from ctypes import wintypes

    PROCESS_TERMINATE = 0x0001
    try:
        pids = _webview_child_pids(parent_pid, _enumerate_processes())
    except OSError as exc:
        logger.warning("Could not enumerate processes to reap WebView2 children: %s", exc)
        return 0

    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    killed = 0
    for pid in pids:
        handle = k.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            continue
        try:
            if k.TerminateProcess(handle, 0):
                killed += 1
        finally:
            k.CloseHandle(handle)
    if killed:
        logger.info("Reaped %d orphaned WebView2 child process(es)", killed)
    return killed
