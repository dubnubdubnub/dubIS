"""Relaunching the desktop app in place — the argv/spawn decision, kept
webview-free so it is unit-testable.

Same reasoning as `remote_mode.py`: `app.pyw` imports `webview` and cannot be
imported in tests, so anything worth testing lives beside it rather than in it.

Why a spawn-then-exit rather than `os.execv`: `Launcher._cleanup()` releases the
data-dir lock LAST, deliberately, so a second instance cannot start writing
while this one is still closing. Replacing the process image mid-teardown would
skip that ordering entirely. Spawning only after cleanup has run keeps the
guarantee: by then the PnP server is stopped, SQLite is committed and closed,
and the lock is free.

The replacement is detached from this process on purpose. It has to outlive us
by definition, and inheriting our process group would mean the imminent
TerminateProcess took it with us.
"""
from __future__ import annotations

import sys
from typing import Mapping, Sequence


def relaunch_argv(executable: str, argv: Sequence[str]) -> list[str]:
    """The command that starts a fresh copy of this app.

    `argv` is `sys.argv` — argv[0] is the script path, which has to be preserved
    so a `.pyw` launch stays a `.pyw` launch (on Windows that is what keeps the
    console window suppressed). Flags are carried through so a restart from a
    `--debug` session is still a `--debug` session; a restart that quietly
    dropped the flags would look like the restart itself changed behaviour.
    """
    if not argv:
        raise ValueError("argv must contain at least the script path")
    return [executable, *argv]


def spawn_kwargs() -> dict:
    """Platform flags that detach the replacement from this process.

    On Windows, DETACHED_PROCESS plus a new process group; elsewhere
    `start_new_session`. Without this the child sits in our process group and
    the `_hard_exit` TerminateProcess that follows would kill it too.
    """
    if sys.platform == "win32":
        # 0x00000008 DETACHED_PROCESS | 0x00000200 CREATE_NEW_PROCESS_GROUP.
        # Spelled numerically because the subprocess constants only exist on
        # Windows, and this module is imported (and tested) everywhere.
        return {"creationflags": 0x00000008 | 0x00000200, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def relaunch_env(env: Mapping[str, str]) -> dict[str, str]:
    """The environment for the replacement.

    `DUBIS_URL` is stripped. It is the highest-precedence source of the server
    URL (`remote_mode.resolve_remote_base_url`), so leaving it in place would
    make a restart-to-apply silently ignore the URL the user just saved in
    preferences — the one thing the restart exists to apply. A user who
    launched with `DUBIS_URL` set and wants it back can set it again; a user who
    edited preferences and got no change would just conclude the field is
    broken.

    `DUBIS_SERVER_PORT` is stripped too: it pins the local server to one port,
    and the process we are replacing may not have released it yet.
    """
    out = {k: v for k, v in env.items() if k not in ("DUBIS_URL", "DUBIS_SERVER_PORT")}
    return out
