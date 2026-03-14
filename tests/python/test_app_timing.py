"""Startup/shutdown timing tests — spawn the real GUI app and measure lifecycle.

These tests are Windows-only (WM_CLOSE + TerminateProcess) and require a display.
They are marked @pytest.mark.slow so they don't run in normal CI.
"""

import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

APP_PY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "app.py"))
REAL_DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
PNP_PORT = 7890

STARTUP_TIMEOUT = 30  # seconds
SHUTDOWN_TIMEOUT = 2  # seconds (after fix, should be near-instant)


# ── Helpers ──


def _load_fieldnames():
    """Load FIELDNAMES from constants.json."""
    with open(os.path.join(REAL_DATA_DIR, "constants.json"), encoding="utf-8") as f:
        return json.load(f)["FIELDNAMES"]


ADJ_FIELDNAMES = ["timestamp", "type", "lcsc_part", "quantity", "bom_file", "board_qty", "note"]


def _write_csv(path, fieldnames, rows):
    """Write a CSV file with the given fieldnames and row dicts."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            full = {fn: "" for fn in fieldnames}
            full.update(row)
            writer.writerow(full)


def _seed_empty(data_dir):
    """Create empty CSVs + preferences so the app starts cleanly."""
    fieldnames = _load_fieldnames()
    _write_csv(os.path.join(data_dir, "purchase_ledger.csv"), fieldnames, [])
    _write_csv(os.path.join(data_dir, "inventory.csv"), fieldnames, [])
    _write_csv(os.path.join(data_dir, "adjustments.csv"), ADJ_FIELDNAMES, [])
    with open(os.path.join(data_dir, "preferences.json"), "w") as f:
        json.dump({}, f)


def _seed_with_data(data_dir):
    """Create CSVs with a handful of inventory rows."""
    fieldnames = _load_fieldnames()
    rows = [
        {
            "LCSC Part Number": f"C{100000 + i}",
            "Manufacture Part Number": f"MPN-{i}",
            "Quantity": "50",
            "Description": f"Test Part {i}",
            "Package": "0402",
            "Unit Price($)": "0.01",
            "Ext.Price($)": "0.50",
        }
        for i in range(20)
    ]
    _write_csv(os.path.join(data_dir, "purchase_ledger.csv"), fieldnames, rows)
    _write_csv(os.path.join(data_dir, "inventory.csv"), fieldnames, rows)
    _write_csv(os.path.join(data_dir, "adjustments.csv"), ADJ_FIELDNAMES, [])
    with open(os.path.join(data_dir, "preferences.json"), "w") as f:
        json.dump({}, f)


def _wait_for_ready(port=PNP_PORT, timeout=STARTUP_TIMEOUT):
    """Poll PnP /api/health until it responds 200 or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
            if resp.status == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def _send_wm_close(pid):
    """Find top-level windows belonging to *pid* and post WM_CLOSE."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    target_hwnds = []

    def enum_callback(hwnd, _lparam):
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and user32.IsWindowVisible(hwnd):
            target_hwnds.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    if not target_hwnds:
        raise RuntimeError(f"No visible windows found for pid {pid}")
    for hwnd in target_hwnds:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return len(target_hwnds)


def _trigger_shutdown_via_http(port=PNP_PORT):
    """Trigger shutdown via the test-mode HTTP endpoint."""
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/test/shutdown", timeout=5)
        return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _parse_timing_log(path):
    """Parse timing log into dict of label -> seconds (float)."""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                result[parts[1]] = float(parts[0])
    return result


# ── Fixtures ──


@pytest.fixture
def app_env(tmp_path):
    """Prepare a temporary data directory and return a launcher.

    Usage in tests::

        data_dir, start = app_env
        _seed_empty(data_dir)           # or _seed_with_data
        proc, timing_log = start()
    """
    import shutil

    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    timing_log = str(tmp_path / "timing.log")

    # Copy constants.json (required at module-import time by inventory_api)
    shutil.copy2(os.path.join(REAL_DATA_DIR, "constants.json"), os.path.join(data_dir, "constants.json"))

    processes = []

    def _start():
        env = os.environ.copy()
        env["DUBIS_TIMING_LOG"] = timing_log
        env["DUBIS_DATA_DIR"] = data_dir
        proc = subprocess.Popen(
            [sys.executable, APP_PY, "--test-mode"],
            env=env,
        )
        processes.append(proc)
        return proc, timing_log

    yield data_dir, _start

    # Cleanup: kill any surviving processes
    for proc in processes:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# ── Tests ──


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only (WM_CLOSE + TerminateProcess)")
class TestStartupTiming:

    def test_startup_empty(self, app_env):
        """App with empty CSVs should become ready within STARTUP_TIMEOUT."""
        data_dir, start = app_env
        _seed_empty(data_dir)

        proc, timing_log = start()
        t0 = time.monotonic()
        ready = _wait_for_ready()
        elapsed = time.monotonic() - t0

        assert ready, f"App did not become ready within {STARTUP_TIMEOUT}s"
        assert elapsed < STARTUP_TIMEOUT, f"Startup took {elapsed:.1f}s (limit {STARTUP_TIMEOUT}s)"

        # Verify timing log was written
        timing = _parse_timing_log(timing_log)
        assert "main_start" in timing
        assert "on_ready" in timing

    def test_startup_with_data(self, app_env):
        """App with inventory data should become ready within STARTUP_TIMEOUT."""
        data_dir, start = app_env
        _seed_with_data(data_dir)

        proc, timing_log = start()
        t0 = time.monotonic()
        ready = _wait_for_ready()
        elapsed = time.monotonic() - t0

        assert ready, f"App did not become ready within {STARTUP_TIMEOUT}s"
        assert elapsed < STARTUP_TIMEOUT, f"Startup took {elapsed:.1f}s (limit {STARTUP_TIMEOUT}s)"


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only (WM_CLOSE + TerminateProcess)")
class TestShutdownTiming:

    def test_shutdown_empty(self, app_env):
        """Shutdown with empty data should complete within SHUTDOWN_TIMEOUT."""
        data_dir, start = app_env
        _seed_empty(data_dir)

        proc, timing_log = start()
        assert _wait_for_ready(), "App did not start"

        _send_wm_close(proc.pid)
        t0 = time.monotonic()
        try:
            proc.wait(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail(f"Shutdown took >{SHUTDOWN_TIMEOUT}s (process had to be killed)")
        elapsed = time.monotonic() - t0
        assert elapsed < SHUTDOWN_TIMEOUT, f"Shutdown took {elapsed:.1f}s (limit {SHUTDOWN_TIMEOUT}s)"

    def test_shutdown_with_data(self, app_env):
        """Shutdown with inventory data should complete within SHUTDOWN_TIMEOUT."""
        data_dir, start = app_env
        _seed_with_data(data_dir)

        proc, timing_log = start()
        assert _wait_for_ready(), "App did not start"

        _send_wm_close(proc.pid)
        t0 = time.monotonic()
        try:
            proc.wait(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail(f"Shutdown took >{SHUTDOWN_TIMEOUT}s (process had to be killed)")
        elapsed = time.monotonic() - t0
        assert elapsed < SHUTDOWN_TIMEOUT, f"Shutdown took {elapsed:.1f}s (limit {SHUTDOWN_TIMEOUT}s)"

    def test_shutdown_via_http(self, app_env):
        """Shutdown via /api/test/shutdown should complete within SHUTDOWN_TIMEOUT."""
        data_dir, start = app_env
        _seed_empty(data_dir)

        proc, timing_log = start()
        assert _wait_for_ready(), "App did not start"

        ok = _trigger_shutdown_via_http()
        assert ok, "HTTP shutdown request failed"

        t0 = time.monotonic()
        try:
            proc.wait(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail(f"Shutdown took >{SHUTDOWN_TIMEOUT}s (process had to be killed)")
        elapsed = time.monotonic() - t0
        assert elapsed < SHUTDOWN_TIMEOUT, f"Shutdown took {elapsed:.1f}s (limit {SHUTDOWN_TIMEOUT}s)"
