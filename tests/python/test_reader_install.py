"""Tests for reader_install — verified downloads and managed-directory removal.

The download half is exercised against a REAL `http.server` on loopback serving
real bytes from a temp dir. Nothing about the socket layer, `requests`, or
chunked transfer encoding is mocked: the properties under test (progress
monotonicity, a `.part` that is never promoted unverified, an absent
`Content-Length`) are properties of an actual HTTP conversation, and a mocked
one would assert only that the mock behaves as imagined.

The uninstall half deletes multiple GiB of a real user's disk in production, so
each safety constraint gets its own test, with real symlinks rather than
patched `os.path` helpers.
"""
import hashlib
import inspect
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import reader_install
from reader_install import (
    ChecksumMismatchError,
    DownloadError,
    UnsafeUninstallTargetError,
    download_verified,
    managed_dir,
    models_dir,
    plan_uninstall,
    progress_pct,
    runtime_dir,
    uninstall,
)

# --------------------------------------------------------------------------
# A real local HTTP server
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Serves files from `server.root`.

    Two routes, because the *headers* are part of what is under test:
      * `/files/<name>` — ordinary response with a `Content-Length`
      * `/chunked/<name>` — `Transfer-Encoding: chunked`, i.e. no
        `Content-Length` at all, which is what a real CDN does for a
        dynamically-served or compressed body.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep pytest output clean
        pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?")[0]
        chunked = path.startswith("/chunked/")
        name = path.rsplit("/", 1)[-1]
        target = Path(self.server.root) / name
        if not target.is_file():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.server.hits[name] = self.server.hits.get(name, 0) + 1
        data = target.read_bytes()

        if chunked:
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            step = 4096
            for start in range(0, len(data), step) or [0]:
                piece = data[start:start + step]
                self.wfile.write(f"{len(piece):x}\r\n".encode())
                self.wfile.write(piece)
                self.wfile.write(b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _Fixture:
    def __init__(self, base_url, root, hits):
        self.base_url = base_url
        self.root = root
        self.hits = hits

    def put(self, name: str, data: bytes) -> tuple[str, str]:
        """Publish `data` as `name`; return (url, sha256)."""
        (self.root / name).write_bytes(data)
        return f"{self.base_url}/files/{name}", hashlib.sha256(data).hexdigest()

    def chunked_url(self, name: str) -> str:
        return f"{self.base_url}/chunked/{name}"


@pytest.fixture
def http_files(tmp_path):
    root = tmp_path / "served"
    root.mkdir()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.root = str(root)
    srv.hits = {}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield _Fixture(f"http://127.0.0.1:{srv.server_address[1]}", root, srv.hits)
    finally:
        srv.shutdown()
        srv.server_close()


class _Recorder:
    """Collects progress calls so their sequence can be asserted, not just their last value."""

    def __init__(self):
        self.calls: list[tuple[int, int | None]] = []

    def __call__(self, done, total):
        self.calls.append((done, total))

    @property
    def dones(self):
        return [d for d, _ in self.calls]


# --------------------------------------------------------------------------
# Download: happy path + progress
# --------------------------------------------------------------------------


def test_happy_path_writes_verified_bytes_and_leaves_no_part(http_files, tmp_path):
    payload = os.urandom(70_000)
    url, sha = http_files.put("weights.gguf", payload)
    dest = tmp_path / "out" / "weights.gguf"

    result = download_verified(url, dest, sha)

    assert dest.read_bytes() == payload
    assert result.skipped is False
    assert result.bytes == len(payload)
    assert result.sha256 == sha
    assert not dest.with_name("weights.gguf.part").exists()
    assert http_files.hits["weights.gguf"] == 1


def test_progress_is_monotonic_and_terminal_call_is_100_pct(http_files, tmp_path):
    payload = os.urandom(50_000)
    url, sha = http_files.put("m.gguf", payload)
    rec = _Recorder()

    download_verified(url, tmp_path / "m.gguf", sha, rec, chunk_size=4096)

    assert len(rec.calls) > 2, "a 50 KiB body at 4 KiB chunks must report intermediate progress"
    assert rec.dones == sorted(rec.dones), f"progress went backwards: {rec.dones}"
    done, total = rec.calls[-1]
    assert done == len(payload)
    assert progress_pct(done, total) == 100.0
    # The percentage is monotonic too, and no call claims completion before the
    # bytes are actually all in — a UI would otherwise hide the bar early.
    pcts = [progress_pct(d, t) for d, t in rec.calls]
    assert pcts == sorted(pcts)
    assert pcts[0] == 0.0
    assert all(d == len(payload) for d, t in rec.calls if progress_pct(d, t) == 100.0)


def test_progress_never_exceeds_100_pct(http_files, tmp_path):
    payload = os.urandom(30_000)
    url, sha = http_files.put("p.gguf", payload)
    rec = _Recorder()

    download_verified(url, tmp_path / "p.gguf", sha, rec, chunk_size=1024)

    assert all(progress_pct(d, t) is None or progress_pct(d, t) <= 100.0 for d, t in rec.calls)


# --------------------------------------------------------------------------
# Download: checksum mismatch
# --------------------------------------------------------------------------


def test_checksum_mismatch_leaves_no_file_at_destination(http_files, tmp_path):
    url, _real_sha = http_files.put("bad.gguf", b"the actual bytes")
    wrong = "0" * 64
    dest = tmp_path / "bad.gguf"

    with pytest.raises(ChecksumMismatchError) as exc:
        download_verified(url, dest, wrong)

    assert not dest.exists(), "a wrong-hash download must never be promoted to the final path"
    assert not dest.with_name("bad.gguf.part").exists(), "the .part must be discarded, not left to be trusted later"
    assert exc.value.expected == wrong
    assert exc.value.actual == hashlib.sha256(b"the actual bytes").hexdigest()


def test_malformed_sha256_fails_before_any_request(http_files, tmp_path):
    url, _ = http_files.put("x.gguf", b"abc")

    with pytest.raises(ValueError):
        download_verified(url, tmp_path / "x.gguf", "not-a-hash")

    assert http_files.hits == {}, "a typo'd pin must fail instantly, not after a multi-GiB transfer"


def test_http_error_raises_download_error_and_writes_nothing(http_files, tmp_path):
    dest = tmp_path / "missing.gguf"

    with pytest.raises(DownloadError) as exc:
        download_verified(f"{http_files.base_url}/files/missing.gguf", dest, "a" * 64)

    assert not dest.exists()
    assert "missing.gguf" in exc.value.url


# --------------------------------------------------------------------------
# Download: leftover .part and idempotency
# --------------------------------------------------------------------------


def test_leftover_garbage_part_is_discarded(http_files, tmp_path):
    payload = os.urandom(20_000)
    url, sha = http_files.put("resume.gguf", payload)
    dest = tmp_path / "resume.gguf"
    part = tmp_path / "resume.gguf.part"
    part.write_bytes(b"truncated bytes from an interrupted run")

    result = download_verified(url, dest, sha)

    assert dest.read_bytes() == payload, "leftover partial bytes must not be prepended/kept"
    assert result.sha256 == sha
    assert not part.exists()


def test_leftover_part_is_never_promoted_even_when_its_bytes_are_correct(http_files, tmp_path):
    """A `.part` is incomplete by definition — even correct-looking content in one
    is re-fetched rather than renamed into place. Proven by the server being hit:
    if the `.part` had been trusted, there would be no request at all."""
    payload = os.urandom(10_000)
    url, sha = http_files.put("full.gguf", payload)
    dest = tmp_path / "full.gguf"
    (tmp_path / "full.gguf.part").write_bytes(payload)

    result = download_verified(url, dest, sha)

    assert http_files.hits["full.gguf"] == 1, "a .part must never satisfy the download"
    assert result.skipped is False
    assert dest.read_bytes() == payload


def test_already_correct_file_is_skipped_entirely(http_files, tmp_path):
    payload = os.urandom(15_000)
    url, sha = http_files.put("cached.gguf", payload)
    dest = tmp_path / "cached.gguf"
    dest.write_bytes(payload)
    rec = _Recorder()

    result = download_verified(url, dest, sha, rec)

    assert http_files.hits == {}, "an already-verified file must not be re-downloaded"
    assert result.skipped is True
    assert result.bytes == len(payload)
    # A skip still reports completion so a resumed install's bar shows 100%.
    assert progress_pct(*rec.calls[-1]) == 100.0


def test_wrong_hash_file_at_destination_is_replaced(http_files, tmp_path):
    payload = os.urandom(12_000)
    url, sha = http_files.put("stale.gguf", payload)
    dest = tmp_path / "stale.gguf"
    dest.write_bytes(b"a stale or corrupted earlier revision")

    result = download_verified(url, dest, sha)

    assert http_files.hits["stale.gguf"] == 1
    assert dest.read_bytes() == payload
    assert result.skipped is False


# --------------------------------------------------------------------------
# Download: unknown / zero total
# --------------------------------------------------------------------------


def test_missing_content_length_reports_unknown_total_then_completes(http_files, tmp_path):
    payload = os.urandom(20_000)
    http_files.put("chunky.gguf", payload)
    sha = hashlib.sha256(payload).hexdigest()
    rec = _Recorder()

    result = download_verified(http_files.chunked_url("chunky.gguf"), tmp_path / "chunky.gguf", sha, rec)

    assert result.bytes == len(payload)
    assert (tmp_path / "chunky.gguf").read_bytes() == payload
    # No Content-Length: every in-flight report is indeterminate, not 0%.
    assert all(t is None for _d, t in rec.calls[:-1]), rec.calls
    assert all(progress_pct(d, t) is None for d, t in rec.calls[:-1])
    # ...and the terminal call still resolves to a real 100%.
    assert progress_pct(*rec.calls[-1]) == 100.0
    assert rec.dones == sorted(rec.dones)


def test_unknown_total_is_distinguishable_from_zero_length():
    assert progress_pct(0, None) is None, "unknown must not masquerade as 0%"
    assert progress_pct(0, 0) == 100.0, "a genuinely empty file is complete, not indeterminate"
    assert progress_pct(5, 10) == 50.0
    assert progress_pct(99, 10) == 100.0, "clamped, never over 100"
    assert progress_pct(-5, 10) == 0.0


def test_zero_length_body_downloads_and_verifies(http_files, tmp_path):
    url, sha = http_files.put("empty.bin", b"")
    rec = _Recorder()

    result = download_verified(url, tmp_path / "empty.bin", sha, rec)

    assert result.bytes == 0
    assert (tmp_path / "empty.bin").read_bytes() == b""
    assert progress_pct(*rec.calls[-1]) == 100.0


# --------------------------------------------------------------------------
# Managed paths
# --------------------------------------------------------------------------


def test_managed_paths_all_live_under_one_directory(tmp_path):
    root = managed_dir(tmp_path)
    assert root == tmp_path / "reader"
    for path in (runtime_dir(tmp_path), models_dir(tmp_path)):
        assert root in path.parents, f"{path} must be inside the single managed dir so uninstall covers it"


# --------------------------------------------------------------------------
# Uninstall safety
# --------------------------------------------------------------------------


def _install(data_dir: Path, sizes: dict[str, int]) -> int:
    """Fake an installed reader; return the exact byte total written."""
    total = 0
    for rel, size in sizes.items():
        target = data_dir / "reader" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\0" * size)
        total += size
    return total


def test_deletes_only_the_managed_subdirectory(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "inventory.csv").write_text("part,qty\n")
    (data_dir / "carts.json").write_text("{}")
    (data_dir / "images").mkdir()
    (data_dir / "images" / "scan.png").write_bytes(b"png")
    _install(data_dir, {"models/w.gguf": 2048})

    uninstall(data_dir)

    assert not (data_dir / "reader").exists()
    assert (data_dir / "inventory.csv").read_text() == "part,qty\n"
    assert (data_dir / "carts.json").exists()
    assert (data_dir / "images" / "scan.png").exists()


def test_never_accepts_a_path_to_delete(tmp_path):
    """The public surface takes `data_dir` only — there is no parameter through
    which a caller can name the deletion target. Passing an already-managed path
    therefore *derives another level down* instead of deleting what was passed."""
    assert list(inspect.signature(uninstall).parameters) == ["data_dir"]
    assert list(inspect.signature(plan_uninstall).parameters) == ["data_dir"]

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    keep = data_dir / "reader" / "keep.gguf"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"\0" * 512)
    nested = data_dir / "reader" / "reader" / "inner.gguf"
    nested.parent.mkdir()
    nested.write_bytes(b"\0" * 64)

    # A caller "asking" for <data_dir>/reader to be deleted gets its own
    # reader/ subdirectory removed — the argument is a data dir, never a target.
    reclaimed = uninstall(data_dir / "reader")

    assert reclaimed == 64
    assert not nested.parent.exists()
    assert keep.exists(), "the caller-named path itself must survive; only the derived child goes"


def test_does_not_follow_a_symlink_out_of_the_managed_tree(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    precious = fake_home / "thesis.pdf"
    precious.write_bytes(b"\0" * 100_000)
    _install(data_dir, {"models/w.gguf": 1024})
    link = data_dir / "reader" / "escape"
    os.symlink(fake_home, link)

    plan = plan_uninstall(data_dir)
    reclaimed = uninstall(data_dir)

    assert fake_home.is_dir(), "a symlink inside reader/ must not be followed"
    assert precious.read_bytes() == b"\0" * 100_000
    assert not (data_dir / "reader").exists()
    # The link is counted as a link, not as the 100 KB behind it — otherwise the
    # confirm dialog promises space it cannot free.
    assert plan.bytes_total < 100_000
    assert reclaimed < 100_000


def test_refuses_when_the_managed_dir_itself_is_a_symlink(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep")
    os.symlink(elsewhere, data_dir / "reader")

    with pytest.raises(UnsafeUninstallTargetError):
        uninstall(data_dir)

    assert (elsewhere / "keep.txt").exists()


def test_refuses_when_the_derived_path_resolves_to_the_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "inventory.csv").write_text("part,qty\n")
    os.symlink(data_dir, data_dir / "reader")

    with pytest.raises(UnsafeUninstallTargetError):
        uninstall(data_dir)
    with pytest.raises(UnsafeUninstallTargetError):
        plan_uninstall(data_dir)

    assert (data_dir / "inventory.csv").exists()


def test_refuses_a_root_data_dir(tmp_path):
    with pytest.raises(UnsafeUninstallTargetError):
        uninstall(os.path.abspath(os.sep))
    with pytest.raises(UnsafeUninstallTargetError):
        plan_uninstall(os.path.abspath(os.sep))
    with pytest.raises(UnsafeUninstallTargetError):
        uninstall("")


def test_refuses_when_the_derived_path_resolves_to_the_filesystem_root(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    os.symlink(os.path.abspath(os.sep), data_dir / "reader")

    with pytest.raises(UnsafeUninstallTargetError):
        uninstall(data_dir)


def test_idempotent_when_nothing_is_installed(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    assert uninstall(data_dir) == 0
    _install(data_dir, {"models/w.gguf": 256})
    assert uninstall(data_dir) == 256
    assert uninstall(data_dir) == 0, "a second uninstall must succeed and report nothing, not raise"


def test_reports_reclaimed_bytes_accurately(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    expected = _install(
        data_dir,
        {
            "runtime/llama-server": 4096,
            "runtime/lib/libggml.dylib": 1500,
            "models/qwen2.5-vl-7b-q4_k_m.gguf": 65_536,
            "models/mmproj.gguf": 8192,
            "models/qwen.gguf.part": 777,
        },
    )

    plan = plan_uninstall(data_dir)
    assert plan.exists is True
    assert plan.bytes_total == expected
    assert plan.file_count == 5
    assert plan.path == str(data_dir / "reader")
    assert plan.entries == ["models", "runtime"]

    assert uninstall(data_dir) == expected


def test_plan_uninstall_is_read_only_and_reports_an_absent_install(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    empty = plan_uninstall(data_dir)
    assert empty.exists is False
    assert empty.bytes_total == 0
    assert empty.file_count == 0
    assert empty.entries == []
    assert empty.as_dict()["path"] == str(data_dir / "reader")

    _install(data_dir, {"models/w.gguf": 2048})
    plan = plan_uninstall(data_dir)
    assert plan.bytes_total == 2048
    assert (data_dir / "reader" / "models" / "w.gguf").exists(), "planning must not delete anything"


def test_uninstall_docstring_defers_process_stop_to_reader_runtime():
    """The process-stopping half of uninstall lives in reader_runtime.py; a caller
    that deletes the bytes out from under a live llama-server gets a failed unlink
    on Windows and a false reclaimed-bytes number on POSIX. Keep that documented."""
    doc = uninstall.__doc__ or ""
    assert "reader_runtime" in doc
    assert "reader_runtime" in (reader_install.__doc__ or "")
