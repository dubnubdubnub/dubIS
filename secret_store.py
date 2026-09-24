"""Write credential files so only the owning user can read them.

Three files in the data dir are plaintext secrets — `digikey_cookies.json`
(a live session), `mouser_credentials.json` (an API key) and
`jlc_sessions.json` (one live session per JLC account). Written with
`open(path, "w")` they land at the process umask's default, typically `0644`:
world-readable on any shared box, and world-readable inside the container
image's PVC. `docs/plans/2026-09-20-extension-credential-capture.md` rule 8
makes `0600` the house rule for all three, which is only one rule if all three
go through one function.

Two steps, not one: `os.open`'s *mode* applies **only when the file is
created**, so a file that already exists at `0644` would keep those bits
forever. The explicit `chmod` after the write is what makes an existing loose
file tighten on the next save.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from typing import Any

logger = logging.getLogger(__name__)

#: Owner read/write, nothing for group or other.
PRIVATE_MODE = 0o600


def harden(path: str) -> None:
    """Best-effort `chmod 0600`. Never raises — a filesystem that cannot
    express Unix modes (a Windows share, FAT) is not a reason to fail the
    write that just succeeded, but it IS worth a warning."""
    try:
        os.chmod(path, PRIVATE_MODE)
    except OSError as exc:
        logger.warning("Could not restrict permissions on %s: %s", path, exc)


def write_private_json(path: str, data: Any) -> None:
    """Serialize *data* as JSON to *path*, owner-only (`0600`).

    Creates the file with `0600` directly (so it is never briefly world
    readable between creation and chmod) and re-applies the mode afterwards
    for a file that already existed with looser bits.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_MODE)
    try:
        handle = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        # Only reachable when fdopen itself failed, i.e. before it took
        # ownership of the descriptor — otherwise closing here would close an
        # fd the file object has already closed and possibly reused.
        os.close(fd)
        raise
    with handle as f:
        json.dump(data, f)
    harden(path)


def is_private(path: str) -> bool:
    """True when *path* is mode `0600`. Used by tests and diagnostics."""
    return stat.S_IMODE(os.stat(path).st_mode) == PRIVATE_MODE
