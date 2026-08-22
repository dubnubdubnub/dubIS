"""Reader wiring: ScanFacade/InventoryApi -> reader_jobs, and the managed dir.

`reader_jobs.py` is tested on its own (`test_reader_jobs.py`); what is untested
without this file is the *wiring* — that the four bridge methods reach it at all,
and that the directory they act on is derived from `InventoryApi.base_dir` rather
than taken from the caller. That second property is the one that matters: a path
crossing the pywebview bridge would let the frontend point a recursive delete at
any directory on the client's disk, so the assertion below is a security
property, not a tidiness one.

Nothing here touches the network. The one install test replaces
`reader_jobs.start_install`/`get_status`, which are the seam `reader_jobs`
already exposes for exactly this.
"""

from __future__ import annotations

import reader_install
import reader_jobs


def test_managed_dir_derives_from_base_dir(api, tmp_path):
    """The facade never accepts a path — it derives one from base_dir."""
    assert api._scan._reader_data_dir() == api.base_dir
    assert api.get_reader_status()["install_dir"] == str(tmp_path / "reader")


def test_status_when_nothing_installed(api, tmp_path):
    status = api.get_reader_status()
    assert status == {
        "install_dir": str(tmp_path / "reader"),
        "installed": False,
        "bytes_total": 0,
        "file_count": 0,
        "entries": [],
        "server_running": False,
        "endpoint": "",
        "active_job_id": "",
    }


def test_status_measures_an_installed_reader(api, tmp_path):
    """The confirm dialog's byte total is the real on-disk size, not an estimate."""
    models = reader_install.models_dir(api.base_dir)
    models.mkdir(parents=True)
    (models / "model.gguf").write_bytes(b"x" * 2048)
    reader_install.runtime_dir(api.base_dir).mkdir(parents=True)

    status = api.get_reader_status()

    assert status["installed"] is True
    assert status["bytes_total"] == 2048
    assert status["file_count"] >= 1
    assert sorted(status["entries"]) == ["models", "runtime"]
    assert status["install_dir"] == str(tmp_path / "reader")


def test_uninstall_removes_the_same_directory_the_status_named(api):
    """Preview and delete must agree — both derive the path the same way."""
    models = reader_install.models_dir(api.base_dir)
    models.mkdir(parents=True)
    (models / "model.gguf").write_bytes(b"y" * 1024)
    previewed = api.get_reader_status()

    result = api.uninstall_reader()

    assert result["path"] == previewed["install_dir"]
    assert result["existed"] is True
    assert result["bytes_reclaimed"] == previewed["bytes_total"] == 1024
    assert not reader_install.managed_dir(api.base_dir).exists()
    assert api.get_reader_status()["installed"] is False


def test_uninstall_is_idempotent(api):
    result = api.uninstall_reader()
    assert result["existed"] is False
    assert result["bytes_reclaimed"] == 0


def test_start_install_passes_the_derived_dir_and_returns_a_status_dict(api, monkeypatch):
    """start_reader_install answers with the poll shape, not a bare job id, so the
    frontend has one dict to render from on its first paint."""
    seen: dict[str, object] = {}

    def fake_start(data_dir, **kwargs):
        seen["data_dir"] = data_dir
        return "job-1"

    monkeypatch.setattr(reader_jobs, "start_install", fake_start)
    monkeypatch.setattr(reader_jobs, "get_status", lambda jid: {"job_id": jid, "phase": "detect"})

    out = api.start_reader_install()

    assert seen["data_dir"] == api.base_dir
    assert out == {"job_id": "job-1", "phase": "detect"}


def test_poll_of_an_unknown_job_is_a_terminal_error_not_an_exception(api):
    """A poll timer must be able to stop. An id from before an app restart is
    genuinely gone, so the honest answer is done-with-an-error, not a raise."""
    status = api.get_reader_install_status("no-such-job")

    assert status["done"] is True
    assert status["phase"] == "error"
    assert status["job_id"] == "no-such-job"
    assert "no-such-job" in status["error"]
    # The frontend renders from a fixed shape; a missing key would be a crash.
    assert set(status) == {
        "job_id", "phase", "message", "bytes_done", "bytes_total", "pct",
        "indeterminate", "done", "error", "tier", "endpoint", "install_dir",
        "phase_history", "elapsed_s",
    }


def test_install_tesseract_is_gone(api):
    """The Windows-only winget path is deleted, not deprecated: it did nothing at
    all on macOS/Linux, and the reader replaces it everywhere."""
    assert not hasattr(api, "install_tesseract")
    assert not hasattr(api._scan, "install_tesseract")
    # The still-meaningful OCR probe stays — it is HTTP-mapped on /v1.
    assert hasattr(api, "ocr_engine_available")
