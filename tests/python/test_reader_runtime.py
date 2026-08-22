"""Tests for reader_runtime: the pinned llama.cpp acquisition table and the
llama-server process supervisor.

Two rules shape this file:

1. **Nothing is downloaded.** The acquisition half is pure metadata, and
   ``ensure_runtime`` takes the downloader as an injected callable, so a fake
   that writes a two-file archive exercises the whole path in milliseconds.
2. **No real llama-server.** Supervision is tested against a fake executable —
   a generated Python script that binds the port it was handed and serves
   ``/health`` — so the spawn/health/stop/reap semantics are exercised for real
   (real fork, real port, real SIGTERM, real reap) without a GPU or a 250 MB
   download.
"""
from __future__ import annotations

import io
import json
import os
import signal
import socket
import subprocess
import sys
import tarfile
import textwrap
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

import reader_runtime as rr

# --------------------------------------------------------------------------- #
# Fake llama-server executable
# --------------------------------------------------------------------------- #

# A stand-in for llama-server. It parses only what it needs (--port, and our own
# test-only knobs from the environment), dumps its argv so tests can assert the
# required flags, and serves /health + /v1/models the way llama.cpp does:
# 503 {"status": "loading model"} until ready, then 200 {"status": "ok"}.
_FAKE_SERVER = textwrap.dedent(
    '''
    import json, os, sys, time, signal
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    argv = sys.argv[1:]
    dump = os.environ.get("FAKE_ARGV_FILE")
    if dump:
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump(argv, fh)

    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "die":
        sys.stderr.write("fake llama-server: could not load model\\n")
        sys.stderr.flush()
        sys.exit(3)
    if mode == "ignore_sigterm":
        signal.signal(signal.SIGTERM, lambda *a: None)
    if mode == "idle":
        # Never binds, never answers /health: the ready-timeout case.
        while True:
            time.sleep(0.05)

    port = int(argv[argv.index("--port") + 1])
    host = argv[argv.index("--host") + 1]
    alias = argv[argv.index("--alias") + 1]
    ready_after = float(os.environ.get("FAKE_READY_AFTER", "0"))
    started = time.monotonic()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            ready = (time.monotonic() - started) >= ready_after
            if self.path == "/health":
                if ready:
                    self._send(200, {"status": "ok"})
                else:
                    self._send(503, {"error": {"message": "loading model"}})
            elif self.path == "/v1/models":
                self._send(200, {"data": [{"id": alias}]})
            else:
                self._send(404, {})

    srv = ThreadingHTTPServer((host, port), H)
    sys.stderr.write("fake llama-server listening on %s:%d\\n" % (host, port))
    sys.stderr.flush()
    srv.serve_forever()
    '''
).strip()


@pytest.fixture
def fake_exe(tmp_path: Path) -> Path:
    """The fake server as a directly-executable file *named* `llama-server`.

    A shebang rather than a shell wrapper on purpose: the kernel then runs
    `python .../llama-server <args>`, so the live process's command line still
    contains "llama-server" — which is exactly the identity check
    `reap_orphan` performs before it kills a recorded pid.
    """
    if sys.platform == "win32":  # pragma: no cover - CI runs POSIX
        script = tmp_path / "fake_llama_server.py"
        script.write_text(_FAKE_SERVER, encoding="utf-8")
        exe = tmp_path / "llama-server.bat"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return exe
    exe = tmp_path / "llama-server"
    exe.write_text(f"#!{sys.executable}\n{_FAKE_SERVER}\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def model_files(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "model.gguf"
    mmproj = tmp_path / "mmproj.gguf"
    model.write_bytes(b"GGUF-fake-weights")
    mmproj.write_bytes(b"GGUF-fake-projector")
    return model, mmproj


def _server(fake_exe, model_files, tmp_path, **kw):
    model, mmproj = model_files
    kw.setdefault("state_dir", tmp_path / "state")
    return rr.LlamaServer(fake_exe, model, mmproj, alias="qwen2.5-vl-7b", **kw)


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":  # pragma: no cover - CI runs POSIX
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


# =========================================================================== #
# Half 1 — the acquisition table
# =========================================================================== #


class TestPlatformTable:
    """The table is the install contract: a wrong asset name or hash is a failed
    install on a user's machine *after* a multi-hundred-MB download."""

    REQUIRED_ALIASES = ["darwin-arm64", "win-cuda", "win-cpu", "linux-cuda", "linux-cpu"]

    def test_required_platforms_all_resolve(self):
        for alias in self.REQUIRED_ALIASES:
            build = rr.build_for(alias)
            assert build.release_tag == rr.LLAMACPP_RELEASE_TAG

    def test_every_entry_is_fully_pinned(self):
        assert rr.RUNTIME_BUILDS, "the table must not be empty"
        for key, build in rr.RUNTIME_BUILDS.items():
            assert build.key == key, f"{key}: table key must match entry key"
            # Exact release tag, exact asset name, exact hash — no 'latest'.
            assert build.release_tag == rr.LLAMACPP_RELEASE_TAG
            for asset in (build.archive, *build.extra_archives):
                assert asset.filename, f"{key}: asset needs a filename"
                assert len(asset.sha256) == 64, f"{key}/{asset.filename}: sha256 must be 64 hex chars"
                assert all(c in "0123456789abcdef" for c in asset.sha256), f"{key}: sha256 must be lowercase hex"
                assert asset.size_bytes > 0, f"{key}/{asset.filename}: needs a byte size for progress totals"
                # The URL is derived from the tag + filename, never hand-typed.
                assert asset.url.endswith(f"/{rr.LLAMACPP_RELEASE_TAG}/{asset.filename}")
                assert asset.url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/")
            assert build.server_relpath, f"{key}: needs the llama-server path inside the archive"
            assert build.accelerator in {"metal", "cuda", "vulkan", "cpu"}

    def test_no_two_entries_claim_the_same_asset(self):
        seen: dict[str, str] = {}
        for key, build in rr.RUNTIME_BUILDS.items():
            prev = seen.setdefault(build.archive.filename, key)
            assert prev == key, f"{key} and {prev} both claim {build.archive.filename}"

    def test_asset_name_embeds_the_pinned_tag(self):
        """A stale filename against a bumped tag is the classic way this table
        rots: the URL 404s only at install time."""
        for key, build in rr.RUNTIME_BUILDS.items():
            name = build.archive.filename
            if name.startswith("llama-"):
                assert rr.LLAMACPP_RELEASE_TAG in name, f"{key}: {name} does not carry the pinned tag"

    def test_cpu_builds_offload_nothing_gpu_builds_offload_everything(self):
        for key, build in rr.RUNTIME_BUILDS.items():
            if build.accelerator == "cpu":
                assert build.gpu_layers == 0, f"{key}: a CPU build must not claim GPU layers"
            else:
                assert build.gpu_layers > 0, f"{key}: a GPU build must offload layers"

    def test_windows_cuda_also_pulls_the_cuda_runtime(self):
        """llama.cpp's win-cuda zip ships without the CUDA runtime DLLs; the
        separate cudart zip is mandatory or llama-server.exe won't start."""
        build = rr.build_for("win-cuda")
        assert build.extra_archives, "win-cuda must pull the cudart archive too"
        assert any("cudart" in a.filename for a in build.extra_archives)

    def test_windows_server_is_an_exe_posix_is_not(self):
        for key, build in rr.RUNTIME_BUILDS.items():
            if key.startswith("win-"):
                assert build.server_relpath.endswith("llama-server.exe"), key
            else:
                assert build.server_relpath.endswith("llama-server"), key

    def test_linux_cuda_is_an_honest_alias_not_a_fabricated_asset(self):
        """Upstream publishes NO Linux CUDA archive (verified across every
        release: only ubuntu-{x64,arm64,vulkan,rocm,sycl,openvino,s390x}). The
        alias therefore resolves to the Vulkan build, which does offload to an
        NVIDIA card — and the entry says so rather than inventing a hash."""
        build = rr.build_for("linux-cuda")
        assert build.accelerator == "vulkan"
        assert "cuda" not in build.archive.filename
        assert build.notes, "the substitution must be documented on the entry"

    def test_unknown_platform_raises_a_typed_error(self):
        with pytest.raises(rr.ReaderPlatformUnsupportedError) as exc:
            rr.build_for("solaris-sparc-cuda")
        # Actionable: name the bad key and what is on offer.
        assert "solaris-sparc-cuda" in str(exc.value)
        assert exc.value.platform_key == "solaris-sparc-cuda"
        assert "win-cuda" in exc.value.available

    def test_unsupported_platform_error_is_a_dubis_error(self):
        from dubis_errors import DubISError
        assert issubclass(rr.ReaderPlatformUnsupportedError, DubISError)


class TestPlatformDetection:
    @pytest.mark.parametrize(
        "system,machine,has_cuda,expected_accel",
        [
            ("Darwin", "arm64", False, "metal"),
            ("Darwin", "x86_64", False, "cpu"),
            ("Windows", "AMD64", True, "cuda"),
            ("Windows", "AMD64", False, "cpu"),
            ("Linux", "x86_64", True, "vulkan"),   # no upstream Linux CUDA asset
            ("Linux", "x86_64", False, "cpu"),
            ("Linux", "aarch64", False, "cpu"),
        ],
    )
    def test_detect_picks_the_right_family(self, system, machine, has_cuda, expected_accel):
        key = rr.detect_platform_key(system=system, machine=machine, has_gpu=has_cuda)
        assert rr.build_for(key).accelerator == expected_accel

    def test_detect_on_an_unknown_platform_is_typed_not_a_crash(self):
        with pytest.raises(rr.ReaderPlatformUnsupportedError):
            rr.detect_platform_key(system="AIX", machine="ppc64", has_gpu=False)

    def test_detect_defaults_to_this_host(self):
        """No arguments must not explode on whatever host runs the suite."""
        key = rr.detect_platform_key()
        assert key in rr.RUNTIME_BUILDS


# =========================================================================== #
# Half 1b — ensure_runtime through the injected downloader
# =========================================================================== #


def _tar_gz_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class _FakeDownloader:
    """Stands in for reader_install.py's downloader: same narrow contract
    (url, sha256, dest) -> Path, and it records every call."""

    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.calls: list[dict] = []

    def __call__(self, *, url: str, sha256: str, dest: Path, size_bytes: int = 0) -> Path:
        self.calls.append({"url": url, "sha256": sha256, "dest": Path(dest), "size_bytes": size_bytes})
        name = url.rsplit("/", 1)[-1]
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.payloads[name])
        return Path(dest)


class TestDownloadPlan:
    def test_plan_lists_every_archive_with_its_hash(self, tmp_path):
        build = rr.build_for("win-cuda")
        plan = rr.plan_runtime_download(build, tmp_path)
        assert len(plan) == 1 + len(build.extra_archives)
        by_name = {r.asset.filename: r for r in plan}
        assert build.archive.filename in by_name
        for req in plan:
            assert req.dest.parent == tmp_path / "archives"
            assert req.dest.name == req.asset.filename
            assert req.asset.sha256 == req.sha256

    def test_ensure_runtime_downloads_extracts_and_returns_the_server(self, tmp_path):
        build = rr.build_for("linux-cpu")
        payload = _tar_gz_bytes({
            build.server_relpath: b"#!/bin/sh\nexit 0\n",
            f"{Path(build.server_relpath).parent}/libmtmd.so": b"x",
        })
        dl = _FakeDownloader({build.archive.filename: payload})
        exe = rr.ensure_runtime(build, tmp_path, download=dl)
        assert exe == tmp_path / build.server_relpath
        assert exe.is_file()
        assert len(dl.calls) == 1
        assert dl.calls[0]["sha256"] == build.archive.sha256
        assert dl.calls[0]["url"] == build.archive.url
        if os.name != "nt":
            assert os.access(exe, os.X_OK), "the extracted server must be executable"

    def test_ensure_runtime_is_idempotent_and_skips_a_second_download(self, tmp_path):
        build = rr.build_for("linux-cpu")
        payload = _tar_gz_bytes({build.server_relpath: b"#!/bin/sh\nexit 0\n"})
        dl = _FakeDownloader({build.archive.filename: payload})
        first = rr.ensure_runtime(build, tmp_path, download=dl)
        second = rr.ensure_runtime(build, tmp_path, download=dl)
        assert first == second
        assert len(dl.calls) == 1, "an already-extracted runtime must not re-download"

    def test_ensure_runtime_extracts_extras_next_to_the_server(self, tmp_path):
        """The cudart DLLs are only found if they sit in llama-server.exe's own
        directory, which for the Windows zips is the archive root."""
        build = rr.build_for("win-cuda")
        payloads = {
            build.archive.filename: _zip_bytes({build.server_relpath: b"MZ"}),
            build.extra_archives[0].filename: _zip_bytes({"cudart64_12.dll": b"MZ"}),
        }
        dl = _FakeDownloader(payloads)
        exe = rr.ensure_runtime(build, tmp_path, download=dl)
        assert (exe.parent / "cudart64_12.dll").is_file()
        assert len(dl.calls) == 2

    def test_ensure_runtime_rejects_a_path_traversing_archive(self, tmp_path):
        build = rr.build_for("linux-cpu")
        payload = _tar_gz_bytes({"../escaped": b"pwned", build.server_relpath: b"x"})
        dl = _FakeDownloader({build.archive.filename: payload})
        with pytest.raises(rr.ReaderRuntimeError):
            rr.ensure_runtime(build, tmp_path, download=dl)
        assert not (tmp_path.parent / "escaped").exists()

    def test_ensure_runtime_errors_if_the_archive_lacks_the_server(self, tmp_path):
        build = rr.build_for("linux-cpu")
        payload = _tar_gz_bytes({"llama-xxxx/llama-cli": b"x"})
        dl = _FakeDownloader({build.archive.filename: payload})
        with pytest.raises(rr.ReaderRuntimeError) as exc:
            rr.ensure_runtime(build, tmp_path, download=dl)
        assert build.server_relpath in str(exc.value)

    def test_ensure_runtime_never_touches_the_network_itself(self, tmp_path, monkeypatch):
        """Downloading belongs to reader_install.py; this module only names the
        bytes. Any urlopen from here is a layering bug."""
        def _boom(*a, **k):
            raise AssertionError("reader_runtime must not open URLs itself")
        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        build = rr.build_for("linux-cpu")
        dl = _FakeDownloader({build.archive.filename: _tar_gz_bytes({build.server_relpath: b"x"})})
        rr.ensure_runtime(build, tmp_path, download=dl)


# =========================================================================== #
# Half 2 — supervision
# =========================================================================== #


class TestFreePort:
    def test_returns_a_bindable_loopback_port(self):
        port = rr.free_loopback_port()
        assert 1024 < port < 65536
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))  # nothing else grabbed it

    def test_never_returns_llama_cpp_default_8080(self):
        """8080 is taken by an unrelated llama-server on mauler and by dubIS
        itself inside the container — binding it is a live collision."""
        assert rr.LLAMA_CPP_DEFAULT_PORT == 8080
        for _ in range(50):
            assert rr.free_loopback_port() != 8080

    def test_successive_calls_do_not_collide(self):
        ports = {rr.free_loopback_port() for _ in range(10)}
        assert len(ports) > 1, "an ephemeral-port picker that always returns one port is broken"


class TestArgv:
    def test_required_flags_are_all_present(self, fake_exe, model_files, tmp_path):
        model, mmproj = model_files
        srv = _server(fake_exe, model_files, tmp_path, gpu_layers=999, ctx_size=8192)
        argv = srv.build_argv(port=54321)
        assert argv[0] == str(fake_exe)

        def val(flag):
            return argv[argv.index(flag) + 1]

        # Without --mmproj the model loads text-only and every image is
        # SILENTLY ignored — no error, just empty extractions.
        assert val("--mmproj") == str(mmproj)
        assert val("--model") == str(model)
        # --alias is the id /v1/models reports, which is what
        # vlm_extract._select_model() matches on.
        assert val("--alias") == "qwen2.5-vl-7b"
        # One slot: the fleet configs record a live session corrupted by four
        # default slots sharing a single KV pool.
        assert val("--parallel") == "1"
        assert "--jinja" in argv
        assert "--metrics" in argv
        assert val("--host") == "127.0.0.1"
        assert val("--port") == "54321"
        assert val("-ngl") == "999"
        assert val("--ctx-size") == "8192"

    def test_alias_matches_what_vlm_extract_selects_on(self, fake_exe, model_files, tmp_path):
        import vlm_extract
        srv = _server(fake_exe, model_files, tmp_path)
        argv = srv.build_argv(port=1234)
        alias = argv[argv.index("--alias") + 1]
        assert alias in vlm_extract._PREFERRED_MODELS

    def test_no_hardcoded_8080_anywhere_in_argv(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        assert "8080" not in srv.build_argv(port=49999)

    def test_cpu_build_passes_ngl_zero(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path, gpu_layers=0)
        argv = srv.build_argv(port=1234)
        assert argv[argv.index("-ngl") + 1] == "0"

    def test_from_build_takes_gpu_layers_from_the_platform_entry(self, fake_exe, model_files, tmp_path):
        model, mmproj = model_files
        build = rr.build_for("linux-cpu")
        srv = rr.LlamaServer.from_build(build, fake_exe, model, mmproj, alias="qwen2.5-vl-3b",
                                       state_dir=tmp_path / "s")
        assert srv.gpu_layers == build.gpu_layers == 0
        metal = rr.LlamaServer.from_build(rr.build_for("darwin-arm64"), fake_exe, model, mmproj,
                                         alias="qwen2.5-vl-3b", state_dir=tmp_path / "s2")
        assert metal.gpu_layers > 0

    def test_extra_args_are_appended(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path, extra_args=["--no-warmup"])
        assert srv.build_argv(port=1234)[-1] == "--no-warmup"

    def test_missing_projector_is_refused_before_spawning(self, fake_exe, tmp_path, model_files):
        model, _ = model_files
        srv = rr.LlamaServer(fake_exe, model, tmp_path / "nope.gguf", alias="a",
                             state_dir=tmp_path / "s")
        with pytest.raises(rr.ReaderRuntimeError) as exc:
            srv.start(timeout=5)
        assert "mmproj" in str(exc.value).lower()
        assert srv.is_running() is False


class TestLifecycle:
    def test_start_health_stop(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        handle = srv.start(timeout=30, poll_interval=0.1)
        try:
            assert handle.port == srv.port
            assert handle.base_url == f"http://127.0.0.1:{handle.port}"
            assert handle.pid > 0
            assert srv.is_running()
            with urllib.request.urlopen(handle.base_url + "/health", timeout=5) as resp:
                assert json.load(resp)["status"] == "ok"
            # The alias really is what /v1/models reports.
            with urllib.request.urlopen(handle.base_url + "/v1/models", timeout=5) as resp:
                assert json.load(resp)["data"][0]["id"] == "qwen2.5-vl-7b"
        finally:
            srv.stop()
        assert srv.is_running() is False
        assert _wait_gone(handle.pid)

    def test_port_is_chosen_at_spawn_not_hardcoded(self, fake_exe, model_files, tmp_path):
        a = _server(fake_exe, model_files, tmp_path, state_dir=tmp_path / "a")
        b = _server(fake_exe, model_files, tmp_path, state_dir=tmp_path / "b")
        try:
            pa = a.start(timeout=30, poll_interval=0.1).port
            pb = b.start(timeout=30, poll_interval=0.1).port
            assert pa != pb, "two concurrent readers must not fight over one port"
            assert 8080 not in (pa, pb)
        finally:
            a.stop()
            b.stop()

    def test_explicit_port_is_honoured(self, fake_exe, model_files, tmp_path):
        port = rr.free_loopback_port()
        srv = _server(fake_exe, model_files, tmp_path, port=port)
        try:
            assert srv.start(timeout=30, poll_interval=0.1).port == port
        finally:
            srv.stop()

    def test_the_spawned_process_really_gets_the_required_flags(self, fake_exe, model_files, tmp_path):
        dump = tmp_path / "argv.json"
        srv = _server(fake_exe, model_files, tmp_path, env={"FAKE_ARGV_FILE": str(dump)})
        try:
            srv.start(timeout=30, poll_interval=0.1)
        finally:
            srv.stop()
        argv = json.loads(dump.read_text())
        for flag in ("--mmproj", "--alias", "--parallel", "--jinja", "--metrics", "--host", "--port", "-ngl"):
            assert flag in argv

    def test_polls_until_ready_rather_than_giving_up_on_the_first_503(self, fake_exe, model_files, tmp_path):
        """First load of a multi-GB model onto a card is slow; llama.cpp answers
        503 'loading model' the whole time."""
        srv = _server(fake_exe, model_files, tmp_path, env={"FAKE_READY_AFTER": "1.5"})
        started = time.monotonic()
        try:
            srv.start(timeout=60, poll_interval=0.1)
            assert time.monotonic() - started >= 1.4
            assert srv.is_running()
        finally:
            srv.stop()

    def test_ready_timeout_raises_typed_error_and_leaves_nothing_running(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path, env={"FAKE_MODE": "idle"})
        with pytest.raises(rr.ReaderStartTimeoutError) as exc:
            srv.start(timeout=1.5, poll_interval=0.1)
        assert exc.value.timeout == pytest.approx(1.5)
        assert "health" in str(exc.value).lower()
        assert srv.is_running() is False
        assert srv.handle is None

    def test_default_ready_timeout_is_generous(self):
        assert rr.DEFAULT_READY_TIMEOUT >= 300

    def test_child_dying_during_startup_is_reported_with_its_output(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path, env={"FAKE_MODE": "die"})
        with pytest.raises(rr.ReaderProcessExitedError) as exc:
            srv.start(timeout=30, poll_interval=0.1)
        assert exc.value.returncode == 3
        assert "could not load model" in exc.value.log_tail
        assert srv.is_running() is False

    def test_log_file_captures_stderr_instead_of_a_pipe(self, fake_exe, model_files, tmp_path):
        """llama-server is extremely chatty; a PIPE nobody drains deadlocks it."""
        srv = _server(fake_exe, model_files, tmp_path)
        try:
            srv.start(timeout=30, poll_interval=0.1)
            assert srv.log_path.is_file()
            assert srv.proc.stdout is None and srv.proc.stderr is None
        finally:
            srv.stop()
        assert "fake llama-server listening" in srv.log_path.read_text(errors="replace")

    def test_context_manager_stops_on_exit(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        with srv as handle:
            pid = handle.pid
            assert srv.is_running()
        assert srv.is_running() is False
        assert _wait_gone(pid)

    def test_context_manager_stops_on_exception(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        with pytest.raises(RuntimeError):
            with srv as handle:
                pid = handle.pid
                raise RuntimeError("boom")
        assert _wait_gone(pid)

    def test_start_twice_returns_the_same_handle(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        try:
            first = srv.start(timeout=30, poll_interval=0.1)
            second = srv.start(timeout=30, poll_interval=0.1)
            assert first == second
        finally:
            srv.stop()


class TestStopIdempotencyAndReaping:
    def test_stop_is_idempotent(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        handle = srv.start(timeout=30, poll_interval=0.1)
        srv.stop()
        srv.stop()
        srv.stop()  # and a third time, for good measure
        assert srv.is_running() is False
        assert _wait_gone(handle.pid)

    def test_stop_before_start_is_a_no_op(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        srv.stop()
        srv.stop()
        assert srv.is_running() is False

    def test_stop_actually_reaps_the_child(self, fake_exe, model_files, tmp_path):
        """A terminated-but-unwaited child is a zombie still shown by ps."""
        srv = _server(fake_exe, model_files, tmp_path)
        srv.start(timeout=30, poll_interval=0.1)
        proc = srv.proc
        srv.stop()
        assert proc.returncode is not None, "stop() must wait() the child, not just signal it"

    def test_stop_escalates_to_kill_when_sigterm_is_ignored(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path,
                      env={"FAKE_MODE": "ignore_sigterm"} if sys.platform != "win32" else {})
        handle = srv.start(timeout=30, poll_interval=0.1)
        srv.stop(timeout=1.0)
        assert _wait_gone(handle.pid), "a child ignoring SIGTERM must still be killed"

    def test_stop_after_the_child_already_exited_still_reaps(self, fake_exe, model_files, tmp_path):
        """A child that died on its own is still a zombie until someone wait()s
        it; stop() must do that rather than shrug because poll() is non-None."""
        srv = _server(fake_exe, model_files, tmp_path)
        handle = srv.start(timeout=30, poll_interval=0.1)
        proc = srv.proc
        os.kill(handle.pid, signal.SIGKILL) if sys.platform != "win32" else proc.kill()
        time.sleep(0.3)
        srv.stop()
        assert proc.returncode is not None
        assert srv.is_running() is False

    def test_stop_clears_the_state_file(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        srv.start(timeout=30, poll_interval=0.1)
        assert srv.state_path.is_file()
        srv.stop()
        assert not srv.state_path.exists()


class TestOrphanPrevention:
    """Four layers, because no single mechanism covers all three platforms.
    An orphaned llama-server sits on multiple GiB of VRAM indefinitely."""

    def test_atexit_hook_is_registered_on_first_spawn(self, fake_exe, model_files, tmp_path, monkeypatch):
        registered = []
        monkeypatch.setattr(rr.atexit, "register", lambda fn, *a, **k: registered.append(fn) or fn)
        monkeypatch.setattr(rr, "_atexit_registered", False)
        srv = _server(fake_exe, model_files, tmp_path)
        try:
            srv.start(timeout=30, poll_interval=0.1)
            assert rr.stop_all in registered
        finally:
            srv.stop()

    def test_stop_all_stops_every_live_server(self, fake_exe, model_files, tmp_path):
        a = _server(fake_exe, model_files, tmp_path, state_dir=tmp_path / "a")
        b = _server(fake_exe, model_files, tmp_path, state_dir=tmp_path / "b")
        pids = [a.start(timeout=30, poll_interval=0.1).pid, b.start(timeout=30, poll_interval=0.1).pid]
        rr.stop_all()
        assert not a.is_running() and not b.is_running()
        for pid in pids:
            assert _wait_gone(pid)

    def test_stop_all_is_idempotent_and_safe_with_nothing_running(self):
        rr.stop_all()
        rr.stop_all()

    def test_sigterm_handler_is_chained_not_clobbered(self, monkeypatch):
        """atexit does NOT run on SIGTERM, so a handler is required — but
        app.pyw may already have one, and dropping it would be a regression."""
        seen = []
        installed = {}

        def fake_signal(sig, handler):
            prior = installed.get(sig, signal.SIG_DFL)
            installed[sig] = handler
            return prior

        monkeypatch.setattr(rr.signal, "signal", fake_signal)
        monkeypatch.setattr(rr, "_signals_installed", False)
        installed[signal.SIGTERM] = lambda *a: seen.append("prior")
        rr.install_signal_handlers()
        handler = installed[signal.SIGTERM]
        assert callable(handler)
        with pytest.raises(SystemExit):
            handler(signal.SIGTERM, None)
        assert seen == ["prior"], "the pre-existing handler must still run"

    def test_signal_handlers_install_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(rr.signal, "signal", lambda s, h: calls.append(s) or signal.SIG_DFL)
        monkeypatch.setattr(rr, "_signals_installed", False)
        rr.install_signal_handlers()
        n = len(calls)
        rr.install_signal_handlers()
        assert len(calls) == n

    def test_signal_install_survives_being_called_off_the_main_thread(self, monkeypatch):
        def boom(sig, handler):
            raise ValueError("signal only works in main thread of the main interpreter")
        monkeypatch.setattr(rr.signal, "signal", boom)
        monkeypatch.setattr(rr, "_signals_installed", False)
        rr.install_signal_handlers()  # must not raise

    def test_linux_gets_a_pdeathsig_preexec_hook(self, monkeypatch):
        """PR_SET_PDEATHSIG is the only mechanism that survives `kill -9` of the
        parent: the kernel signals the child."""
        monkeypatch.setattr(rr.sys, "platform", "linux")
        hook = rr._pdeathsig_preexec()
        assert callable(hook)
        recorded = []
        monkeypatch.setattr(rr.ctypes, "CDLL", lambda *a, **k: type(
            "L", (), {"prctl": staticmethod(lambda *args: recorded.append(args) or 0)})())
        hook()
        assert recorded == [(rr._PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0)]

    def test_pdeathsig_hook_never_blocks_the_spawn(self, monkeypatch):
        monkeypatch.setattr(rr.sys, "platform", "linux")
        hook = rr._pdeathsig_preexec()

        def boom(*a, **k):
            raise OSError("no libc for you")
        monkeypatch.setattr(rr.ctypes, "CDLL", boom)
        hook()  # best-effort: a missing libc must not abort the child

    def test_no_pdeathsig_off_linux(self, monkeypatch):
        for plat in ("darwin", "win32"):
            monkeypatch.setattr(rr.sys, "platform", plat)
            assert rr._pdeathsig_preexec() is None

    def test_spawn_kwargs_carry_the_platform_mechanism(self, monkeypatch):
        monkeypatch.setattr(rr.sys, "platform", "linux")
        assert callable(rr._spawn_kwargs().get("preexec_fn"))
        monkeypatch.setattr(rr.sys, "platform", "darwin")
        assert "creationflags" not in rr._spawn_kwargs()
        monkeypatch.setattr(rr.sys, "platform", "win32")
        assert rr._spawn_kwargs().get("creationflags") == rr._CREATE_NO_WINDOW

    def test_windows_job_object_asks_for_kill_on_close(self):
        """The Job Object is what saves Windows, where there is no PDEATHSIG and
        TerminateProcess of the parent skips atexit entirely."""
        assert rr._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x2000

    def test_job_object_attach_is_a_noop_off_windows(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        if sys.platform != "win32":
            assert srv._attach_to_job(os.getpid()) is None


class TestOrphanReaping:
    """macOS has neither PDEATHSIG nor Job Objects, so a hard-killed parent can
    strand the child. The state file bounds that to 'until the next launch'."""

    def _spawn_stray(self, fake_exe, tmp_path) -> subprocess.Popen:
        proc = subprocess.Popen(
            [str(fake_exe), "--host", "127.0.0.1", "--port", str(rr.free_loopback_port()),
             "--alias", "stray"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.4)
        return proc

    def test_state_file_records_what_is_needed_to_reap(self, fake_exe, model_files, tmp_path):
        srv = _server(fake_exe, model_files, tmp_path)
        try:
            handle = srv.start(timeout=30, poll_interval=0.1)
            state = json.loads(srv.state_path.read_text())
            assert state["pid"] == handle.pid
            assert state["port"] == handle.port
            assert state["exe"] == str(fake_exe)
            assert state["parent_pid"] == os.getpid()
        finally:
            srv.stop()

    def test_reap_kills_a_stranded_child_from_a_previous_run(self, fake_exe, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        proc = self._spawn_stray(fake_exe, tmp_path)
        try:
            (state_dir / rr.STATE_FILENAME).write_text(json.dumps({
                "pid": proc.pid, "port": 1234, "exe": str(fake_exe),
                "parent_pid": 999999, "started_at": time.time(),
            }))
            reaped = rr.reap_orphan(state_dir)
            assert reaped == proc.pid
            # `reap_orphan` signals a process it does not own a Popen for, so
            # here (unlike in production, where the orphan's parent is dead and
            # init reaps it) the test harness is still its parent: wait() is how
            # we confirm it actually died, and by which signal.
            assert proc.wait(timeout=10) != 0
            assert not (state_dir / rr.STATE_FILENAME).exists()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def test_reap_refuses_a_pid_whose_command_does_not_match(self, fake_exe, tmp_path):
        """PIDs get recycled. Killing a stale pid blind could kill anything."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        proc = self._spawn_stray(fake_exe, tmp_path)
        try:
            (state_dir / rr.STATE_FILENAME).write_text(json.dumps({
                "pid": proc.pid, "port": 1234,
                "exe": str(tmp_path / "some-other-binary"),
                "parent_pid": 999999, "started_at": time.time(),
            }))
            assert rr.reap_orphan(state_dir) is None
            assert _pid_alive(proc.pid), "an unrelated pid must never be killed"
        finally:
            proc.kill()
            proc.wait()

    def test_reap_with_no_state_file_is_a_no_op(self, tmp_path):
        assert rr.reap_orphan(tmp_path / "nothing") is None

    def test_reap_survives_a_corrupt_state_file(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / rr.STATE_FILENAME).write_text("{not json at all")
        assert rr.reap_orphan(state_dir) is None

    def test_reap_ignores_a_dead_pid_and_clears_the_file(self, tmp_path, fake_exe):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        (state_dir / rr.STATE_FILENAME).write_text(json.dumps({
            "pid": proc.pid, "port": 1, "exe": str(fake_exe),
            "parent_pid": 999999, "started_at": time.time(),
        }))
        assert rr.reap_orphan(state_dir) is None
        assert not (state_dir / rr.STATE_FILENAME).exists()

    def test_reap_never_kills_the_reaper_itself(self, tmp_path, fake_exe):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / rr.STATE_FILENAME).write_text(json.dumps({
            "pid": os.getpid(), "port": 1, "exe": sys.executable,
            "parent_pid": 1, "started_at": time.time(),
        }))
        assert rr.reap_orphan(state_dir) is None

    def test_start_reaps_a_stray_from_the_same_state_dir_first(self, fake_exe, model_files, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        proc = self._spawn_stray(fake_exe, tmp_path)
        (state_dir / rr.STATE_FILENAME).write_text(json.dumps({
            "pid": proc.pid, "port": 1234, "exe": str(fake_exe),
            "parent_pid": 999999, "started_at": time.time(),
        }))
        srv = _server(fake_exe, model_files, tmp_path, state_dir=state_dir)
        try:
            srv.start(timeout=30, poll_interval=0.1)
            assert proc.wait(timeout=10) != 0, "start() must reap the previous run's orphan"
        finally:
            srv.stop()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
