"""Which build is answering — the one fact `/v1/health` used to be missing.

Verifying a fremont roll meant six commands (kubectl context, deployment spec,
pod image, rollout status, `/v1/health`, `/v1/meta`) because nothing the app
serves identifies its own build; the image tag was the only truth and it was
only visible from the cluster. This module resolves a build identity once, in
a fixed precedence, and never guesses:

1. **Baked at image build time** — `server/_build_id.py`, a module the
   `Dockerfile` generates from its `ARG GIT_SHA`. It is absent from the repo
   (and gitignored); its presence *is* the signal that this is a built image,
   and it cannot drift because the image carries it, unlike a deployment env
   var someone must remember to update.
2. **`DUBIS_BUILD_SHA` env** — for a source checkout or bundle that wants to
   stamp itself without a git tree.
3. **`git rev-parse HEAD`** in the source tree this module lives in — the
   desktop app and any `python -m server` run from a clone.
4. The literal :data:`UNKNOWN`. Never empty, so "no identity" is
   distinguishable from a real sha at a glance.

A module rather than a data file on purpose: a new tracked file under `data/`
is a decision for `tests/python/test_pyinstaller_spec.py` and a runtime-read
asset needs a `Dockerfile` COPY *and* a `.dockerignore` exemption
(`tests/python/test_container_assets.py`); an importable module imported by
the server package rides every existing packaging path untouched.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

UNKNOWN = "unknown"

# server/build_identity.py -> the repo root when running from a source tree.
# In a container or bundle this path exists but holds no `.git`, which is
# exactly the case tier 3 is supposed to fall through.
REPO_ROOT = Path(__file__).resolve().parents[1]


def _baked_sha() -> str:
    """The sha baked into the image by the Dockerfile, or "" when absent."""
    try:
        from server import _build_id  # noqa: PLC0415 — generated module, may not exist
    except ImportError:
        return ""
    return str(getattr(_build_id, "BUILD_SHA", "") or "").strip()


def _git_head(source_root: Path) -> str:
    """`git rev-parse HEAD` in *source_root*; "" on any failure (no git, no
    tree, no commits). A failure here is a tier falling through, not an error
    — the caller's contract ends in UNKNOWN, not in a raise."""
    try:
        out = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip()


def resolve_build_sha(
    baked: str | None = None,
    environ: Mapping[str, str] | None = None,
    source_root: Path | str | None = None,
) -> str:
    """Resolve the build identity with the precedence documented above.

    Every input is injectable so the precedence is testable without building
    an image or faking a git tree at the real repo root; `None` means "read
    the real thing". Whitespace-only inputs fall through — an empty identity
    is never a tier's answer.
    """
    baked_val = _baked_sha() if baked is None else baked.strip()
    if baked_val:
        return baked_val

    env = os.environ if environ is None else environ
    from_env = (env.get("DUBIS_BUILD_SHA") or "").strip()
    if from_env:
        return from_env

    root = REPO_ROOT if source_root is None else Path(source_root)
    from_git = _git_head(root)
    if from_git:
        return from_git

    return UNKNOWN


@lru_cache(maxsize=1)
def get_build_sha() -> str:
    """The process-wide answer. Resolved once: the tiers cannot change under
    a running process (the image, the env and the git tree are fixed at
    launch), and `/v1/health` is polled every 30s by the container
    HEALTHCHECK and by every server-picker probe — shelling out to git per
    request would make a health check slower than the thing it checks."""
    return resolve_build_sha()
