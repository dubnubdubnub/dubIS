"""Reader install/uninstall — verified downloads in, managed directory out.

Everything the local picture/PDF reader needs (llama.cpp runtime, GGUF weights,
mmproj projector) lands under one managed directory, `<data_dir>/reader/`. This
module owns two halves of that lifecycle:

**Download** (`download_verified`) copies the semantics of the pinned,
checksummed init container in the org's `win-runners/gpu/llamacpp.yaml`:

1. idempotent — a file already present at `dest` with the right sha256 is left
   alone and never re-downloaded;
2. staged — bytes stream to `<dest>.part`, never to `dest`;
3. verified — the sha256 is computed while streaming and compared before
   anything is promoted;
4. atomic — only a verified `.part` is `os.replace`d into place, so `dest`
   either does not exist or is correct. There is no window in which a partial
   or wrong-hash file sits at the final path.

"Resumable-ish" means (2)+(4): an interrupted run costs bytes, never
correctness, and the retry is a plain re-call. It deliberately does *not* do
HTTP range resumption — leftover partial bytes cannot be attributed to the
pinned revision they were started against, and a silently-resumed download of a
*different* revision would produce a file whose hash fails for reasons nobody
can debug. A leftover `.part` is therefore always discarded, never trusted.

**Uninstall** (`plan_uninstall` / `uninstall`) removes that managed directory
and nothing else. It deletes multiple GiB of the user's disk, so the safety
rules are structural rather than advisory:

* it never accepts a path to delete — the caller passes `data_dir`, this module
  derives the managed subdirectory itself (see `managed_dir`);
* the derived path is validated before a single unlink (must be named `reader`,
  must be a direct child of the resolved data dir, must not resolve to the data
  dir itself or to a filesystem root);
* symlinks are removed, never followed, so a link inside `reader/` pointing at
  `$HOME` costs the link and nothing behind it — in the measured byte total as
  well as in the deletion;
* it is idempotent — nothing installed reports 0 bytes and does not raise.

Process management is NOT here. A caller must stop a running reader server
*first* (`reader_runtime.stop_server`), then call `uninstall`: on Windows an
open mmap of a GGUF makes the unlink fail outright, and on POSIX the inode
survives until the process exits, so the "reclaimed" bytes would not come back.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from dubis_errors import DubISError

logger = logging.getLogger(__name__)

MANAGED_DIRNAME = "reader"
"""The single directory under `data_dir` this module is allowed to create or
delete. Derived, never supplied by a caller."""

PART_SUFFIX = ".part"
"""Staging suffix. A `<name>.part` is by definition incomplete: it is written
in place of `dest`, discarded if found left over, and only ever promoted after
its sha256 matches."""

_CHUNK_SIZE = 1 << 20  # 1 MiB — big enough that hashing dominates, small enough for a live progress bar
_DEFAULT_TIMEOUT = 60.0  # connect/read timeout; a multi-GiB body has no overall deadline

ProgressCallback = Callable[[int, "int | None"], None]
"""`progress(bytes_done, bytes_total)`. `bytes_total` is `None` when the server
sent no `Content-Length` — distinct from `0`, which means a genuinely
zero-length file. Calls are monotonic in `bytes_done`, and the terminal call
always carries a known total equal to `bytes_done`, so it reports 100%."""


class ReaderInstallError(DubISError):
    """Base for reader install/uninstall failures."""


class DownloadError(ReaderInstallError):
    """The bytes could not be fetched (DNS, connection, HTTP status, truncation).

    Carries `url` so a job/status line can name what failed without the caller
    re-deriving it."""

    def __init__(self, message: str, *, url: str = ""):
        super().__init__(message)
        self.url = url


class ChecksumMismatchError(ReaderInstallError):
    """The downloaded bytes hashed to something other than the pinned sha256.

    Raised *after* the `.part` has been deleted and *before* anything is
    promoted, so no file exists at the destination when this surfaces. Pinned
    hashes are the whole point of this module: a mismatch is either upstream
    drift or a corrupted transfer, and both must be loud."""

    def __init__(self, message: str, *, url: str = "", expected: str = "", actual: str = ""):
        super().__init__(message)
        self.url = url
        self.expected = expected
        self.actual = actual


class UnsafeUninstallTargetError(ReaderInstallError):
    """The derived managed directory failed a safety check, so nothing was deleted.

    This is a bug-or-tampering signal, not a user error: `<data_dir>/reader`
    resolving to the data dir itself, to a filesystem root, or out of the tree
    via a symlink means a delete would take something we never created."""


def progress_pct(bytes_done: int, bytes_total: int | None) -> float | None:
    """Percentage for a progress report, or `None` when it is indeterminate.

    Three distinct cases, and conflating any two of them is the bug this
    function exists to prevent:

    * `bytes_total is None` — no `Content-Length`; there is no percentage. Not
      0.0, which a progress bar would render as "stuck at the start".
    * `bytes_total == 0` — a real zero-length file, which is complete: 100.0.
    * otherwise — clamped to 0..100 so a body longer than its declared length
      cannot report 103%.
    """
    if bytes_total is None:
        return None
    if bytes_total <= 0:
        return 100.0
    return max(0.0, min(100.0, bytes_done * 100.0 / bytes_total))


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of one `download_verified` call.

    `skipped` distinguishes "already correct on disk" from "fetched now", which
    is what makes a re-run of a multi-file install cheap and observable.
    """

    path: str
    bytes: int
    sha256: str
    skipped: bool

    def as_dict(self) -> dict:
        return {"path": self.path, "bytes": self.bytes, "sha256": self.sha256, "skipped": self.skipped}


@dataclass(frozen=True)
class UninstallPlan:
    """What `uninstall` *would* remove, for the confirm dialog.

    The design doc requires the confirm to name the directory and the space it
    frees, so both are first-class here. `entries` is the top-level listing
    only — deep enough to show "runtime, models", shallow enough to render.
    """

    path: str
    exists: bool
    bytes_total: int
    file_count: int
    entries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "exists": self.exists,
            "bytes_total": self.bytes_total,
            "file_count": self.file_count,
            "entries": list(self.entries),
        }


# --------------------------------------------------------------------------
# Managed paths — derived from data_dir, never accepted from a caller
# --------------------------------------------------------------------------

def managed_dir(data_dir: str | os.PathLike[str]) -> Path:
    """`<data_dir>/reader` — the only directory this module creates or deletes.

    Validated on the way out (see `UnsafeUninstallTargetError`) so every caller,
    install or uninstall, works from a path that already passed the checks.
    """
    return _validated_managed_dir(data_dir)


def runtime_dir(data_dir: str | os.PathLike[str]) -> Path:
    """Where the llama.cpp release binaries land. Inside the managed dir, so
    uninstall covers them without knowing they exist."""
    return managed_dir(data_dir) / "runtime"


def models_dir(data_dir: str | os.PathLike[str]) -> Path:
    """Where the GGUF weights and the mmproj projector land."""
    return managed_dir(data_dir) / "models"


def _validated_managed_dir(data_dir: str | os.PathLike[str]) -> Path:
    raw = str(data_dir or "").strip()
    if not raw:
        raise UnsafeUninstallTargetError("data_dir is empty; refusing to derive a managed reader directory")

    base = Path(raw).expanduser()
    resolved_base = base.resolve()
    if resolved_base == Path(resolved_base.anchor):
        raise UnsafeUninstallTargetError(
            f"data_dir {raw!r} resolves to the filesystem root {resolved_base}; refusing to manage {resolved_base}/"
            f"{MANAGED_DIRNAME}"
        )

    target = base / MANAGED_DIRNAME
    resolved_target = target.resolve()

    if resolved_target == Path(resolved_target.anchor):
        raise UnsafeUninstallTargetError(f"{target} resolves to the filesystem root {resolved_target}")
    if resolved_target == resolved_base:
        raise UnsafeUninstallTargetError(f"{target} resolves to the data dir itself ({resolved_base})")
    if resolved_target.name != MANAGED_DIRNAME:
        raise UnsafeUninstallTargetError(
            f"{target} resolves to {resolved_target}, which is not named {MANAGED_DIRNAME!r} — "
            "it is a link or mount pointing out of the managed tree"
        )
    if resolved_target.parent != resolved_base:
        raise UnsafeUninstallTargetError(
            f"{target} resolves to {resolved_target}, which is not a direct child of {resolved_base}"
        )
    return target


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = _CHUNK_SIZE) -> str:
    """Streaming sha256 of a file, lowercase hex. Never loads a GiB into RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_sha256(value: str) -> str:
    cleaned = (value or "").strip().lower()
    if len(cleaned) != 64 or any(c not in "0123456789abcdef" for c in cleaned):
        raise ValueError(f"expected a 64-char hex sha256, got {value!r}")
    return cleaned


class _Emitter:
    """Monotonic progress relay.

    Two invariants the callback can rely on: `bytes_done` never goes backwards
    (a retry or a clamp cannot make a progress bar jump left), and a declared
    total that turns out to be short is widened rather than allowed to report
    over 100%.
    """

    def __init__(self, progress: ProgressCallback | None):
        self._progress = progress
        self._last = -1

    def emit(self, done: int, total: int | None) -> None:
        if self._progress is None:
            return
        if done < self._last:
            done = self._last
        self._last = done
        if total is not None and total < done:
            total = done
        self._progress(done, total)


def download_verified(
    url: str,
    dest: str | os.PathLike[str],
    sha256: str,
    progress: ProgressCallback | None = None,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    chunk_size: int = _CHUNK_SIZE,
    session: requests.Session | None = None,
) -> DownloadResult:
    """Fetch `url` to `dest`, verify `sha256`, promote atomically.

    Idempotent: a `dest` that already hashes to `sha256` is returned as
    `skipped=True` without a single byte of network traffic — re-running an
    interrupted install re-downloads only what is actually missing.

    Raises `ChecksumMismatchError` (nothing at `dest`), `DownloadError`, or
    `ValueError` for a malformed `sha256`. The `sha256` is validated before the
    request is made, so a typo'd pin fails instantly instead of after a 5 GiB
    transfer.
    """
    expected = _normalize_sha256(sha256)
    dest_path = Path(dest)
    part_path = dest_path.with_name(dest_path.name + PART_SUFFIX)
    emitter = _Emitter(progress)

    if dest_path.exists() and dest_path.is_file():
        actual = sha256_file(dest_path)
        if actual == expected:
            size = dest_path.stat().st_size
            emitter.emit(size, size)
            logger.info("reader: %s already present and verified, skipping download", dest_path)
            return DownloadResult(path=str(dest_path), bytes=size, sha256=actual, skipped=True)
        logger.warning(
            "reader: %s exists but hashes %s, expected %s — re-downloading", dest_path, actual, expected
        )

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # A leftover `.part` is incomplete bytes from an interrupted run, possibly
    # of a different revision. It is never resumed and never promoted.
    if part_path.exists() or part_path.is_symlink():
        logger.warning("reader: discarding leftover partial download %s", part_path)
        part_path.unlink()

    digest = hashlib.sha256()
    done = 0
    getter = session.get if session is not None else requests.get
    try:
        with getter(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = _declared_length(resp)
            emitter.emit(0, total)
            with open(part_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    emitter.emit(done, total)
                fh.flush()
                os.fsync(fh.fileno())
    except requests.RequestException as exc:
        # The `.part` is left in place: it is harmless (the next attempt
        # discards it) and deleting it here would race a caller inspecting it.
        raise DownloadError(f"download of {url} failed: {exc}", url=url) from exc

    actual = digest.hexdigest()
    if actual != expected:
        part_path.unlink(missing_ok=True)
        raise ChecksumMismatchError(
            f"sha256 mismatch for {url}: expected {expected}, got {actual}",
            url=url,
            expected=expected,
            actual=actual,
        )

    os.replace(part_path, dest_path)
    # Terminal report carries a known total even when Content-Length was absent,
    # so the last thing a progress bar sees is unambiguously 100%.
    emitter.emit(done, done)
    logger.info("reader: downloaded %s (%d bytes) to %s", url, done, dest_path)
    return DownloadResult(path=str(dest_path), bytes=done, sha256=actual, skipped=False)


def _declared_length(resp: requests.Response) -> int | None:
    """`Content-Length` as an int, or `None` when absent/unparseable.

    A chunked or compressed response has no usable length; reporting `None`
    keeps `progress_pct` indeterminate instead of inventing a denominator.
    """
    raw = resp.headers.get("Content-Length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("reader: unparseable Content-Length %r, treating total as unknown", raw)
        return None
    return value if value >= 0 else None


# --------------------------------------------------------------------------
# Uninstall
# --------------------------------------------------------------------------

def plan_uninstall(data_dir: str | os.PathLike[str]) -> UninstallPlan:
    """Report what `uninstall(data_dir)` would remove, and how many bytes.

    Read-only. Takes the same single `data_dir` argument and derives the same
    validated path, so the confirm dialog can never describe a different
    directory than the one that gets deleted. Symlinks count as their own link
    size, never as the size of whatever they point at.
    """
    target = _validated_managed_dir(data_dir)
    if not target.exists() and not target.is_symlink():
        return UninstallPlan(path=str(target), exists=False, bytes_total=0, file_count=0, entries=[])
    total, count = _measure(target)
    return UninstallPlan(
        path=str(target),
        exists=True,
        bytes_total=total,
        file_count=count,
        entries=_top_level_entries(target),
    )


def uninstall(data_dir: str | os.PathLike[str]) -> int:
    """Delete `<data_dir>/reader` and return the bytes reclaimed.

    Stop the reader server FIRST — process management belongs to
    `reader_runtime.py`, not here. A live llama-server holding an mmap of a
    GGUF makes this fail on Windows and makes the reclaimed bytes a lie on
    POSIX (the inode survives until the process exits).

    Idempotent: nothing installed returns 0 and does not raise. Local-only —
    `reader_mode` `remote` keeps working, and `local`/`auto` fall back.
    """
    target = _validated_managed_dir(data_dir)

    if target.is_symlink():
        # We never create `reader` as a link. Deleting *through* one would take
        # bytes outside the managed tree, so refuse and let a human look.
        raise UnsafeUninstallTargetError(
            f"{target} is a symlink to {os.readlink(target)}; refusing to delete through it"
        )
    if not target.exists():
        return 0

    if target.is_file():
        reclaimed = target.stat().st_size
        target.unlink()
        logger.warning("reader: removed stray file at %s (%d bytes)", target, reclaimed)
        return reclaimed

    reclaimed, count = _measure(target)
    shutil.rmtree(target)  # does not follow symlinks: links inside are unlinked, not descended
    logger.info("reader: uninstalled %s, reclaimed %d bytes across %d files", target, reclaimed, count)
    return reclaimed


def _measure(root: Path) -> tuple[int, int]:
    """(bytes, entry count) under `root`, never following a symlink out.

    Every `stat` is `follow_symlinks=False`, so a link to a 500 GiB directory
    contributes the size of the link itself. Anything else would let a link
    inflate the "space you will reclaim" number by orders of magnitude.
    """
    if root.is_symlink():
        return root.lstat().st_size, 1
    if root.is_file():
        return root.stat().st_size, 1

    total = 0
    count = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        total += entry.stat(follow_symlinks=False).st_size
                        count += 1
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                        count += 1
        except OSError as exc:
            # Best effort: an unreadable subdirectory makes the total an
            # underestimate, which is better than a confirm dialog that raises.
            logger.warning("reader: could not measure %s: %s", current, exc)
    return total, count


def _top_level_entries(root: Path) -> list[str]:
    try:
        return sorted(p.name for p in root.iterdir())
    except OSError as exc:
        logger.warning("reader: could not list %s: %s", root, exc)
        return []
