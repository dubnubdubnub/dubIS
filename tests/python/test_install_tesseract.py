"""Tests for inventory_api.install_tesseract — best-effort winget install.

No real winget/network is invoked; we monkeypatch ocr_engine.ensure_tesseract,
shutil.which, and subprocess.run so each branch (already-installed, non-win32,
winget-missing, success, non-zero exit) is exercised deterministically. The
method must NEVER raise — it always returns a {ok, message, available} dict.
"""

import subprocess

import ocr_engine
from inventory_api import InventoryApi


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_already_installed_skips_subprocess(monkeypatch):
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: True)

    def _boom(*a, **k):
        raise AssertionError("subprocess.run must not be called when installed")

    monkeypatch.setattr(subprocess, "run", _boom)

    api = InventoryApi(debug=True)
    out = api.install_tesseract()
    assert out == {"ok": True, "message": "Tesseract is already installed.", "available": True}


def test_non_win32_returns_hint(monkeypatch):
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: False)
    monkeypatch.setattr("inventory_api.sys.platform", "linux")

    api = InventoryApi(debug=True)
    out = api.install_tesseract()
    assert out["ok"] is False
    assert out["available"] is False
    assert out["message"] == ocr_engine.INSTALL_HINT


def test_winget_missing(monkeypatch):
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: False)
    monkeypatch.setattr("inventory_api.sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda _name: None)

    api = InventoryApi(debug=True)
    out = api.install_tesseract()
    assert out["ok"] is False
    assert out["available"] is False
    assert "winget not found" in out["message"]


def test_winget_success_redetects_engine(monkeypatch):
    # ensure_tesseract: False first (so it proceeds), then True after install.
    calls = {"n": 0}

    def _ensure():
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(ocr_engine, "ensure_tesseract", _ensure)
    monkeypatch.setattr("inventory_api.sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda _name: r"C:\winget.exe")

    captured = {}

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc(returncode=0)

    monkeypatch.setattr(subprocess, "run", _run)

    api = InventoryApi(debug=True)
    out = api.install_tesseract()
    assert out == {"ok": True, "message": "Tesseract installed.", "available": True}

    # Pinned package id + accept-agreement flags must be present.
    assert "UB-Mannheim.TesseractOCR" in captured["cmd"]
    assert "--accept-package-agreements" in captured["cmd"]
    assert "--accept-source-agreements" in captured["cmd"]


def test_winget_nonzero_exit(monkeypatch):
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: False)
    monkeypatch.setattr("inventory_api.sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda _name: r"C:\winget.exe")
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **k: _FakeProc(returncode=1, stderr="install error blah"),
    )

    api = InventoryApi(debug=True)
    out = api.install_tesseract()
    assert out["ok"] is False
    assert out["available"] is False
    assert "winget exited 1" in out["message"]
    assert "install error blah" in out["message"]
    assert ocr_engine.INSTALL_HINT in out["message"]


def test_winget_run_raises_does_not_propagate(monkeypatch):
    monkeypatch.setattr(ocr_engine, "ensure_tesseract", lambda: False)
    monkeypatch.setattr("inventory_api.sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda _name: r"C:\winget.exe")

    def _raise(*a, **k):
        raise OSError("cannot spawn winget")

    monkeypatch.setattr(subprocess, "run", _raise)

    api = InventoryApi(debug=True)
    out = api.install_tesseract()  # must not raise
    assert out["ok"] is False
    assert out["available"] is False
    assert "Install failed to start" in out["message"]
