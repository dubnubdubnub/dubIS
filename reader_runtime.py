"""llama.cpp runtime: which binary to fetch for this machine, and how to run it.

Two halves, both narrow on purpose.

**Acquisition** is a pinned table, not a search. Every entry names an exact
``ggml-org/llama.cpp`` release tag, an exact release-asset filename, and that
asset's sha256 — the same auditable pattern as the fleet's
``win-runners/gpu/llamacpp.yaml`` init container. A wrong asset name here is a
failed install on the user's machine *after* a several-hundred-megabyte
download, so the table is generated from, and re-checkable against, the real
releases API::

    curl -s https://api.github.com/repos/ggml-org/llama.cpp/releases \\
      | jq -r '.[] | select(.tag_name=="b10549") | .assets[] | "\\(.name) \\(.digest) \\(.size)"'

Every value below was taken from that response for tag ``b10549`` on
2026-08-21, and four of the archives were additionally downloaded and hashed
locally to confirm the API's ``digest`` field agrees with the bytes GitHub
serves (macos-arm64, ubuntu-x64, ubuntu-vulkan-x64, win-cpu-x64 — all matched).
The ``server_relpath`` values come from listing those same archives: the
tarballs put everything under a single ``llama-<tag>/`` directory, the Windows
zips are flat.

**Downloading is not this module's job.** ``reader_install.py`` owns bytes,
progress and atomic renames; ``ensure_runtime`` takes that downloader as an
injected callable, which is also what lets the tests exercise the whole
extraction path without touching the network.

**Supervision** spawns ``llama-server`` on a *freshly chosen free loopback
port* — never llama.cpp's default 8080, which is already taken by an unrelated
llama-server on mauler and by dubIS itself inside the container — polls
``/health`` until the model is resident, and guarantees the child dies with the
app. That last guarantee needs four mechanisms because no single one covers all
three platforms; see ``LlamaServer.start`` and ``reap_orphan``. An orphaned
llama-server holds multiple GiB of VRAM until someone notices.
"""
from __future__ import annotations

import atexit
import ctypes
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from dubis_errors import DubISError

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
# These subclass DubISError (dubis_errors.py) so they inherit the repo's
# hierarchy and its 500-by-default HTTP mapping, but they live here rather than
# in dubis_errors.py: the reader is a client-shell concern (it installs and runs
# binaries on the *client's* disk, never over /v1), so nothing in server/ needs
# to import or special-case them.

class ReaderRuntimeError(DubISError):
    """Base for every llama.cpp runtime acquisition/supervision failure."""


class ReaderPlatformUnsupportedError(ReaderRuntimeError):
    """No pinned llama.cpp release asset exists for this platform.

    Raised instead of guessing: silently falling back to, say, a CPU build on a
    platform we have not pinned would download hundreds of megabytes and then
    fail (or run 50x too slowly) with no explanation. Carries the rejected key
    and the keys that *are* on offer so the caller can say something useful.
    """

    def __init__(self, message: str, *, platform_key: str = "", available: tuple[str, ...] = ()):
        super().__init__(message)
        self.platform_key = platform_key
        self.available = tuple(available)


class ReaderStartTimeoutError(ReaderRuntimeError):
    """`llama-server` never answered GET /health within the timeout."""

    def __init__(self, message: str, *, timeout: float = 0.0, log_tail: str = ""):
        super().__init__(message)
        self.timeout = timeout
        self.log_tail = log_tail


class ReaderProcessExitedError(ReaderRuntimeError):
    """`llama-server` exited during startup (bad GGUF, no GPU, missing DLL...)."""

    def __init__(self, message: str, *, returncode: int | None = None, log_tail: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.log_tail = log_tail


# --------------------------------------------------------------------------- #
# The pinned acquisition table
# --------------------------------------------------------------------------- #

#: The release these assets belong to. Bumping it means re-reading *every*
#: filename, size and digest from the releases API — the asset names embed the
#: tag, so a half-done bump 404s at install time.
LLAMACPP_RELEASE_TAG = "b10549"

#: Latest non-prerelease at pin time (published 2026-08-21T09:23:07Z, commit
#: b2e5e9b28b2484fbf94b543432ece638996a8b97).
_RELEASE_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMACPP_RELEASE_TAG}"

#: `-ngl` for a GPU build. llama.cpp clamps this to the model's actual layer
#: count, so "999" is the idiomatic "offload everything".
_ALL_LAYERS = 999


@dataclass(frozen=True)
class RuntimeAsset:
    """One release asset: exactly which bytes, and how to know they are right."""

    filename: str
    sha256: str
    size_bytes: int

    @property
    def url(self) -> str:
        """Derived, never hand-typed — a copy-pasted URL is how a table like
        this ends up pointing at a different release than its tag claims."""
        return f"{_RELEASE_BASE}/{self.filename}"


@dataclass(frozen=True)
class RuntimeBuild:
    """The complete recipe for getting a runnable `llama-server` on one platform."""

    key: str
    archive: RuntimeAsset
    #: Archives that must be unpacked *alongside* the server binary. Only the
    #: Windows CUDA builds need this, and they genuinely do: llama.cpp ships the
    #: CUDA runtime DLLs in a separate `cudart-*` zip and llama-server.exe will
    #: not start without them.
    extra_archives: tuple[RuntimeAsset, ...] = ()
    #: Where `llama-server` sits once the archive is unpacked. Verified by
    #: listing the real archives: tarballs nest under `llama-<tag>/`, Windows
    #: zips are flat.
    server_relpath: str = f"llama-{LLAMACPP_RELEASE_TAG}/llama-server"
    #: "metal" | "cuda" | "vulkan" | "cpu"
    accelerator: str = "cpu"
    #: `-ngl` value for this build. 0 for CPU builds: asking a CPU build to
    #: offload layers is either an error or a silent no-op depending on version.
    gpu_layers: int = 0
    notes: str = ""

    @property
    def release_tag(self) -> str:
        return LLAMACPP_RELEASE_TAG

    @property
    def total_bytes(self) -> int:
        """Download total, for the install progress bar."""
        return self.archive.size_bytes + sum(a.size_bytes for a in self.extra_archives)


_TAR_SERVER = f"llama-{LLAMACPP_RELEASE_TAG}/llama-server"
_ZIP_SERVER = "llama-server.exe"

#: Platform -> release asset. Keys are `<os>-<arch>-<accelerator>` so the key
#: itself cannot lie about which archive it names.
RUNTIME_BUILDS: dict[str, RuntimeBuild] = {
    # ---- macOS -----------------------------------------------------------
    # Metal is built into the macos-arm64 build; there is no separate asset and
    # no runtime to install alongside it.
    "darwin-arm64-metal": RuntimeBuild(
        key="darwin-arm64-metal",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-macos-arm64.tar.gz",
            "71e4b31afb020d6b71894eb8d1f2c0693038aec3f41f672f9fafb5055c8f2226",
            11093286,
        ),
        server_relpath=_TAR_SERVER,
        accelerator="metal",
        gpu_layers=_ALL_LAYERS,
        notes="Metal is compiled in; unified memory means the whole model is 'VRAM'.",
    ),
    # Intel Macs have no usable GPU backend in the published builds, so this is
    # honestly a CPU build rather than a pretend-accelerated one.
    "darwin-x64-cpu": RuntimeBuild(
        key="darwin-x64-cpu",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-macos-x64.tar.gz",
            "94177680843a187881ae54021bbad8211c40797cab0df923ef17ee735e3ade09",
            11395189,
        ),
        server_relpath=_TAR_SERVER,
        accelerator="cpu",
        notes="No Metal on Intel Macs in the published builds; CPU only, expect minutes per page.",
    ),

    # ---- Windows ---------------------------------------------------------
    # CUDA 12.4 rather than 13.3: it accepts far older NVIDIA drivers, and the
    # reader is a click-to-install feature where a driver-version failure is
    # indistinguishable from a broken download to the user.
    "win-x64-cuda": RuntimeBuild(
        key="win-x64-cuda",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-win-cuda-12.4-x64.zip",
            "2e980ae28b40c92c9c30bdbcf3f28064b40104472e213c52edbeb89b920d65fe",
            250969968,
        ),
        extra_archives=(
            RuntimeAsset(
                "cudart-llama-bin-win-cuda-12.4-x64.zip",
                "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
                391443627,
            ),
        ),
        server_relpath=_ZIP_SERVER,
        accelerator="cuda",
        gpu_layers=_ALL_LAYERS,
        notes="Needs the separate cudart zip unpacked next to llama-server.exe.",
    ),
    "win-x64-cuda-13.3": RuntimeBuild(
        key="win-x64-cuda-13.3",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-win-cuda-13.3-x64.zip",
            "67a1097716a4b4c20b94d248d1b3886fd7b91b73d9af5e0630fd6a25a32309a5",
            146945631,
        ),
        extra_archives=(
            RuntimeAsset(
                "cudart-llama-bin-win-cuda-13.3-x64.zip",
                "1462a050eb4c684921ba51dcc4cc488a036674c3e73e9945ee705b854808d03e",
                390970417,
            ),
        ),
        server_relpath=_ZIP_SERVER,
        accelerator="cuda",
        gpu_layers=_ALL_LAYERS,
        notes="Opt-in: smaller than the 12.4 build but requires a recent NVIDIA driver.",
    ),
    # Vendor-neutral GPU escape hatch: AMD/Intel dGPUs, and NVIDIA cards whose
    # driver is too old for the pinned CUDA runtime.
    "win-x64-vulkan": RuntimeBuild(
        key="win-x64-vulkan",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-win-vulkan-x64.zip",
            "8e7b0e6382a5bcbf57c79cf54b61483e9f7b26561d4413f28095cdaee256207b",
            34936498,
        ),
        server_relpath=_ZIP_SERVER,
        accelerator="vulkan",
        gpu_layers=_ALL_LAYERS,
        notes="Vendor-neutral GPU offload; slower than CUDA but needs no CUDA runtime.",
    ),
    "win-x64-cpu": RuntimeBuild(
        key="win-x64-cpu",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-win-cpu-x64.zip",
            "11d38f2ed878489b2c3d02b3d1a67683c02fbfb3d265876b9ede749a8dff5f1c",
            18581129,
        ),
        server_relpath=_ZIP_SERVER,
        accelerator="cpu",
    ),
    "win-arm64-cpu": RuntimeBuild(
        key="win-arm64-cpu",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-win-cpu-arm64.zip",
            "88453b6c9ca186885ac22b3505f5591381068d830ebc622a499af73a3607d8c2",
            12339627,
        ),
        server_relpath=_ZIP_SERVER,
        accelerator="cpu",
    ),

    # ---- Linux -----------------------------------------------------------
    # NOTE — there is NO Linux CUDA release asset. Checked every published
    # release: the ubuntu family is {x64, arm64, vulkan-x64, vulkan-arm64,
    # rocm-*, sycl-*, openvino-*, s390x} and nothing else. Upstream expects
    # Linux CUDA users to build from source or run the ghcr.io container image.
    # Rather than invent a hash for an asset that does not exist, the
    # "linux-cuda" alias resolves here: the Vulkan build *does* offload to an
    # NVIDIA card through its Vulkan driver, at some throughput cost.
    "linux-x64-vulkan": RuntimeBuild(
        key="linux-x64-vulkan",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-ubuntu-vulkan-x64.tar.gz",
            "7e3a48ce9d6445cc7296691c240ab75d417558be999716209eaa70a06170a6b3",
            33294970,
        ),
        server_relpath=_TAR_SERVER,
        accelerator="vulkan",
        gpu_layers=_ALL_LAYERS,
        notes=(
            "Upstream publishes no Linux CUDA archive, so this is what a Linux "
            "NVIDIA host gets: Vulkan offload via the NVIDIA driver. Needs "
            "libvulkan1 + the vendor ICD present on the host."
        ),
    ),
    "linux-arm64-vulkan": RuntimeBuild(
        key="linux-arm64-vulkan",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-ubuntu-vulkan-arm64.tar.gz",
            "9e8bd06b973a91625a5a27f5222c568dc6bf77ba091d2c781f0db74ba33c095d",
            27272989,
        ),
        server_relpath=_TAR_SERVER,
        accelerator="vulkan",
        gpu_layers=_ALL_LAYERS,
        notes="Same no-CUDA-asset caveat as linux-x64-vulkan.",
    ),
    "linux-x64-cpu": RuntimeBuild(
        key="linux-x64-cpu",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-ubuntu-x64.tar.gz",
            "66b26d8cb3ab8edaf5a12bfe642b8f00844925f614f196a96a222b7ed1582c1d",
            16675230,
        ),
        server_relpath=_TAR_SERVER,
        accelerator="cpu",
    ),
    "linux-arm64-cpu": RuntimeBuild(
        key="linux-arm64-cpu",
        archive=RuntimeAsset(
            f"llama-{LLAMACPP_RELEASE_TAG}-bin-ubuntu-arm64.tar.gz",
            "461d4b8775807fe39a418ea82b69c477e0e861ab8c5141af20d9c2c4975a3f2a",
            13536090,
        ),
        server_relpath=_TAR_SERVER,
        accelerator="cpu",
    ),
}

#: Short names the rest of the codebase (and the plan) speaks, mapped onto the
#: canonical `<os>-<arch>-<accel>` keys. `linux-cuda` is deliberately an alias
#: for the Vulkan build — see the NOTE above the Linux entries.
PLATFORM_ALIASES: dict[str, str] = {
    "darwin-arm64": "darwin-arm64-metal",
    "darwin-metal": "darwin-arm64-metal",
    "darwin-x64": "darwin-x64-cpu",
    "win-cuda": "win-x64-cuda",
    "win-vulkan": "win-x64-vulkan",
    "win-cpu": "win-x64-cpu",
    "linux-cuda": "linux-x64-vulkan",
    "linux-vulkan": "linux-x64-vulkan",
    "linux-cpu": "linux-x64-cpu",
}


def build_for(platform_key: str) -> RuntimeBuild:
    """The pinned build for `platform_key` (canonical key or alias).

    Raises `ReaderPlatformUnsupportedError` for anything unrecognised — the one
    thing this must never do is quietly hand back some other platform's asset.
    """
    key = PLATFORM_ALIASES.get(platform_key, platform_key)
    build = RUNTIME_BUILDS.get(key)
    if build is None:
        offered = tuple(sorted(set(RUNTIME_BUILDS) | set(PLATFORM_ALIASES)))
        raise ReaderPlatformUnsupportedError(
            f"no pinned llama.cpp {LLAMACPP_RELEASE_TAG} release asset for platform "
            f"{platform_key!r}; known platforms: {', '.join(offered)}",
            platform_key=platform_key,
            available=offered,
        )
    return build


_ARCH_X64 = {"x86_64", "amd64", "x64", "i386", "i686"}
_ARCH_ARM64 = {"arm64", "aarch64", "armv8", "armv8l"}


def detect_platform_key(*, system: str | None = None, machine: str | None = None,
                        has_gpu: bool = False) -> str:
    """Canonical key for this host (or the described one).

    `has_gpu` is the caller's answer — `reader_memory.detect_budget()` is what
    actually knows whether there is a usable discrete GPU, and this module
    deliberately does not duplicate that probe.
    """
    import platform as _platform

    system = (system or _platform.system()).lower()
    machine = (machine or _platform.machine()).lower()

    if machine in _ARCH_ARM64:
        arch = "arm64"
    elif machine in _ARCH_X64:
        arch = "x64"
    else:
        raise ReaderPlatformUnsupportedError(
            f"unrecognised CPU architecture {machine!r} on {system!r}",
            platform_key=f"{system}-{machine}",
            available=tuple(sorted(RUNTIME_BUILDS)),
        )

    if system == "darwin":
        # Metal only exists in the arm64 build; an Intel Mac gets the CPU build.
        return "darwin-arm64-metal" if arch == "arm64" else "darwin-x64-cpu"
    if system == "windows":
        if arch == "arm64":
            return "win-arm64-cpu"  # no CUDA/Vulkan asset for Windows-on-ARM
        return "win-x64-cuda" if has_gpu else "win-x64-cpu"
    if system == "linux":
        # ...-vulkan, not ...-cuda: upstream ships no Linux CUDA archive.
        if has_gpu:
            return f"linux-{arch}-vulkan"
        return f"linux-{arch}-cpu"

    raise ReaderPlatformUnsupportedError(
        f"unsupported operating system {system!r}",
        platform_key=f"{system}-{arch}",
        available=tuple(sorted(RUNTIME_BUILDS)),
    )


# --------------------------------------------------------------------------- #
# Acquisition through an injected downloader
# --------------------------------------------------------------------------- #

class Downloader(Protocol):
    """The one thing this module needs from `reader_install.py`.

    Keeping the seam this narrow is what lets the runtime table be tested with a
    fake that writes a two-file tarball, instead of a several-hundred-megabyte
    download. The real implementation is expected to stream to `<dest>.part`,
    verify `sha256`, and atomically rename.
    """

    def __call__(self, *, url: str, sha256: str, dest: Path, size_bytes: int = 0) -> Path:
        ...  # pragma: no cover - structural type


@dataclass(frozen=True)
class DownloadRequest:
    """One (url, sha256) -> path job for the injected downloader."""

    asset: RuntimeAsset
    dest: Path

    @property
    def url(self) -> str:
        return self.asset.url

    @property
    def sha256(self) -> str:
        return self.asset.sha256

    @property
    def size_bytes(self) -> int:
        return self.asset.size_bytes


def plan_runtime_download(build: RuntimeBuild, dest_dir: Path | str) -> list[DownloadRequest]:
    """Every archive `build` needs, in fetch order, with its destination path.

    Split out from `ensure_runtime` so the install job can total the bytes up
    front and drive a real percentage instead of a spinner.
    """
    archives = Path(dest_dir) / "archives"
    return [DownloadRequest(a, archives / a.filename)
            for a in (build.archive, *build.extra_archives)]


def ensure_runtime(build: RuntimeBuild, dest_dir: Path | str, *,
                   download: Downloader) -> Path:
    """Return the path to a runnable `llama-server`, downloading/unpacking if needed.

    Idempotent: an already-unpacked runtime short-circuits without a single
    request, which is what makes a retried install cheap.
    """
    dest_dir = Path(dest_dir)
    server = dest_dir / build.server_relpath
    if server.is_file():
        logger.debug("llama.cpp runtime already present at %s", server)
        return server

    dest_dir.mkdir(parents=True, exist_ok=True)
    for req in plan_runtime_download(build, dest_dir):
        req.dest.parent.mkdir(parents=True, exist_ok=True)
        archive = Path(download(url=req.url, sha256=req.sha256, dest=req.dest,
                                size_bytes=req.size_bytes))
        # The primary archive lands in dest_dir (its own `llama-<tag>/` subdir
        # for tarballs); the extras must land in the *server's* directory,
        # because that is the only place Windows looks for the CUDA DLLs.
        target = dest_dir if req.asset is build.archive else server.parent
        target.mkdir(parents=True, exist_ok=True)
        _extract(archive, target)

    if not server.is_file():
        raise ReaderRuntimeError(
            f"{build.archive.filename} did not contain {build.server_relpath!r} — "
            f"the pinned archive layout for {build.key} has changed"
        )
    if os.name != "nt":
        server.chmod(server.stat().st_mode | 0o755)
    return server


def _extract(archive: Path, target: Path) -> None:
    """Unpack `archive` into `target`, refusing anything that escapes it.

    A release asset is a downloaded-from-the-internet archive; `tarfile` and
    `zipfile` will both happily write outside the destination given a
    `../` member unless told not to.
    """
    if archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                _reject_escape(member.name, target)
            try:
                tf.extractall(target, filter="data")
            except tarfile.TarError as exc:
                raise ReaderRuntimeError(f"{archive.name}: bad tar archive: {exc}") from exc
    elif archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                _reject_escape(name, target)
            zf.extractall(target)
    else:
        raise ReaderRuntimeError(f"{archive.name}: unsupported archive type")


def _reject_escape(name: str, target: Path) -> None:
    resolved = (target / name).resolve()
    if resolved != target.resolve() and target.resolve() not in resolved.parents:
        raise ReaderRuntimeError(
            f"archive member {name!r} would be written outside {target} — refusing to extract"
        )


# --------------------------------------------------------------------------- #
# Supervision
# --------------------------------------------------------------------------- #

#: llama.cpp's own default. Never used: on mauler it is an unrelated
#: llama-server, and inside the dubIS container it is dubIS itself.
LLAMA_CPP_DEFAULT_PORT = 8080

#: First load of a multi-GB GGUF plus its projector onto a card is slow — cold
#: page cache, a full copy over PCIe, and the projector loaded separately.
#: Ten minutes is generous on purpose; the failure mode we want is "eventually
#: works", not "gave up while it was still loading".
DEFAULT_READY_TIMEOUT = 600.0

#: Records the running child so the *next* launch can reap it if this process
#: dies without running its own cleanup (the macOS gap, see `reap_orphan`).
STATE_FILENAME = "llama-server.json"

_LOG_FILENAME = "llama-server.log"
_LOG_TAIL_BYTES = 4096

# Windows: don't flash a console window. app.pyw runs under pythonw.exe
# precisely to avoid one.
_CREATE_NO_WINDOW = 0x08000000
# Windows Job Object: when the last handle to the job closes — including because
# the parent was TerminateProcess'd, which skips atexit entirely — the OS kills
# every process in it.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

# Linux: ask the kernel to signal the child when *this* process dies, for any
# reason including SIGKILL. The only mechanism that survives a hard kill.
_PR_SET_PDEATHSIG = 1


def free_loopback_port() -> int:
    """A currently-free loopback port, never 8080.

    Binding port 0 and reading back the assignment is inherently
    time-of-check-to-time-of-use racy, so callers should spawn immediately. The
    alternative — a fixed port — is not racy, it is *reliably wrong*: it
    collides with the llama-server already on 8080 on mauler.
    """
    for _ in range(16):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port != LLAMA_CPP_DEFAULT_PORT:
            return port
    raise ReaderRuntimeError("could not find a free loopback port")  # pragma: no cover


@dataclass(frozen=True)
class ServerHandle:
    """What a started llama-server is, from a caller's point of view."""

    base_url: str
    port: int
    pid: int
    alias: str


class LlamaServer:
    """A supervised `llama-server` child on loopback.

    One instance owns at most one child. `start()` is idempotent (a second call
    returns the same handle), and so is `stop()`.
    """

    def __init__(self, exe: Path | str, model_path: Path | str, mmproj_path: Path | str, *,
                 alias: str, gpu_layers: int = _ALL_LAYERS, ctx_size: int = 8192,
                 host: str = "127.0.0.1", port: int | None = None,
                 state_dir: Path | str | None = None,
                 extra_args: list[str] | tuple[str, ...] = (),
                 env: dict[str, str] | None = None):
        self.exe = Path(exe)
        self.model_path = Path(model_path)
        self.mmproj_path = Path(mmproj_path)
        self.alias = alias
        self.gpu_layers = gpu_layers
        self.ctx_size = ctx_size
        self.host = host
        self._requested_port = port
        self.state_dir = Path(state_dir) if state_dir is not None else self.exe.parent
        self.extra_args = list(extra_args)
        self.env_overrides = dict(env or {})
        self.proc: subprocess.Popen | None = None
        self._handle: ServerHandle | None = None
        self._job_handle = None
        self._log_handle = None
        self._lock = threading.RLock()

    # -- construction ------------------------------------------------------

    @classmethod
    def from_build(cls, build: RuntimeBuild, exe: Path | str, model_path: Path | str,
                   mmproj_path: Path | str, **kwargs) -> LlamaServer:
        """Take `-ngl` from the platform entry rather than from a guess — asking
        a CPU-only build to offload layers is at best a silent no-op."""
        kwargs.setdefault("gpu_layers", build.gpu_layers)
        return cls(exe, model_path, mmproj_path, **kwargs)

    # -- properties --------------------------------------------------------

    @property
    def handle(self) -> ServerHandle | None:
        return self._handle

    @property
    def port(self) -> int | None:
        return self._handle.port if self._handle else self._requested_port

    @property
    def base_url(self) -> str | None:
        return self._handle.base_url if self._handle else None

    @property
    def state_path(self) -> Path:
        return self.state_dir / STATE_FILENAME

    @property
    def log_path(self) -> Path:
        return self.state_dir / _LOG_FILENAME

    def is_running(self) -> bool:
        with self._lock:
            return self.proc is not None and self.proc.poll() is None and self._handle is not None

    # -- argv --------------------------------------------------------------

    def build_argv(self, port: int) -> list[str]:
        """The command line, with a reason for every flag that has one.

        Kept separate from `start()` so the flag set is assertable without
        spawning anything.
        """
        return [
            str(self.exe),
            "--model", str(self.model_path),
            # WHY: without --mmproj, llama-server loads the text tower only and
            # every image in a request is SILENTLY dropped — no error, just
            # empty extractions. docs/install.md already warns about this and it
            # is the single easiest way to make the reader look broken.
            "--mmproj", str(self.mmproj_path),
            # WHY: --alias is the id GET /v1/models reports, and
            # vlm_extract._select_model() matches on exactly that id. Get it
            # wrong and the server is up, healthy, and never selected.
            "--alias", self.alias,
            # WHY: one slot. The default is -1 ("auto"), which opens several
            # server slots sharing a single KV pool; the fleet configs record a
            # live session corrupted that way with four default slots. We serve
            # one page at a time, so extra slots buy nothing and risk that.
            "--parallel", "1",
            # WHY: Qwen2.5-VL's chat template is a Jinja template; without the
            # jinja engine llama.cpp falls back to a built-in template that
            # drops the multimodal content parts.
            "--jinja",
            # WHY: /metrics is how a fleet node's baseline gets measured at all;
            # a model with no baseline reads "unknown" to the registry.
            "--metrics",
            # WHY: loopback only. The reader is a client-local process and
            # llama.cpp's /v1 "will happily burn the node's only GPU for any
            # caller that can reach it" — so nothing outside this machine may.
            "--host", self.host,
            # WHY: a port chosen free at spawn time, never 8080 (taken by an
            # unrelated llama-server on mauler, and by dubIS in the container).
            "--port", str(port),
            # WHY: platform-appropriate offload — every layer on Metal/CUDA/
            # Vulkan builds, zero on CPU builds.
            "-ngl", str(self.gpu_layers),
            "--ctx-size", str(self.ctx_size),
            *self.extra_args,
        ]

    # -- lifecycle ---------------------------------------------------------

    def start(self, *, timeout: float = DEFAULT_READY_TIMEOUT, poll_interval: float = 0.5,
              health_probe: Callable[[str], bool] | None = None) -> ServerHandle:
        with self._lock:
            if self._handle is not None and self.proc is not None and self.proc.poll() is None:
                return self._handle

            # Fail before a 30-second model load rather than after it, and fail
            # loudly on the projector in particular: a missing --mmproj target
            # is the difference between "reads the page" and "returns nothing".
            if not self.model_path.is_file():
                raise ReaderRuntimeError(f"model not found: {self.model_path}")
            if not self.mmproj_path.is_file():
                raise ReaderRuntimeError(
                    f"mmproj (vision projector) not found: {self.mmproj_path} — without it "
                    f"llama-server loads text-only and silently ignores every image"
                )
            if not self.exe.exists():
                raise ReaderRuntimeError(f"llama-server not found: {self.exe}")

            self.state_dir.mkdir(parents=True, exist_ok=True)
            # Layer 4: if a previous run of the app was hard-killed, its child
            # may still be sitting on the GPU. Reap it before adding another.
            reap_orphan(self.state_dir)

            port = self._requested_port or free_loopback_port()
            argv = self.build_argv(port)
            env = {**os.environ, **self.env_overrides}

            # A log file, not a PIPE: llama-server is extremely chatty during
            # model load, and a pipe nobody drains fills its buffer and wedges
            # the child mid-startup.
            log = open(self.log_path, "ab", buffering=0)  # noqa: SIM115 - closed in stop()
            try:
                proc = subprocess.Popen(
                    argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    env=env, cwd=str(self.exe.parent), **_spawn_kwargs(),
                )
            except OSError as exc:
                log.close()
                raise ReaderRuntimeError(f"could not spawn {self.exe}: {exc}") from exc
            self._log_handle = log
            self.proc = proc
            self._job_handle = self._attach_to_job(proc.pid)
            self._write_state(pid=proc.pid, port=port)
            _register(self)

        base_url = f"http://{self.host}:{port}"
        probe = health_probe or _health_ok
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                tail = self._log_tail()
                self.stop()
                raise ReaderProcessExitedError(
                    f"llama-server exited with code {rc} before becoming healthy",
                    returncode=rc, log_tail=tail,
                )
            if probe(base_url):
                with self._lock:
                    self._handle = ServerHandle(base_url=base_url, port=port,
                                                pid=proc.pid, alias=self.alias)
                logger.info("llama-server ready on %s (alias %s, pid %d)",
                            base_url, self.alias, proc.pid)
                return self._handle
            time.sleep(poll_interval)

        tail = self._log_tail()
        self.stop()
        raise ReaderStartTimeoutError(
            f"llama-server on {base_url} never answered GET /health within {timeout:g}s",
            timeout=timeout, log_tail=tail,
        )

    def stop(self, *, timeout: float = 10.0) -> None:
        """Terminate and **reap** the child. Idempotent, and safe before start().

        Reaping matters: a terminated-but-unwaited child is a zombie that still
        shows up in `ps`, and on Windows an unclosed process handle keeps the
        object alive.
        """
        with self._lock:
            proc, self.proc = self.proc, None
            self._handle = None
            job, self._job_handle = self._job_handle, None
            log = getattr(self, "_log_handle", None)
            self._log_handle = None
        _unregister(self)

        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    logger.warning("llama-server pid %d ignored terminate; killing", proc.pid)
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:  # pragma: no cover
                        logger.error("llama-server pid %d survived kill", proc.pid)
            else:
                proc.wait()  # already dead: collect it so it is not a zombie

        if log is not None:
            try:
                log.close()
            except OSError:  # pragma: no cover
                pass
        if job is not None:
            _close_handle(job)
        self._clear_state()

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> ServerHandle:
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # -- state file --------------------------------------------------------

    def _write_state(self, *, pid: int, port: int) -> None:
        payload = {
            "pid": pid,
            "port": port,
            "exe": str(self.exe),
            "alias": self.alias,
            "parent_pid": os.getpid(),
            "started_at": time.time(),
        }
        try:
            self.state_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - best effort
            logger.warning("could not write %s: %s", self.state_path, exc)

    def _clear_state(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover
            logger.warning("could not remove %s: %s", self.state_path, exc)

    def _log_tail(self) -> str:
        """The last few KiB of the child's output, for the error message. A
        typed error saying only "timed out" is unactionable; llama-server's own
        last words usually name the problem (bad GGUF, no CUDA device, ...)."""
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - _LOG_TAIL_BYTES))
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

    # -- Windows job object ------------------------------------------------

    def _attach_to_job(self, pid: int):
        """Put the child in a kill-on-close Job Object (Windows only).

        Windows has no PDEATHSIG, and `TerminateProcess` on the parent runs no
        atexit hooks, so this is the only thing standing between a crashed
        app.pyw and an llama-server holding the whole GPU. Returns the job
        handle, which must stay open for as long as the child should live.

        There is a microscopic window between spawn and assignment; closing it
        would need CREATE_SUSPENDED, and `subprocess.Popen` closes the child's
        primary thread handle, so the child could never be resumed.
        """
        if sys.platform != "win32":
            return None
        return _create_kill_on_close_job(pid)  # pragma: no cover - Windows only


# --------------------------------------------------------------------------- #
# Health probe
# --------------------------------------------------------------------------- #

def _health_ok(base_url: str, timeout: float = 2.0) -> bool:
    """True once GET /health answers 200.

    llama.cpp answers 503 `{"status": "loading model"}` for the entire load, so
    "not 200 yet" is the normal state for minutes, not an error.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Orphan prevention, layers 1-4
# --------------------------------------------------------------------------- #
# 1. atexit + a chained SIGTERM/SIGINT handler  -> normal and signalled exits
# 2. Linux PR_SET_PDEATHSIG=SIGKILL             -> survives `kill -9` of the parent
# 3. Windows kill-on-job-close Job Object       -> survives TerminateProcess
# 4. A state file reaped on next start          -> covers macOS, which has neither
#                                                  (2) nor (3); bounds a stranded
#                                                  child to "until the next launch"

_live: set[LlamaServer] = set()
_live_lock = threading.Lock()
_atexit_registered = False
_signals_installed = False
_prior_handlers: dict[int, object] = {}


def _register(server: LlamaServer) -> None:
    global _atexit_registered
    with _live_lock:
        _live.add(server)
        if not _atexit_registered:
            atexit.register(stop_all)
            _atexit_registered = True
    install_signal_handlers()


def _unregister(server: LlamaServer) -> None:
    with _live_lock:
        _live.discard(server)


def stop_all() -> None:
    """Stop every supervised llama-server. Idempotent; safe with none running."""
    with _live_lock:
        servers = list(_live)
    for server in servers:
        try:
            server.stop()
        except Exception as exc:  # pragma: no cover - teardown must not raise
            logger.warning("error stopping llama-server: %s", exc)


def install_signal_handlers() -> None:
    """Install SIGTERM/SIGINT handlers that stop the children, then chain.

    `atexit` does **not** run on SIGTERM, which is exactly how a supervised or
    task-killed parent strands its child. Any pre-existing handler (app.pyw
    installs its own) is called afterwards rather than clobbered.
    """
    global _signals_installed
    with _live_lock:
        if _signals_installed:
            return
        _signals_installed = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            _prior_handlers[sig] = signal.signal(sig, _signal_handler)
        except (ValueError, OSError, AttributeError) as exc:
            # signal.signal only works on the main thread of the main
            # interpreter; a non-main-thread caller still gets layers 1-4.
            logger.debug("could not install handler for signal %s: %s", sig, exc)


def _signal_handler(signum, frame):
    try:
        stop_all()
    finally:
        prior = _prior_handlers.get(signum)
        if callable(prior):
            prior(signum, frame)
        raise SystemExit(128 + int(signum))


def _spawn_kwargs() -> dict:
    """Platform Popen kwargs that make the child die with us."""
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    hook = _pdeathsig_preexec()
    return {"preexec_fn": hook} if hook else {}


def _pdeathsig_preexec():
    """A `preexec_fn` asking the kernel to SIGKILL the child when we die.

    Linux only — macOS/BSD have no equivalent, which is why layer 4 exists.
    Best-effort: a missing or unusual libc must not stop the child starting.
    """
    if not sys.platform.startswith("linux"):
        return None

    def _set_pdeathsig():
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(_PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0)
        except Exception:
            pass  # best effort; layers 1 and 4 still apply

    return _set_pdeathsig


# -- Windows Job Object (ctypes; structs are portable to define, not to call) --

class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _create_kill_on_close_job(pid: int):  # pragma: no cover - Windows only
    try:
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
                job, _JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        handle = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not handle:
            k32.CloseHandle(job)
            return None
        ok = k32.AssignProcessToJobObject(job, handle)
        k32.CloseHandle(handle)
        if not ok:
            k32.CloseHandle(job)
            return None
        return job
    except Exception as exc:
        logger.warning("could not create a kill-on-close job object: %s", exc)
        return None


def _close_handle(handle) -> None:
    if sys.platform != "win32":
        return
    try:  # pragma: no cover - Windows only
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover
        logger.debug("CloseHandle failed: %s", exc)


# -- layer 4: reap a child stranded by a previous, hard-killed run ------------

def reap_orphan(state_dir: Path | str) -> int | None:
    """Kill the llama-server recorded in `<state_dir>/llama-server.json`, if it
    is still alive and still actually llama-server. Returns the reaped pid.

    This is the macOS answer: there is no PDEATHSIG and no Job Object, so a
    `kill -9` of the app leaves the child holding VRAM. The next launch finds
    the state file and cleans up, bounding the leak to one app lifetime.

    **Fails safe.** PIDs are recycled, so a recorded pid is only killed if the
    live process's command line still mentions the recorded executable. If that
    cannot be established, nothing is killed — a stranded llama-server costs
    VRAM; killing the wrong process costs the user's work.
    """
    state_path = Path(state_dir) / STATE_FILENAME
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pid = int(state["pid"])
        exe = str(state["exe"])
    except (OSError, ValueError, TypeError, KeyError):
        return None

    if pid <= 1 or pid == os.getpid():
        _unlink(state_path)
        return None

    cmdline = _process_cmdline(pid)
    if cmdline is None:  # not running (or unknowable): nothing to reap
        _unlink(state_path)
        return None
    if Path(exe).name not in cmdline:
        logger.warning("pid %d is no longer %s (%r); refusing to kill it", pid, exe, cmdline[:120])
        return None

    logger.warning("reaping orphaned llama-server pid %d from a previous run", pid)
    if not _terminate_pid(pid):
        return None
    _unlink(state_path)
    return pid


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _process_cmdline(pid: int) -> str | None:
    """The live process's command line, or None if it is not running / unknown.

    Shelling out to ps/tasklist rather than taking a psutil dependency: this
    runs once per app launch, and `reader_*` is meant to stay dependency-free.
    """
    if sys.platform == "win32":  # pragma: no cover - Windows only
        exe = shutil.which("tasklist")
        if not exe:
            return None
        out = _run([exe, "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
        if not out or "No tasks" in out or str(pid) not in out:
            return None
        return out
    exe = shutil.which("ps")
    if not exe:
        return None  # pragma: no cover
    out = _run([exe, "-p", str(pid), "-o", "command="])
    return out.strip() or None if out is not None else None


def _run(argv: list[str]) -> str | None:
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def _terminate_pid(pid: int) -> bool:
    """SIGTERM, then SIGKILL, a process we do not own a Popen for."""
    if sys.platform == "win32":  # pragma: no cover - Windows only
        return _run([shutil.which("taskkill") or "taskkill", "/PID", str(pid), "/T", "/F"]) is not None
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError as exc:
        logger.warning("could not signal pid %d: %s", pid, exc)
        return False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return True
