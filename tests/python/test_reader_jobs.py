"""Tests for reader_jobs: the install job registry and its phase state machine.

Nothing here touches the network, a GPU, or a real llama-server. `reader_jobs`
reaches everything outside itself through `InstallSeams`, so a fake downloader
that writes 64 bytes and a fake server that reports a base URL exercise the whole
seven-phase machine in milliseconds.

Two things are deliberately *real* rather than faked:

* the pinned tables — `reader_tiers.tier_by_name` and `reader_runtime.build_for`
  supply the actual repo/revision/filenames and the actual asset byte counts, so
  the "overall percentage" assertions are made against the real numbers a real
  install would use, and `plan_runtime_download` is the real planner;
* the threads — the concurrency, single-flight and error-path tests run the
  worker on an actual background thread and poll from other actual threads,
  because a lock bug is exactly what a sequential test cannot see.
"""

from __future__ import annotations

import io
import itertools
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

import reader_install
import reader_jobs
import reader_memory
import reader_runtime
import reader_tiers

# --------------------------------------------------------------------------- #
# Real pinned metadata the fakes are driven from
# --------------------------------------------------------------------------- #

# The 3B tier and the Windows CUDA build. The CUDA build is chosen on purpose:
# it is the only family with `extra_archives` (the separate cudart zip), so the
# runtime-downloader adapter is exercised with two files rather than one.
TIER = reader_tiers.tier_by_name("qwen2.5-vl-3b-q4_k_m")
BUILD = reader_runtime.build_for("win-x64-cuda")

# Byte counts straight out of the pinned tables — 250969968 + 391443627 runtime,
# 1929901056 weights, 1338428128 projector. Written out so a change to any pinned
# asset has to be acknowledged here rather than silently redefining "100%".
RUNTIME_BYTES = 250969968 + 391443627
EXPECTED_TOTAL = RUNTIME_BYTES + 1929901056 + 1338428128

WEIGHTS_URL = reader_jobs.model_file_url(TIER, TIER.weights_file)
MMPROJ_URL = reader_jobs.model_file_url(TIER, TIER.mmproj_file)

STATUS_KEYS = {
    "job_id", "phase", "message", "bytes_done", "bytes_total", "pct",
    "indeterminate", "done", "error", "tier", "endpoint", "install_dir",
    "phase_history", "elapsed_s",
}


def _budget(*, free=20 * 1024 ** 3, unified=False):
    return reader_memory.BudgetInfo(
        total_bytes=24 * 1024 ** 3, free_bytes=free, source="fake-probe", unified=unified,
    )


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeDownload:
    """Stand-in for `reader_install.download_verified`.

    Emits `ticks` monotonic progress callbacks against the real pinned size for
    that URL, then writes a few real bytes so `plan_uninstall` has something to
    measure. `hook` fires before the download so a test can snapshot the job's
    status at an exact point in the install, or block it.
    """

    def __init__(self, *, ticks: int = 4, hook=None, raise_for: dict | None = None,
                 tick_delay: float = 0.0):
        self.ticks = ticks
        self.tick_delay = tick_delay
        self.hook = hook
        self.raise_for = raise_for or {}
        self.calls: list[str] = []
        self.sizes = {
            asset.url: asset.size_bytes for asset in (BUILD.archive, *BUILD.extra_archives)
        }
        self.sizes[WEIGHTS_URL] = TIER.weights_bytes
        self.sizes[MMPROJ_URL] = TIER.mmproj_bytes

    def __call__(self, url, dest, sha256, progress=None, **kwargs):
        self.calls.append(url)
        if self.hook is not None:
            self.hook(url)
        if url in self.raise_for:
            raise self.raise_for[url]
        size = self.sizes[url]
        if progress is not None:
            for i in range(1, self.ticks + 1):
                progress(size * i // self.ticks, size)
                if self.tick_delay:
                    time.sleep(self.tick_delay)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 64)
        return reader_install.DownloadResult(path=str(dest), bytes=size, sha256=sha256, skipped=False)


class FakeEnsureRuntime:
    """Stand-in for `reader_runtime.ensure_runtime` that still drives the real
    download plan through the injected downloader, so the argument-shape adapter
    in `_runtime_downloader` is genuinely exercised."""

    def __init__(self, *, short_circuit: bool = False):
        self.short_circuit = short_circuit
        self.calls = 0

    def __call__(self, build, dest_dir, *, download):
        self.calls += 1
        exe = Path(dest_dir) / build.server_relpath
        if not self.short_circuit:
            for req in reader_runtime.plan_runtime_download(build, dest_dir):
                download(url=req.url, sha256=req.sha256, dest=req.dest, size_bytes=req.size_bytes)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!fake llama-server")
        return exe


_PORTS = itertools.count(51000)


class FakeServer:
    def __init__(self, *, port: int | None = None, fail: BaseException | None = None, events=None):
        self.base_url = f"http://127.0.0.1:{port if port is not None else next(_PORTS)}"
        self.fail = fail
        self.events = events
        self.starts = 0
        self.stops = 0
        self.timeouts: list[float | None] = []

    def start(self, *, timeout=None, **kwargs):
        self.starts += 1
        self.timeouts.append(timeout)
        if self.events is not None:
            self.events.append("start")
        if self.fail is not None:
            raise self.fail
        return object()

    def stop(self, **kwargs):
        self.stops += 1
        if self.events is not None:
            self.events.append("stop")


#: `rows=None` has to mean "the extractor returned None", which is a *failure*
#: mode vlm_extract really does produce — so the default needs its own sentinel.
_DEFAULT = object()

_DEFAULT_ROWS = [{"part": "C0402C104K5RACTU"}, {"part": "RC0603FR-0710KL"}]


class Harness:
    """Builds an `InstallSeams` whose every leg is a recording fake."""

    def __init__(self, *, budget=None, tier=TIER, platform_key="win-x64-cuda",
                 rows=_DEFAULT, inline=True, download=None, ensure_runtime=None,
                 server=None, extract_error=None):
        self.budget = budget if budget is not None else _budget()
        self.tier = tier
        self.platform_key = platform_key
        self.rows = _DEFAULT_ROWS if rows is _DEFAULT else rows
        self.extract_error = extract_error
        self.download = download or FakeDownload()
        self.ensure_runtime = ensure_runtime or FakeEnsureRuntime()
        self.servers: list[FakeServer] = []
        self._server_template = server
        self.extract_calls: list[dict] = []
        self.platform_kwargs: list[dict] = []
        self.factory_kwargs: list[dict] = []
        self.threads: list[threading.Thread] = []
        self.spawn_calls = 0
        self.inline = inline
        self.probe = (b"\x89PNG\r\n\x1a\n-synthetic-page", 700, 900)

    # -- seam implementations ---------------------------------------------

    def _detect_platform_key(self, **kwargs):
        self.platform_kwargs.append(kwargs)
        return self.platform_key

    def _server_factory(self, **kwargs):
        self.factory_kwargs.append(kwargs)
        server = self._server_template or FakeServer()
        self._server_template = None
        self.servers.append(server)
        return server

    def _extract(self, image, template, page_w, page_h, endpoint=None, token=None):
        self.extract_calls.append({
            "image": image, "template": template, "page_w": page_w, "page_h": page_h,
            "endpoint": endpoint, "token": token,
        })
        if self.extract_error is not None:
            raise self.extract_error
        return self.rows

    def _spawn(self, fn):
        self.spawn_calls += 1
        if self.inline:
            fn()
            return None
        thread = threading.Thread(target=fn, name="test-reader-install", daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread

    @property
    def seams(self) -> reader_jobs.InstallSeams:
        return reader_jobs.InstallSeams(
            detect_budget=lambda: self.budget,
            choose_tier=lambda budget: self.tier,
            detect_platform_key=self._detect_platform_key,
            build_for=reader_runtime.build_for,
            plan_runtime_download=reader_runtime.plan_runtime_download,
            ensure_runtime=self.ensure_runtime,
            download_verified=self.download,
            server_factory=self._server_factory,
            extract_line_items=self._extract,
            probe_page=lambda: self.probe,
            spawn=self._spawn,
            start_timeout=7.5,
        )

    # -- driving ----------------------------------------------------------

    def start(self, data_dir) -> str:
        return reader_jobs.start_install(data_dir, seams=self.seams)

    def run(self, data_dir) -> dict:
        """Start and (inline mode) return the terminal status."""
        job_id = self.start(data_dir)
        return wait_done(job_id)

    def join(self, timeout: float = 10.0) -> None:
        for thread in self.threads:
            thread.join(timeout)


def wait_done(job_id: str, timeout: float = 15.0) -> dict:
    """Poll like the frontend does, until the job reports it is finished."""
    deadline = time.monotonic() + timeout
    status = reader_jobs.get_status(job_id)
    while not status["done"]:
        if time.monotonic() > deadline:
            raise AssertionError(f"install job never reached a terminal state: {status}")
        time.sleep(0.005)
        status = reader_jobs.get_status(job_id)
    return status


# --------------------------------------------------------------------------- #
# Happy path and phase ordering
# --------------------------------------------------------------------------- #

class TestPhaseOrder:
    def test_phases_advance_detect_to_done_in_order(self, tmp_path):
        harness = Harness()
        status = harness.run(tmp_path)

        assert status["phase_history"] == list(reader_jobs.PHASE_ORDER)
        assert status["phase"] == "done"
        assert status["done"] is True
        assert status["error"] is None
        assert status["tier"] == TIER.name
        assert status["endpoint"] == harness.servers[0].base_url
        assert status["install_dir"] == str(tmp_path / "reader")

    def test_terminal_status_is_coherent_for_an_occasional_poller(self, tmp_path):
        """A caller that polls once, at the end, must see a self-consistent state:
        100%, every byte accounted for, no error, nothing 'indeterminate'."""
        harness = Harness(inline=False, download=FakeDownload(ticks=20))
        job_id = harness.start(tmp_path)
        status = wait_done(job_id)

        assert status["phase"] == "done"
        assert status["bytes_total"] == EXPECTED_TOTAL
        assert status["bytes_done"] == EXPECTED_TOTAL
        assert status["pct"] == 100.0
        assert status["indeterminate"] is False
        assert status["error"] is None

    def test_phases_cannot_move_backwards(self, tmp_path):
        job = reader_jobs.InstallJob("j1", tmp_path, Harness().seams)
        job._advance("weights", "weights")
        with pytest.raises(ValueError, match="only move forward"):
            job._advance("runtime", "back to runtime")

    def test_a_terminal_job_cannot_be_advanced(self, tmp_path):
        job = reader_jobs.InstallJob("j2", tmp_path, Harness().seams)
        job._finish("done")
        with pytest.raises(ValueError, match="already done"):
            job._advance("verify", "verify")

    def test_status_dict_is_the_documented_frontend_contract(self, tmp_path):
        status = Harness().run(tmp_path)
        assert set(status) == STATUS_KEYS

    def test_unknown_job_ids_get_the_same_shape(self):
        status = reader_jobs.get_status("no-such-job")
        assert set(status) == STATUS_KEYS


# --------------------------------------------------------------------------- #
# Byte accounting / percentage
# --------------------------------------------------------------------------- #

class TestByteAccounting:
    def test_total_is_known_before_the_first_weights_byte(self, tmp_path):
        """The bar must span the whole install, so runtime + weights + projector
        has to be totalled in `detect` — not discovered one file at a time."""
        seen: list[dict] = []

        def hook(url):
            if url == WEIGHTS_URL:
                # `active_job_id` is how the hook finds the job it is inside:
                # the registry entry exists before the worker is spawned.
                seen.append(reader_jobs.get_status(reader_jobs.active_job_id(tmp_path)))

        harness = Harness(download=FakeDownload(hook=hook))
        harness.start(tmp_path)

        assert len(seen) == 1
        at_weights = seen[0]
        assert at_weights["phase"] == "weights"
        assert at_weights["bytes_total"] == EXPECTED_TOTAL
        # The runtime is already paid for, and no weights byte has arrived yet.
        assert at_weights["bytes_done"] == RUNTIME_BYTES
        assert at_weights["pct"] == pytest.approx(RUNTIME_BYTES * 100.0 / EXPECTED_TOTAL)

    def test_detect_has_no_percentage_at_all(self, tmp_path):
        """`bytes_total` is unknown during the probe, and an unknown total must
        report `pct is None` rather than a 0.0 that renders as 'stuck'."""
        gate = threading.Event()
        entered = threading.Event()

        def blocking_choose(budget):
            entered.set()
            assert gate.wait(10), "gate never released"
            return TIER

        harness = Harness(inline=False)
        job_id = reader_jobs.start_install(
            tmp_path, seams=replace(harness.seams, choose_tier=blocking_choose),
        )
        assert entered.wait(10)
        during_detect = reader_jobs.get_status(job_id)
        assert during_detect["phase"] == "detect"
        assert during_detect["bytes_total"] is None
        assert during_detect["pct"] is None
        assert during_detect["indeterminate"] is True
        assert during_detect["done"] is False

        gate.set()
        assert wait_done(job_id)["phase"] == "done"
        harness.join()

    def test_progress_never_regresses_and_never_exceeds_the_total(self, tmp_path):
        harness = Harness(inline=False, download=FakeDownload(ticks=25))
        job_id = harness.start(tmp_path)

        last = -1
        while True:
            status = reader_jobs.get_status(job_id)
            total = status["bytes_total"]
            assert status["bytes_done"] >= last
            last = status["bytes_done"]
            if total is not None:
                assert status["bytes_done"] <= total
                assert 0.0 <= status["pct"] <= 100.0
            if status["done"]:
                break
        assert last == EXPECTED_TOTAL

    def test_an_already_unpacked_runtime_still_reaches_100_percent(self, tmp_path):
        """`ensure_runtime` short-circuits when the runtime is already on disk, so
        no download callback ever fires for it. Per-phase settling is what keeps a
        resumed install from finishing at 84%."""
        harness = Harness(ensure_runtime=FakeEnsureRuntime(short_circuit=True))
        status = harness.run(tmp_path)

        assert harness.download.calls == [WEIGHTS_URL, MMPROJ_URL]
        assert status["bytes_total"] == EXPECTED_TOTAL
        assert status["bytes_done"] == EXPECTED_TOTAL
        assert status["pct"] == 100.0


# --------------------------------------------------------------------------- #
# Detection, tiering, platform
# --------------------------------------------------------------------------- #

class TestDetect:
    def test_no_tier_fails_the_job_before_a_single_byte(self, tmp_path):
        harness = Harness(tier=None)
        status = harness.run(tmp_path)

        assert status["phase"] == "error"
        assert status["done"] is True
        assert "no reader model fits" in status["error"]
        assert "5 GiB free" in status["error"]
        assert harness.download.calls == []
        assert harness.servers == []
        assert status["tier"] == ""

    def test_discrete_vram_asks_for_a_gpu_build(self, tmp_path):
        harness = Harness(budget=_budget(unified=False))
        harness.run(tmp_path)
        assert harness.platform_kwargs == [{"has_gpu": True}]

    def test_unified_or_system_ram_asks_for_a_cpu_build(self, tmp_path):
        """`unified=True` is Apple unified memory or plain system RAM — i.e. no
        discrete GPU was found, so a CUDA archive would be the wrong download."""
        harness = Harness(budget=_budget(unified=True))
        harness.run(tmp_path)
        assert harness.platform_kwargs == [{"has_gpu": False}]

    def test_unsupported_platform_error_is_preserved(self, tmp_path):
        harness = Harness(platform_key="plan9-risc-v")
        status = harness.run(tmp_path)
        assert status["phase"] == "error"
        assert "plan9-risc-v" in status["error"]
        assert harness.download.calls == []


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #

class TestDownloads:
    def test_fetches_runtime_archives_then_weights_then_projector(self, tmp_path):
        harness = Harness()
        harness.run(tmp_path)
        assert harness.download.calls == [
            BUILD.archive.url,
            BUILD.extra_archives[0].url,
            WEIGHTS_URL,
            MMPROJ_URL,
        ]

    def test_model_urls_pin_the_revision_not_main(self):
        assert WEIGHTS_URL == (
            f"https://huggingface.co/{TIER.repo}/resolve/{TIER.revision}/{TIER.weights_file}"
        )
        assert "/main/" not in WEIGHTS_URL
        assert TIER.revision in MMPROJ_URL

    def test_files_land_under_the_managed_models_dir(self, tmp_path):
        Harness().run(tmp_path)
        assert (tmp_path / "reader" / "models" / TIER.weights_file).is_file()
        assert (tmp_path / "reader" / "models" / TIER.mmproj_file).is_file()

    def test_a_checksum_mismatch_lands_in_error_with_its_message(self, tmp_path):
        boom = reader_install.ChecksumMismatchError(
            "sha256 mismatch for the weights: expected aaa, got bbb", url=WEIGHTS_URL,
        )
        harness = Harness(download=FakeDownload(raise_for={WEIGHTS_URL: boom}))
        status = harness.run(tmp_path)

        assert status["phase"] == "error"
        assert status["error"] == "sha256 mismatch for the weights: expected aaa, got bbb"
        assert "weights" in status["message"]
        assert MMPROJ_URL not in harness.download.calls
        assert harness.servers == []


# --------------------------------------------------------------------------- #
# Start
# --------------------------------------------------------------------------- #

class TestStart:
    def test_server_is_built_from_the_downloaded_files_and_the_tier(self, tmp_path):
        harness = Harness()
        harness.run(tmp_path)

        kwargs = harness.factory_kwargs[0]
        assert kwargs["tier"] is TIER
        assert kwargs["build"] is BUILD
        assert Path(kwargs["model_path"]).name == TIER.weights_file
        assert Path(kwargs["mmproj_path"]).name == TIER.mmproj_file
        assert Path(kwargs["state_dir"]) == tmp_path / "reader" / "runtime"
        assert harness.servers[0].timeouts == [7.5]

    def test_a_start_failure_lands_in_error(self, tmp_path):
        harness = Harness(server=FakeServer(fail=reader_runtime.ReaderStartTimeoutError(
            "llama-server never answered GET /health within 600s", timeout=600.0,
        )))
        status = harness.run(tmp_path)

        assert status["phase"] == "error"
        assert "never answered GET /health" in status["error"]
        assert status["endpoint"] == ""
        assert harness.extract_calls == []

    def test_reinstalling_stops_the_previously_started_server(self, tmp_path):
        """Two llama-servers on one GPU is how a 24 GiB card ends up with 4 GiB
        free, so a second successful install must retire the first one's child."""
        first = Harness()
        first.run(tmp_path)
        second = Harness()
        second.run(tmp_path)

        assert first.servers[0].stops == 1
        assert second.servers[0].stops == 0
        assert reader_jobs.running_endpoint(tmp_path) == second.servers[0].base_url


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #

class TestVerify:
    def test_verify_extracts_from_the_just_started_server(self, tmp_path):
        harness = Harness()
        harness.run(tmp_path)

        assert len(harness.extract_calls) == 1
        call = harness.extract_calls[0]
        assert call["endpoint"] == harness.servers[0].base_url
        assert call["image"] == harness.probe[0]
        assert (call["page_w"], call["page_h"]) == (700, 900)
        assert call["template"] == "generic"

    @pytest.mark.parametrize("empty", [None, [], ()])
    def test_a_reader_that_reads_nothing_is_a_failed_install(self, tmp_path, empty):
        harness = Harness(rows=empty)
        status = harness.run(tmp_path)

        assert status["phase"] == "error"
        assert status["done"] is True
        assert "read nothing from the test page" in status["error"]
        # The projector is the likely culprit and the error has to say so: a
        # text-only llama-server answers every image request with no error.
        assert "vision projector" in status["error"]

    def test_a_failed_verify_does_not_leave_a_server_running(self, tmp_path):
        harness = Harness(rows=[])
        harness.run(tmp_path)

        assert harness.servers[0].stops == 1
        assert reader_jobs.running_endpoint(tmp_path) is None

    def test_an_exception_from_the_extractor_is_preserved(self, tmp_path):
        harness = Harness(extract_error=RuntimeError("connection reset by peer"))
        status = harness.run(tmp_path)

        assert status["phase"] == "error"
        assert status["error"] == "connection reset by peer"
        assert "verify" in status["message"]
        assert harness.servers[0].stops == 1

    def test_a_successful_install_keeps_the_server_registered(self, tmp_path):
        harness = Harness()
        harness.run(tmp_path)
        assert reader_jobs.running_endpoint(tmp_path) == harness.servers[0].base_url
        assert harness.servers[0].stops == 0


# --------------------------------------------------------------------------- #
# Errors in every phase
# --------------------------------------------------------------------------- #

class TestErrorsInEveryPhase:
    @pytest.mark.parametrize("phase", list(reader_jobs.PHASE_ORDER[:-1]))
    def test_every_phase_can_fail_without_hanging(self, tmp_path, phase):
        """One `except BaseException` covers the worker, so no phase can leave a
        job un-terminal with a dead thread behind it — which is the one failure a
        polling frontend cannot recover from."""
        boom = RuntimeError(f"exploded in {phase}")
        harness = Harness(inline=False)
        overrides = {}
        if phase == "detect":
            overrides["detect_budget"] = _raise(boom)
        elif phase == "runtime":
            overrides["ensure_runtime"] = _raise(boom)
        elif phase == "weights":
            harness.download = FakeDownload(raise_for={WEIGHTS_URL: boom})
        elif phase == "projector":
            harness.download = FakeDownload(raise_for={MMPROJ_URL: boom})
        elif phase == "start":
            harness._server_template = FakeServer(fail=boom)
        else:  # verify
            harness.extract_error = boom

        job_id = reader_jobs.start_install(tmp_path, seams=replace(harness.seams, **overrides))
        status = wait_done(job_id)

        assert status["phase"] == "error"
        assert status["done"] is True
        assert status["error"] == f"exploded in {phase}"
        assert phase in status["message"]
        assert status["phase_history"][-1] == "error"
        assert status["phase_history"][-2] == phase
        harness.join()

    def test_an_exception_with_no_message_still_names_something(self, tmp_path):
        harness = Harness()
        harness.extract_error = ValueError()
        status = harness.run(tmp_path)
        assert status["error"] == "ValueError"

    def test_a_spawn_failure_fails_the_job_instead_of_wedging_it(self, tmp_path):
        """If the worker never runs, the job must still be terminal — otherwise
        single-flight would refuse every future install for this directory."""
        harness = Harness()
        seams = replace(harness.seams, spawn=_raise(RuntimeError("cannot start thread")))

        job_id = reader_jobs.start_install(tmp_path, seams=seams)
        status = reader_jobs.get_status(job_id)
        assert status["phase"] == "error"
        assert status["error"] == "cannot start thread"

        # ...and the next click is not blocked by the corpse.
        assert reader_jobs.active_job_id(tmp_path) is None
        assert Harness().start(tmp_path) != job_id


def _raise(exc):
    def boom(*args, **kwargs):
        raise exc
    return boom


# --------------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------------- #

class TestConcurrency:
    def test_status_is_safe_to_poll_while_the_worker_mutates_it(self, tmp_path):
        """Real threads, on purpose: the risk is a *torn* snapshot — a `done`
        phase beside a stale message, or a `pct` computed from one file's
        `bytes_done` against another file's total. Only a lock prevents that, and
        only concurrent polling can catch its absence."""
        # tick_delay stretches the install to ~400ms so the pollers below get a
        # real window to race the worker in, rather than a 3ms one.
        harness = Harness(inline=False, download=FakeDownload(ticks=50, tick_delay=0.002))
        job_id = harness.start(tmp_path)

        errors: list[BaseException] = []
        polls = [0, 0, 0, 0]
        stop = threading.Event()

        def poller(slot: int):
            try:
                last_index = -1
                while not stop.is_set():
                    status = reader_jobs.get_status(job_id)
                    polls[slot] += 1
                    assert set(status) == STATUS_KEYS
                    phase = status["phase"]
                    if phase != reader_jobs.ERROR_PHASE:
                        index = reader_jobs.PHASE_ORDER.index(phase)
                        assert index >= last_index, f"phase went backwards: {status}"
                        last_index = index
                    total = status["bytes_total"]
                    if total is None:
                        assert status["pct"] is None
                    else:
                        assert status["bytes_done"] <= total
                        assert 0.0 <= status["pct"] <= 100.0
                    if status["done"]:
                        assert status["phase"] in reader_jobs.TERMINAL_PHASES
                        # Each of these is a *tear* detector: the worker writes
                        # phase, then history, then message/error under one lock,
                        # so a reader without that lock can catch a `done` phase
                        # still carrying the previous phase's message.
                        assert (status["error"] is None) == (status["phase"] == "done")
                        if status["phase"] == "done":
                            assert "installed and verified" in status["message"], status
                            assert status["bytes_done"] == status["bytes_total"]
                            assert status["pct"] == 100.0
                            assert status["phase_history"][-1] == "done"
            except BaseException as exc:  # noqa: BLE001 - reported through `errors`
                errors.append(exc)

        threads = [threading.Thread(target=poller, args=(i,), daemon=True) for i in range(4)]
        for thread in threads:
            thread.start()
        final = wait_done(job_id)
        stop.set()
        for thread in threads:
            thread.join(5)

        assert not errors, errors
        assert all(count > 10 for count in polls), polls
        assert final["phase"] == "done"
        harness.join()

    def test_status_snapshot_is_taken_under_the_job_lock(self, tmp_path):
        """The poller test above races the worker, but CPython's GIL makes those
        individual attribute reads effectively atomic, so it cannot *prove* the
        snapshot is atomic as a whole — it would pass against a lock-free
        `status()` that happens not to tear. This asserts the property directly
        and observably: with the job lock held, `status()` must block, because a
        snapshot assembled from fourteen unsynchronised reads is exactly how a
        poller ends up rendering a `done` phase beside the previous phase's
        message."""
        job = reader_jobs.InstallJob("lock-probe", tmp_path, Harness().seams)
        returned = threading.Event()

        def snapshot():
            job.status()
            returned.set()

        with job._lock:
            thread = threading.Thread(target=snapshot, daemon=True)
            thread.start()
            assert not returned.wait(0.25), (
                "status() returned while the job lock was held — it is not taking an atomic snapshot"
            )
        assert returned.wait(5)
        thread.join(5)

    def test_second_start_while_in_flight_returns_the_same_job_id(self, tmp_path):
        """The Install button is a click target on a panel that can be reopened.
        A second start must attach to the running install, not race it for the
        same `.part` files and then put a second server on the GPU."""
        gate = threading.Event()
        reached = threading.Event()

        def hook(url):
            if url == WEIGHTS_URL:
                reached.set()
                assert gate.wait(10), "gate never released"

        harness = Harness(inline=False, download=FakeDownload(hook=hook))
        first = harness.start(tmp_path)
        assert reached.wait(10)

        second_harness = Harness(inline=False)
        second = second_harness.start(tmp_path)
        third = reader_jobs.start_install(tmp_path, seams=second_harness.seams)

        assert second == first
        assert third == first
        # Nothing else was scheduled and nothing else downloaded.
        assert second_harness.spawn_calls == 0
        assert second_harness.download.calls == []
        assert second_harness.servers == []

        gate.set()
        status = wait_done(first)
        assert status["phase"] == "done"
        assert harness.download.calls.count(WEIGHTS_URL) == 1
        harness.join()

    def test_a_finished_install_does_not_block_the_next_one(self, tmp_path):
        harness = Harness()
        first = harness.start(tmp_path)
        assert reader_jobs.get_status(first)["done"] is True
        assert reader_jobs.active_job_id(tmp_path) is None

        second = Harness().start(tmp_path)
        assert second != first
        assert reader_jobs.get_status(second)["phase"] == "done"

    def test_single_flight_is_per_install_directory(self, tmp_path):
        """Two different data dirs are two different install targets, so they get
        two jobs — the interlock is about the files and the GPU, not a global
        mutex on the module."""
        gate = threading.Event()
        reached = threading.Event()

        def hook(url):
            if url == WEIGHTS_URL:
                reached.set()
                assert gate.wait(10)

        one = tmp_path / "one"
        two = tmp_path / "two"
        one.mkdir()
        two.mkdir()

        blocked = Harness(inline=False, download=FakeDownload(hook=hook))
        first = blocked.start(one)
        assert reached.wait(10)
        second = Harness().start(two)

        assert second != first
        gate.set()
        assert wait_done(first)["phase"] == "done"
        blocked.join()


# --------------------------------------------------------------------------- #
# Uninstall
# --------------------------------------------------------------------------- #

class TestUninstall:
    def test_stops_the_server_before_deleting_anything(self, tmp_path):
        """`reader_install.uninstall.__doc__` defers process-stopping to its
        caller for a concrete reason: on Windows a live llama-server's mmap of the
        GGUF makes the unlink fail, and on POSIX the inode outlives the unlink so
        the reclaimed byte count is a lie. Order is the assertion."""
        events: list[str] = []
        harness = Harness(server=FakeServer(events=events))
        harness.run(tmp_path)
        events.clear()

        def remove(data_dir):
            events.append("remove")
            return 3_910_742_779

        def reap(state_dir):
            events.append("reap")
            assert Path(state_dir) == tmp_path / "reader" / "runtime"
            return None

        result = reader_jobs.uninstall_reader(tmp_path, remove=remove, reap=reap)

        assert events == ["stop", "reap", "remove"]
        assert result["server_stopped"] is True
        assert result["bytes_reclaimed"] == 3_910_742_779
        assert result["path"] == str(tmp_path / "reader")
        assert result["existed"] is True
        assert result["file_count"] > 0

    def test_really_deletes_the_managed_directory(self, tmp_path):
        harness = Harness()
        harness.run(tmp_path)
        assert (tmp_path / "reader").is_dir()

        result = reader_jobs.uninstall_reader(tmp_path)

        assert not (tmp_path / "reader").exists()
        assert result["bytes_reclaimed"] > 0
        assert reader_jobs.running_endpoint(tmp_path) is None
        assert harness.servers[0].stops == 1

    def test_is_idempotent_with_nothing_installed(self, tmp_path):
        result = reader_jobs.uninstall_reader(tmp_path)
        assert result == {
            "path": str(tmp_path / "reader"),
            "existed": False,
            "bytes_reclaimed": 0,
            "file_count": 0,
            "server_stopped": False,
            "reaped_pid": None,
        }
        assert reader_jobs.uninstall_reader(tmp_path)["bytes_reclaimed"] == 0

    def test_a_failing_stop_does_not_block_the_delete(self, tmp_path):
        class StubbornServer(FakeServer):
            def stop(self, **kwargs):
                super().stop(**kwargs)
                raise OSError("access denied")

        harness = Harness(server=StubbornServer())
        harness.run(tmp_path)

        result = reader_jobs.uninstall_reader(tmp_path)
        assert result["server_stopped"] is True
        assert not (tmp_path / "reader").exists()

    def test_a_failing_reap_does_not_block_the_delete(self, tmp_path):
        Harness().run(tmp_path)
        result = reader_jobs.uninstall_reader(tmp_path, reap=_raise(OSError("no /proc")))
        assert not (tmp_path / "reader").exists()
        assert result["reaped_pid"] is None

    def test_never_accepts_a_path_to_delete(self, tmp_path):
        """The signature takes `data_dir` and derives `<data_dir>/reader` itself,
        so there is no argument through which a caller could aim it elsewhere."""
        import inspect
        params = inspect.signature(reader_jobs.uninstall_reader).parameters
        assert list(params) == ["data_dir", "remove", "reap"]

    def test_an_empty_data_dir_is_refused(self):
        with pytest.raises(reader_install.UnsafeUninstallTargetError):
            reader_jobs.uninstall_reader("")
        with pytest.raises(reader_install.UnsafeUninstallTargetError):
            reader_jobs.start_install("")

    def test_plan_names_the_directory_the_bytes_and_the_running_server(self, tmp_path):
        harness = Harness()
        harness.run(tmp_path)

        plan = reader_jobs.plan_uninstall_reader(tmp_path)
        assert plan["path"] == str(tmp_path / "reader")
        assert plan["exists"] is True
        assert plan["bytes_total"] > 0
        assert plan["server_running"] is True
        assert sorted(plan["entries"]) == ["models", "runtime"]

        reader_jobs.uninstall_reader(tmp_path)
        gone = reader_jobs.plan_uninstall_reader(tmp_path)
        assert gone["exists"] is False
        assert gone["bytes_total"] == 0
        assert gone["server_running"] is False


# --------------------------------------------------------------------------- #
# The real seam defaults
# --------------------------------------------------------------------------- #

class TestRealDefaults:
    def test_defaults_point_at_the_real_modules(self):
        """The fakes above only prove the machine; this proves production is
        wired to the actual implementations rather than to a stub."""
        import vlm_extract
        seams = reader_jobs.InstallSeams()
        assert seams.detect_budget is reader_memory.detect_budget
        assert seams.choose_tier is reader_tiers.choose_tier
        assert seams.build_for is reader_runtime.build_for
        assert seams.ensure_runtime is reader_runtime.ensure_runtime
        assert seams.download_verified is reader_install.download_verified
        assert seams.extract_line_items is vlm_extract.extract_line_items
        assert seams.start_timeout == reader_runtime.DEFAULT_READY_TIMEOUT

    def test_the_synthetic_probe_page_is_a_readable_image(self):
        """`verify`'s default page has to be a real, decodable image with legible
        text — a 1x1 pixel would fail every install."""
        from PIL import Image
        image_bytes, width, height = reader_jobs._synthetic_probe_page()
        assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        with Image.open(io.BytesIO(image_bytes)) as img:
            assert img.size == (width, height)
            # Not a blank page: the text has to have actually been drawn.
            assert img.convert("L").getextrema()[0] < 128
        assert width >= 600 and height >= 400

    def test_default_server_factory_wires_the_alias_to_the_tier(self, tmp_path):
        """`--alias` is the id `GET /v1/models` reports and the id
        `vlm_extract._select_model()` matches on. A mismatch leaves the server up,
        healthy, and never selected."""
        server = reader_jobs._default_server_factory(
            build=BUILD, tier=TIER, exe=tmp_path / "llama-server",
            model_path=tmp_path / "w.gguf", mmproj_path=tmp_path / "m.gguf",
            state_dir=tmp_path / "state",
        )
        assert isinstance(server, reader_runtime.LlamaServer)
        assert server.alias == TIER.name
        assert server.ctx_size == TIER.ctx_size
        assert server.gpu_layers == BUILD.gpu_layers
        assert server.state_dir == tmp_path / "state"


class TestErrorTaxonomy:
    def test_job_errors_are_dubis_errors(self):
        from dubis_errors import DubISError
        assert issubclass(reader_jobs.ReaderJobError, DubISError)
        assert issubclass(reader_jobs.NoReaderTierError, reader_jobs.ReaderJobError)
        assert issubclass(reader_jobs.ReaderVerifyError, reader_jobs.ReaderJobError)

    def test_phase_tuples_agree(self):
        assert reader_jobs.PHASE_ORDER[0] == "detect"
        assert reader_jobs.PHASE_ORDER[-1] == "done"
        assert reader_jobs.ERROR_PHASE not in reader_jobs.PHASE_ORDER
        assert reader_jobs.TERMINAL_PHASES == {"done", "error"}
        assert reader_jobs.INDETERMINATE_PHASES <= set(reader_jobs.PHASE_ORDER)
