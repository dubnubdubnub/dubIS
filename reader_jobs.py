"""Install-job registry and status state machine for the local picture/PDF reader.

This module is the only place that knows the *order* of a reader install. It owns
nothing else: memory probing lives in `reader_memory`, the pinned model table in
`reader_tiers`, verified downloads and the managed directory in `reader_install`,
the llama.cpp release table and process supervision in `reader_runtime`, and the
extraction call in `vlm_extract`. Every one of those is reached through an
injected seam (see `InstallSeams`), which is what lets the whole seven-phase
machine be tested without a network, a GPU, or a real llama-server.

    detect -> runtime -> weights -> projector -> start -> verify -> done
                                                                 \\-> error

**Why a poll-able registry and not a callback or a stream.** The local reader has
to install on the *client* machine, so it is carried by the pywebview client
shell rather than `/v1` (in remote-backend mode there is no local `/v1` at all,
and the remote one is the wrong machine). pywebview cannot stream, so
`start_install()` returns a job id immediately and the frontend polls
`get_status(job_id)` on a timer. That dict is therefore the **entire** API
surface the frontend sees, and it is documented as a contract in
`InstallJob.status`.

Three properties this module exists to guarantee:

1. **One install at a time, per managed directory.** A second `start_install`
   while one is in flight returns the *same* job id rather than starting a second
   multi-GiB download onto the same disk (and, at `start`, a second llama-server
   onto the same GPU). See `start_install`.
2. **A true overall percentage.** The byte total for runtime + weights +
   projector is known at the end of `detect`, before the first model byte is
   fetched, so the bar advances once across the whole install instead of
   restarting per file. See `_settle` for why the accounting uses *planned*
   sizes rather than observed ones.
3. **No terminal state is ever missed.** Every phase runs inside one
   `except BaseException` that lands the job in `phase == "error"` with the
   message preserved and `done == True`. A poller that only checks every few
   seconds — or once, at the end — sees a coherent terminal state, never a job
   stuck mid-phase with a dead worker thread.

`uninstall_reader` is here rather than in `reader_install` for one reason:
`reader_install.uninstall.__doc__` explicitly defers process-stopping to its
caller, because on Windows a live llama-server holding an mmap of a GGUF makes
the unlink fail outright, and on POSIX the inode survives until the process
exits — so the "reclaimed bytes" it reports would be a lie. This module is the
only thing that knows which server is running, so it is the caller that has to
stop it first.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import reader_install
import reader_memory
import reader_runtime
import reader_tiers
import vlm_extract
from dubis_errors import DubISError

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #

#: The install phases, in the only order they may occur. `_advance` refuses to
#: move backwards through this tuple, so a future edit that reorders the worker
#: fails a test instead of shipping a progress bar that jumps around.
PHASE_ORDER: tuple[str, ...] = (
    "detect",     # memory probe + tier choice + platform choice; totals the bytes
    "runtime",    # llama.cpp release archive(s) for this platform, sha256-verified
    "weights",    # the GGUF, sha256-verified, atomic rename from .part
    "projector",  # the mmproj GGUF — NOT optional; see reader_tiers.Tier.mmproj_file
    "start",      # spawn llama-server on a free loopback port, poll /health
    "verify",     # one real extract_line_items against a synthetic page
    "done",
)

#: Terminal, and deliberately *not* in `PHASE_ORDER`: it can be reached from any
#: phase, so ordering it against the others would be meaningless.
ERROR_PHASE = "error"

TERMINAL_PHASES = frozenset({"done", ERROR_PHASE})

#: Phases that do no byte-counted work. `pct` is still whatever the byte
#: accounting says (100.0 during `start`/`verify`, because the downloads really
#: are finished by then), so the frontend needs this flag to know the bar should
#: read as busy-not-progressing rather than "done but not done".
INDETERMINATE_PHASES = frozenset({"detect", "start", "verify"})

_PHASE_INDEX = {phase: i for i, phase in enumerate(PHASE_ORDER)}

#: Finished jobs kept for polling after the fact. The frontend polls the job it
#: just started, so this only has to outlive one panel's timer — but a status
#: dict is small and an id that 404s mid-poll is a worse failure than a few KiB.
_MAX_FINISHED_JOBS = 8

_HF_BASE = "https://huggingface.co"

_GIB = 1024 ** 3
_MIB = 1024 ** 2


class ReaderJobError(DubISError):
    """Base for install-job failures raised by this module (not re-raised ones)."""


class NoReaderTierError(ReaderJobError):
    """No model tier fits this machine's *free* memory, so nothing was downloaded.

    Not a bug and not a crash: below the smallest tier's floor the honest answer
    is "stay off, keep the existing tesseract/flat path". Raised in `detect`, so
    it costs zero bytes.
    """


class ReaderVerifyError(ReaderJobError):
    """The reader installed and started, but could not read a test page.

    This is a *failed* install, deliberately. Every ingredient of a silent
    reader — a missing `--mmproj` (llama-server then loads text-only and drops
    every image with no error at all), a model that answers but never localises,
    an alias mismatch that makes `vlm_extract._select_model` pick nothing — looks
    exactly like a healthy install right up until the operator's first real
    packing list comes back empty. So the install itself pays for one real
    extraction, and a reader that cannot read is reported as broken now rather
    than discovered later.
    """


# --------------------------------------------------------------------------- #
# Seams
# --------------------------------------------------------------------------- #

def _default_server_factory(*, build, tier, exe, model_path, mmproj_path, state_dir):
    """The real llama-server, wired from the platform build and the chosen tier.

    `alias` MUST be the tier name: it is the id `GET /v1/models` reports and the
    id `vlm_extract._select_model()` matches on, so a mismatch leaves the server
    up, healthy, and never selected. `state_dir` is passed explicitly (rather
    than defaulting to the executable's directory) so `reap_orphan` and
    `uninstall_reader` can find the pid file at a path derived from `data_dir`.
    """
    return reader_runtime.LlamaServer.from_build(
        build, exe, model_path, mmproj_path,
        alias=tier.name, ctx_size=tier.ctx_size, state_dir=state_dir,
    )


def _spawn_thread(fn: Callable[[], None]) -> threading.Thread:
    """Default spawn seam: a daemon thread, so a job in flight never keeps the
    app alive at exit (the child process is handled by `reader_runtime`'s own
    Job Object / PDEATHSIG / state-file layers, not by this thread's lifetime).

    Tests override this with `lambda fn: fn()` to run a whole install inline and
    assert the phase sequence deterministically.
    """
    thread = threading.Thread(target=fn, name="reader-install", daemon=True)
    thread.start()
    return thread


def _synthetic_probe_page() -> tuple[bytes, int, int]:
    """A small synthetic packing-list page for `verify`, as `(png, width, height)`.

    Synthetic on purpose: the real scans this feature exists to read are the
    operator's purchase documents and are PII, so they are never committed and
    never shipped inside a verification path (see the plan's "do not commit
    `data/signal-*.jpeg`" note). Two rows are enough — `verify` asserts that
    *something* was read, not what.
    """
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1000, 620
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = _probe_font(ImageFont)

    lines = [
        "PACKING LIST     ORDER 90210     1 of 1",
        "",
        "PART NUMBER            DESCRIPTION                        QTY",
        "C0402C104K5RACTU       CAP CER 0.1UF 25V X7R 0402         250",
        "RC0603FR-0710KL        RES 10K OHM 1% 1/10W 0603          100",
        "SN74LVC1G08DBVR        IC GATE AND 1CH 2-INP SOT-23-5      25",
    ]
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 46

    from io import BytesIO
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), width, height


def _probe_font(image_font_module):
    """The largest legible built-in font available.

    Pillow's bitmap default is ~11px, which is a coin flip for a vision encoder
    on a 1000px page. Pillow >= 10.1 can scale it; older Pillow cannot, and a
    TrueType path would have to be guessed per platform, so the unscaled default
    is the fallback rather than a hard requirement.
    """
    try:
        return image_font_module.load_default(size=34)
    except TypeError:  # Pillow < 10.1: load_default() takes no size
        return image_font_module.load_default()


@dataclass(frozen=True)
class InstallSeams:
    """Everything the install worker reaches outside itself, injectable.

    Defaults are the real implementations, so production calls
    `start_install(data_dir)` and gets the real thing. Tests replace individual
    fields (`dataclasses.replace`, or a fresh instance) and never touch the
    network, the GPU, or a real llama-server.
    """

    detect_budget: Callable[[], Any] = reader_memory.detect_budget
    choose_tier: Callable[[Any], Any] = reader_tiers.choose_tier
    detect_platform_key: Callable[..., str] = reader_runtime.detect_platform_key
    build_for: Callable[[str], Any] = reader_runtime.build_for
    plan_runtime_download: Callable[..., list] = reader_runtime.plan_runtime_download
    ensure_runtime: Callable[..., Path] = reader_runtime.ensure_runtime
    download_verified: Callable[..., Any] = reader_install.download_verified
    server_factory: Callable[..., Any] = _default_server_factory
    extract_line_items: Callable[..., Any] = vlm_extract.extract_line_items
    probe_page: Callable[[], tuple[bytes, int, int]] = _synthetic_probe_page
    spawn: Callable[[Callable[[], None]], Any] = _spawn_thread
    #: llama-server's first load of a multi-GiB GGUF is minutes on a cold page
    #: cache, which is why the runtime's own default is 10 minutes, not seconds.
    start_timeout: float = reader_runtime.DEFAULT_READY_TIMEOUT


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #

class InstallJob:
    """One reader install, its status, and the worker that advances it.

    Every mutation of the status fields happens under `self._lock`, and
    `status()` snapshots them all under that same lock — so a frontend polling
    from another thread can never observe a half-updated status (a `done` phase
    with a stale message, or a `pct` computed from one file's `bytes_done`
    against another file's total).
    """

    def __init__(self, job_id: str, data_dir: str | Path, seams: InstallSeams):
        self.job_id = job_id
        self.data_dir = str(data_dir)
        self.seams = seams
        self.install_dir = str(reader_install.managed_dir(data_dir))

        self._lock = threading.Lock()
        self._phase = PHASE_ORDER[0]
        self._message = "Checking this machine's memory..."
        self._history: list[str] = [PHASE_ORDER[0]]
        self._error: str | None = None
        self._failed_phase = ""
        self._tier_name = ""
        self._endpoint = ""
        self._started_at = time.monotonic()
        self._finished_at: float | None = None

        # Byte accounting. `_bytes_total` stays None until `detect` has the tier
        # and the platform build, which is what makes `pct` honestly
        # indeterminate rather than a fake 0% during the probe.
        self._bytes_total: int | None = None
        self._completed_bytes = 0   # planned size of every finished file
        self._current_bytes = 0     # progress within the file being fetched
        self._runtime_bytes = 0
        self._weights_bytes = 0
        self._mmproj_bytes = 0

        self.server: Any = None

    # -- status ------------------------------------------------------------

    def status(self) -> dict:
        """The frontend contract. Keys, and what each one promises:

        * `job_id`      — pass it back to `get_status`.
        * `phase`       — one of `PHASE_ORDER` or `"error"`.
        * `message`     — human, already localised to the phase; renderable as-is.
        * `bytes_done`  — bytes fetched so far across the whole install.
        * `bytes_total` — total for runtime + weights + projector, or `None`
                          while still unknown (during `detect`).
        * `pct`         — 0..100 float, or `None` when `bytes_total` is `None`.
                          Never a fake 0.0 for "unknown" (see
                          `reader_install.progress_pct`).
        * `indeterminate` — True while the phase does no byte-counted work
                          (`detect`, `start`, `verify`). Byte-derived `pct` reads
                          100 during `start`/`verify` because the downloads
                          genuinely are finished; this flag is how the bar knows
                          to read as busy rather than complete.
        * `done`        — True in `done` *and* in `error`. It means "stop
                          polling", not "succeeded"; check `error` for that.
        * `error`       — the failure message, else `None`.
        * `tier`        — chosen tier name once known, else `""`.
        * `endpoint`    — the running reader's base URL once `start` succeeded.
        * `install_dir` — `<data_dir>/reader`, for the uninstall confirm.
        * `phase_history` — every phase entered, in order. Diagnostic: it lets a
                          caller that polled only occasionally still see the
                          path taken.
        * `elapsed_s`   — wall time, rounded.
        """
        with self._lock:
            bytes_done = self._completed_bytes + self._current_bytes
            total = self._bytes_total
            end = self._finished_at if self._finished_at is not None else time.monotonic()
            return {
                "job_id": self.job_id,
                "phase": self._phase,
                "message": self._message,
                "bytes_done": bytes_done,
                "bytes_total": total,
                "pct": reader_install.progress_pct(bytes_done, total),
                "indeterminate": self._phase in INDETERMINATE_PHASES,
                "done": self._phase in TERMINAL_PHASES,
                "error": self._error,
                "tier": self._tier_name,
                "endpoint": self._endpoint,
                "install_dir": self.install_dir,
                "phase_history": list(self._history),
                "elapsed_s": round(end - self._started_at, 3),
            }

    def is_done(self) -> bool:
        with self._lock:
            return self._phase in TERMINAL_PHASES

    @property
    def finished_at(self) -> float:
        with self._lock:
            return self._finished_at if self._finished_at is not None else float("inf")

    # -- state transitions -------------------------------------------------

    def _advance(self, phase: str, message: str) -> None:
        """Move to `phase`, refusing to go backwards.

        The guard is not defensive coding for its own sake: a progress UI that
        moves backwards is read as a bug in the *install*, and the cheapest way
        to prevent it is to make an out-of-order worker impossible to write.
        """
        if phase not in _PHASE_INDEX:
            raise ValueError(f"{phase!r} is not an install phase")
        with self._lock:
            if self._phase in TERMINAL_PHASES:
                raise ValueError(f"job {self.job_id} is already {self._phase}; cannot advance to {phase}")
            if _PHASE_INDEX[phase] < _PHASE_INDEX[self._phase]:
                raise ValueError(f"install phases only move forward: {self._phase} -> {phase}")
            if phase != self._phase:
                self._phase = phase
                self._history.append(phase)
            self._message = message
        logger.info("reader install %s: %s (%s)", self.job_id, message, phase)

    def _set_message(self, message: str) -> None:
        with self._lock:
            self._message = message

    def _fail(self, exc: BaseException) -> None:
        detail = str(exc).strip() or exc.__class__.__name__
        with self._lock:
            self._failed_phase = self._phase
            self._phase = ERROR_PHASE
            self._history.append(ERROR_PHASE)
            self._error = detail
            self._message = f"Reader install failed during {self._failed_phase}: {detail}"
            self._finished_at = time.monotonic()
        logger.error("reader install %s failed during %s: %s", self.job_id, self._failed_phase, detail,
                     exc_info=isinstance(exc, Exception))

    def _finish(self, message: str) -> None:
        with self._lock:
            self._phase = "done"
            self._history.append("done")
            self._message = message
            self._finished_at = time.monotonic()
        logger.info("reader install %s complete: %s", self.job_id, message)

    # -- byte accounting ---------------------------------------------------

    def _file_progress(self, planned: int) -> Callable[[int, int | None], None]:
        """A `reader_install.ProgressCallback` scoped to one file.

        Clamped to `planned` so the overall bar cannot exceed 100% if a server
        sends more than its `Content-Length` claimed.
        """
        def progress(bytes_done: int, bytes_total: int | None) -> None:  # noqa: ARG001 - protocol shape
            with self._lock:
                self._current_bytes = min(bytes_done, planned) if planned > 0 else bytes_done
        return progress

    def _settle(self, cumulative_planned: int) -> None:
        """Snap the accounting to the *planned* total through the finished phase.

        Deliberately planned-not-observed. Two cases otherwise break the bar:
        an already-present, already-verified file is `skipped` and emits its size
        once (fine), but an already-unpacked runtime makes `ensure_runtime`
        short-circuit with **no** download calls at all — so a resumed install
        would sit at 40% forever and finish below 100%. Snapping per phase makes
        the percentage a function of *progress through the install*, not of how
        much of it happened to need fetching.
        """
        with self._lock:
            self._completed_bytes = max(self._completed_bytes, cumulative_planned)
            self._current_bytes = 0

    # -- the worker --------------------------------------------------------

    def run(self) -> None:
        """Run every phase to a terminal state. Never raises."""
        server = None
        try:
            tier, build = self._phase_detect()
            exe = self._phase_runtime(build)
            weights = self._phase_weights(tier)
            mmproj = self._phase_projector(tier)
            server = self._phase_start(build, tier, exe, weights, mmproj)
            count = self._phase_verify(server, tier)
            self._finish(
                f"Picture/PDF reader installed and verified — {tier.name}, "
                f"{count} line item{'' if count == 1 else 's'} read from the test page."
            )
        except BaseException as exc:  # noqa: BLE001 - a lost exception here hangs the poller forever
            self._fail(exc)
            # A reader that started but failed `verify` must not be left holding
            # the GPU: it is not usable (that is what the failure means), so it
            # is stopped *and* de-registered. De-registering matters as much as
            # stopping: a stale entry would make `running_endpoint` advertise a
            # dead server, and would let `uninstall_reader` report that it
            # stopped a reader it did not.
            if server is not None:
                if _take_server(_install_key(self.data_dir)) is None:
                    logger.debug("reader install %s: server was not registered yet", self.job_id)
                _safe_stop(server)
                with self._lock:
                    self._endpoint = ""

    # -- phases ------------------------------------------------------------

    def _phase_detect(self):
        self._advance("detect", "Checking this machine's memory...")
        budget = self.seams.detect_budget()
        tier = self.seams.choose_tier(budget)
        if tier is None:
            raise NoReaderTierError(
                f"no reader model fits this machine: {_describe_budget(budget)}. The smallest tier needs "
                f"{reader_tiers.MIN_TIER_FREE_BYTES / _GIB:.0f} GiB free. Image/PDF import keeps using the "
                f"existing text-recognition path."
            )

        # `unified=False` is a discrete-VRAM probe (nvidia-smi, the Windows
        # registry, rocm-smi); `unified=True` is Apple unified memory or plain
        # system RAM, i.e. no usable discrete GPU was found. That distinction is
        # exactly what `detect_platform_key` wants, and `reader_runtime`
        # deliberately does not duplicate the probe.
        has_gpu = budget is not None and not getattr(budget, "unified", True)
        platform_key = self.seams.detect_platform_key(has_gpu=has_gpu)
        build = self.seams.build_for(platform_key)

        runtime_requests = self.seams.plan_runtime_download(build, reader_install.runtime_dir(self.data_dir))
        runtime_bytes = sum(int(req.size_bytes) for req in runtime_requests)

        with self._lock:
            self._tier_name = tier.name
            self._runtime_bytes = runtime_bytes
            self._weights_bytes = int(tier.weights_bytes)
            self._mmproj_bytes = int(tier.mmproj_bytes)
            # Known *before* the first byte is fetched, which is the whole point:
            # one bar across the whole install rather than three that each
            # restart at zero.
            self._bytes_total = runtime_bytes + self._weights_bytes + self._mmproj_bytes
            total = self._bytes_total
        self._set_message(
            f"Selected {tier.name} for {platform_key} — {_fmt_bytes(total)} to download."
        )
        return tier, build

    def _phase_runtime(self, build) -> Path:
        with self._lock:
            planned = self._runtime_bytes
        self._advance("runtime", f"Downloading the llama.cpp {build.release_tag} runtime for "
                                 f"{build.key} ({_fmt_bytes(planned)})...")
        exe = self.seams.ensure_runtime(
            build, reader_install.runtime_dir(self.data_dir), download=self._runtime_downloader(),
        )
        self._settle(planned)
        return Path(exe)

    def _phase_weights(self, tier) -> Path:
        with self._lock:
            cumulative = self._runtime_bytes + self._weights_bytes
        self._advance("weights", f"Downloading {tier.name} weights ({_fmt_bytes(tier.weights_bytes)})...")
        dest = reader_install.models_dir(self.data_dir) / tier.weights_file
        self.seams.download_verified(
            model_file_url(tier, tier.weights_file), dest, tier.weights_sha256,
            self._file_progress(int(tier.weights_bytes)),
        )
        self._settle(cumulative)
        return dest

    def _phase_projector(self, tier) -> Path:
        with self._lock:
            cumulative = self._runtime_bytes + self._weights_bytes + self._mmproj_bytes
        # Its own phase, not an appendix to `weights`, because it is the file
        # whose absence is invisible: without --mmproj llama-server loads the
        # text tower only and silently ignores every image.
        self._advance("projector", f"Downloading the vision projector "
                                   f"({_fmt_bytes(tier.mmproj_bytes)})...")
        dest = reader_install.models_dir(self.data_dir) / tier.mmproj_file
        self.seams.download_verified(
            model_file_url(tier, tier.mmproj_file), dest, tier.mmproj_sha256,
            self._file_progress(int(tier.mmproj_bytes)),
        )
        self._settle(cumulative)
        return dest

    def _phase_start(self, build, tier, exe: Path, weights: Path, mmproj: Path):
        self._advance("start", "Starting the reader (loading the model can take a minute)...")
        key = _install_key(self.data_dir)
        # A previous successful install for this directory left a server running.
        # Two llama-servers on one GPU is how a 24 GiB card ends up with 4 GiB
        # free, so the old one goes before the new one starts.
        previous = _take_server(key)
        if previous is not None:
            _safe_stop(previous)

        server = self.seams.server_factory(
            build=build, tier=tier, exe=exe, model_path=weights, mmproj_path=mmproj,
            state_dir=reader_install.runtime_dir(self.data_dir),
        )
        self.server = server
        server.start(timeout=self.seams.start_timeout)
        base_url = getattr(server, "base_url", "") or ""
        with self._lock:
            self._endpoint = base_url
        _put_server(key, server)
        self._set_message(f"Reader running on {base_url}.")
        return server

    def _phase_verify(self, server, tier) -> int:
        self._advance("verify", "Checking the reader can actually read a page...")
        image, page_w, page_h = self.seams.probe_page()
        rows = self.seams.extract_line_items(
            image, "generic", page_w, page_h, endpoint=getattr(server, "base_url", None),
        )
        if not rows:
            raise ReaderVerifyError(
                f"{tier.name} started but read nothing from the test page. The most likely cause is a "
                f"vision projector that did not load — without it llama-server answers as if the image "
                f"were absent, and reports no error. The install is being reported as failed rather than "
                f"leaving a reader that cannot read."
            )
        return len(rows)

    # -- the runtime's Downloader seam ------------------------------------

    def _runtime_downloader(self):
        """Adapt `reader_install.download_verified` to `reader_runtime.Downloader`.

        `ensure_runtime` wants `(url, sha256, dest, size_bytes) -> Path`;
        `download_verified` wants `(url, dest, sha256, progress)` and returns a
        `DownloadResult`. This is that argument swap plus the progress wiring,
        and it is the only place the two shapes meet.
        """
        def download(*, url: str, sha256: str, dest: Path, size_bytes: int = 0) -> Path:
            result = self.seams.download_verified(url, dest, sha256, self._file_progress(int(size_bytes)))
            with self._lock:
                self._completed_bytes += int(size_bytes)
                self._current_bytes = 0
            path = getattr(result, "path", None)
            return Path(path) if path is not None else Path(dest)
        return download


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #

def model_file_url(tier, filename: str) -> str:
    """The HuggingFace download URL for one file of `tier`, pinned to its revision.

    `resolve/<revision>` and not `resolve/main`: the sha256 in `reader_tiers` was
    verified against that exact commit, so a repo that force-pushes or re-quantises
    under the same filename fails the checksum instead of quietly handing the
    operator different weights.
    """
    return f"{_HF_BASE}/{tier.repo}/resolve/{tier.revision}/{filename}"


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_LOCK = threading.RLock()
_JOBS: dict[str, InstallJob] = {}
#: managed-dir path -> job id. Keyed by the *managed directory* rather than
#: globally so single-flight means "one install per install target", which is
#: what the disk and the GPU actually care about.
_ACTIVE: dict[str, str] = {}
#: managed-dir path -> the running LlamaServer from a successful install. The
#: only record of what `uninstall_reader` has to stop before deleting files.
_SERVERS: dict[str, Any] = {}


def _install_key(data_dir: str | Path) -> str:
    """Canonical key for one install target: the validated managed directory.

    Derived through `reader_install.managed_dir`, so two spellings of the same
    data dir cannot start two installs against the same files — and a data dir
    that fails the safety checks fails here, before any work is scheduled.
    """
    return str(reader_install.managed_dir(data_dir))


def start_install(data_dir: str | Path, *, seams: InstallSeams | None = None) -> str:
    """Begin (or join) an install of the local reader under `<data_dir>/reader`.

    Returns a job id immediately; the work runs on a background thread and is
    observed with `get_status(job_id)`.

    **Single-flight.** If an install for this directory is already in flight, the
    id of *that* job is returned and nothing new is started. This is not a
    convenience: the frontend's Install button is a click target on a panel that
    can be reopened, and two workers would race to write the same `.part` files,
    then race to put two llama-servers on one GPU. Returning the in-flight id
    means a double click, or a second panel, simply attaches to the running
    install.
    """
    seams = seams or InstallSeams()
    key = _install_key(data_dir)
    with _LOCK:
        existing_id = _ACTIVE.get(key)
        if existing_id is not None:
            existing = _JOBS.get(existing_id)
            if existing is not None and not existing.is_done():
                logger.info("reader install already in flight for %s; returning job %s", key, existing_id)
                return existing_id
        job = InstallJob(uuid.uuid4().hex, data_dir, seams)
        _JOBS[job.job_id] = job
        _ACTIVE[key] = job.job_id
        _prune_locked()

    # Spawned outside the lock: a synchronous `spawn` seam (tests, or a future
    # blocking caller) would otherwise hold `_LOCK` for the entire install and
    # deadlock every `get_status` poll.
    try:
        seams.spawn(job.run)
    except BaseException as exc:  # noqa: BLE001 - a job nobody runs would wedge single-flight forever
        job._fail(exc)
    return job.job_id


def get_status(job_id: str) -> dict:
    """The status dict for `job_id` — see `InstallJob.status` for the contract.

    An unknown id gets a terminal error dict rather than an exception. The
    registry is in-memory, so an id from before an app restart is genuinely gone;
    answering "done, with an error" stops the frontend's poll timer, where
    raising would leave it retrying forever against a job that cannot come back.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        return job.status()
    return {
        "job_id": job_id,
        "phase": ERROR_PHASE,
        "message": "That reader install is no longer being tracked (the app may have restarted).",
        "bytes_done": 0,
        "bytes_total": None,
        "pct": None,
        "indeterminate": False,
        "done": True,
        "error": f"unknown install job {job_id!r}",
        "tier": "",
        "endpoint": "",
        "install_dir": "",
        "phase_history": [],
        "elapsed_s": 0.0,
    }


def active_job_id(data_dir: str | Path) -> str | None:
    """The in-flight job for this directory, if any. `None` once it is terminal."""
    key = _install_key(data_dir)
    with _LOCK:
        job_id = _ACTIVE.get(key)
        job = _JOBS.get(job_id) if job_id else None
    return job_id if job is not None and not job.is_done() else None


def running_endpoint(data_dir: str | Path) -> str | None:
    """Base URL of the reader started for this directory, or `None`.

    Read-only view of what `uninstall_reader` would have to stop.
    """
    with _LOCK:
        server = _SERVERS.get(_install_key(data_dir))
    if server is None:
        return None
    return getattr(server, "base_url", None)


def _prune_locked() -> None:
    """Drop the oldest finished jobs. Caller holds `_LOCK`."""
    finished = [j for j in _JOBS.values() if j.is_done()]
    if len(finished) <= _MAX_FINISHED_JOBS:
        return
    finished.sort(key=lambda j: j.finished_at)
    for job in finished[: len(finished) - _MAX_FINISHED_JOBS]:
        _JOBS.pop(job.job_id, None)


def _put_server(key: str, server: Any) -> None:
    with _LOCK:
        _SERVERS[key] = server


def _take_server(key: str) -> Any:
    with _LOCK:
        return _SERVERS.pop(key, None)


def _safe_stop(server: Any) -> None:
    """Stop a server, never letting the stop itself become the reported failure.

    Used on error paths, where the exception already in flight is the one the
    operator needs to see.
    """
    try:
        server.stop()
    except Exception:  # noqa: BLE001 - best-effort cleanup on an error path
        logger.warning("reader: could not stop llama-server cleanly", exc_info=True)


# --------------------------------------------------------------------------- #
# Uninstall
# --------------------------------------------------------------------------- #

def uninstall_reader(data_dir: str | Path, *,
                     remove: Callable[[Any], int] = reader_install.uninstall,
                     reap: Callable[[Any], int | None] = reader_runtime.reap_orphan) -> dict:
    """Stop the local reader, then delete `<data_dir>/reader`.

    Order is the entire reason this function exists.
    `reader_install.uninstall.__doc__` explicitly defers process-stopping to its
    caller, and this module is the only thing that knows which server is running.
    Deleting first would: fail outright on Windows, where llama-server's mmap of
    the GGUF holds an open handle; and on POSIX succeed while reporting bytes
    that do not come back until the child exits, because the inode outlives the
    unlink. So: stop the server we started, reap a server left by a previous app
    run (its pid file is inside the directory about to be deleted, so it is now
    or never), and only then remove the files.

    Local-only, per the design: `reader_mode` `remote` keeps working, and
    `local`/`auto` fall back rather than erroring. Idempotent — uninstalling
    nothing succeeds and reports 0 bytes.

    An install in flight is *not* cancelled here (the frontend disables Uninstall
    while one runs); the `plan` in the returned dict is measured before deletion,
    so the confirm dialog and the result describe the same directory.
    """
    key = _install_key(data_dir)
    plan = reader_install.plan_uninstall(data_dir)

    server = _take_server(key)
    stopped = False
    if server is not None:
        _safe_stop(server)
        stopped = True

    reaped_pid = None
    try:
        reaped_pid = reap(reader_install.runtime_dir(data_dir))
    except Exception:  # noqa: BLE001 - reaping is best-effort; the delete is the point
        logger.warning("reader: orphan reap before uninstall failed", exc_info=True)

    reclaimed = remove(data_dir)
    with _LOCK:
        _ACTIVE.pop(key, None)
    logger.info("reader: uninstalled %s (%d bytes, server_stopped=%s)", plan.path, reclaimed, stopped)
    return {
        "path": plan.path,
        "existed": plan.exists,
        "bytes_reclaimed": reclaimed,
        "file_count": plan.file_count,
        "server_stopped": stopped,
        "reaped_pid": reaped_pid,
    }


def plan_uninstall_reader(data_dir: str | Path) -> dict:
    """What `uninstall_reader` would remove — for the confirm dialog.

    Same derived path, so the dialog can never name a directory other than the
    one that gets deleted. `server_running` is included because stopping the
    reader is a visible side effect the operator should be told about.
    """
    plan = reader_install.plan_uninstall(data_dir).as_dict()
    plan["server_running"] = running_endpoint(data_dir) is not None
    return plan


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def _fmt_bytes(value: int | None) -> str:
    """Human size for a status message. The frontend has its own formatter
    (`js/reader/reader-progress-logic.js`); this one is only for the message
    strings composed here."""
    if value is None:
        return "unknown size"
    if value >= _GIB:
        return f"{value / _GIB:.1f} GiB"
    if value >= _MIB:
        return f"{value / _MIB:.0f} MiB"
    return f"{value} bytes"


def _describe_budget(budget) -> str:
    """Say what the memory probe actually found, so "no tier fits" is arguable
    rather than mysterious."""
    if budget is None:
        return "no memory probe answered"
    free = getattr(budget, "free_bytes", None)
    source = getattr(budget, "source", "unknown")
    if free is None:
        total = getattr(budget, "total_bytes", None)
        return f"{source} reported {_fmt_bytes(total)} total but no free-memory figure"
    return f"{source} reported {_fmt_bytes(free)} free"
