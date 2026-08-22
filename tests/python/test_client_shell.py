"""Tests for client_shell.ClientShell — the ~9-method pywebview bridge.

Two concerns, kept separate from tests/python/test_api_surface.py's rigorous
name+signature freeze (which is the actual guard against silently breaking
the JS bridge contract):

  - a lightweight surface sanity check here (reuses the same frozen name set,
    imported rather than duplicated, so there's one source of truth for
    "what's on the bridge")
  - delegation tests: does each ClientShell method actually forward to the
    right place with the right arguments, and is the InventoryApi instance
    the single source of truth for the BOM-dirty/force-close flags (rather
    than ClientShell keeping a second copy)?
"""
from __future__ import annotations

from unittest.mock import MagicMock

import client_shell
from client_shell import ClientShell
from tests.python.test_api_surface import FROZEN_SURFACE


def test_surface_matches_frozen_names(api):
    shell = ClientShell(api)
    live = {
        n for n in dir(shell)
        if not n.startswith("_") and callable(getattr(shell, n))
    }
    assert live == set(FROZEN_SURFACE)


class TestFileDialogDelegation:
    """file_dialogs.py functions are module-level and window-independent —
    ClientShell just forwards args/return value untouched."""

    def test_open_file_dialog_delegates(self, api, monkeypatch):
        mock = MagicMock(return_value={"name": "x.csv"})
        monkeypatch.setattr(client_shell.file_dialogs, "open_file_dialog", mock)
        shell = ClientShell(api)

        result = shell.open_file_dialog("My Title", "/some/dir")

        mock.assert_called_once_with("My Title", "/some/dir")
        assert result == {"name": "x.csv"}

    def test_open_file_dialog_defaults(self, api, monkeypatch):
        mock = MagicMock(return_value=None)
        monkeypatch.setattr(client_shell.file_dialogs, "open_file_dialog", mock)
        shell = ClientShell(api)

        shell.open_file_dialog()

        mock.assert_called_once_with("Select CSV file", None)

    def test_save_file_dialog_delegates(self, api, monkeypatch):
        mock = MagicMock(return_value={"path": "/x/export.csv"})
        monkeypatch.setattr(client_shell.file_dialogs, "save_file_dialog", mock)
        shell = ClientShell(api)

        result = shell.save_file_dialog("csv,content", "out.csv", "/dir", '{"a":1}')

        mock.assert_called_once_with("csv,content", "out.csv", "/dir", '{"a":1}')
        assert result == {"path": "/x/export.csv"}

    def test_load_file_delegates(self, api, monkeypatch):
        mock = MagicMock(return_value={"name": "y.csv"})
        monkeypatch.setattr(client_shell.file_dialogs, "load_file", mock)
        shell = ClientShell(api)

        result = shell.load_file("/x/y.csv")

        mock.assert_called_once_with("/x/y.csv")
        assert result == {"name": "y.csv"}


class TestFlagsRoundtripThroughApi:
    """set_bom_dirty/confirm_close forward to InventoryApi, which is the
    single source of truth app.pyw's on_closing reads from."""

    def test_set_bom_dirty_writes_through_to_api(self, api):
        shell = ClientShell(api)
        assert api._bom_dirty is False

        shell.set_bom_dirty(True)
        assert api._bom_dirty is True

        shell.set_bom_dirty(False)
        assert api._bom_dirty is False

    def test_set_bom_dirty_coerces_like_api(self, api):
        shell = ClientShell(api)
        shell.set_bom_dirty(1)
        assert api._bom_dirty is True

    def test_confirm_close_delegates_to_api(self, api, monkeypatch):
        mock = MagicMock()
        monkeypatch.setattr(api, "confirm_close", mock)
        shell = ClientShell(api)

        shell.confirm_close()

        mock.assert_called_once_with()

    def test_confirm_close_sets_force_close_flag(self, api, monkeypatch):
        # Exercise the real InventoryApi.confirm_close (not mocked) to prove
        # the flag genuinely lives on api, not on a shell-local copy.
        # Imported lazily (only usage of `webview` in this file) so the module
        # stays importable without pulling in the GUI dependency at collection time.
        import webview
        monkeypatch.setattr(webview, "windows", [MagicMock()])
        shell = ClientShell(api)
        assert api._force_close is False

        shell.confirm_close()

        assert api._force_close is True


class TestWebviewReadySignal:
    """notify_webview_ready forwards to InventoryApi, which invokes the
    app.pyw-registered self-heal callback (clears the WebView2 launch sentinel
    proving the profile loaded cleanly). See webview_profile.py."""

    def test_notify_webview_ready_delegates_to_api(self, api, monkeypatch):
        mock = MagicMock()
        monkeypatch.setattr(api, "notify_webview_ready", mock)
        shell = ClientShell(api)

        shell.notify_webview_ready()

        mock.assert_called_once_with()

    def test_api_notify_invokes_registered_callback(self, api):
        called = []
        api._on_webview_ready = lambda: called.append(True)

        api.notify_webview_ready()

        assert called == [True]

    def test_api_notify_without_callback_is_safe(self, api):
        # Headless server / tests register no callback — must be a no-op, not raise.
        api.notify_webview_ready()


class TestDigikeyAndPoAndTesseractDelegation:
    def test_start_digikey_login_delegates(self, api, monkeypatch):
        mock = MagicMock(return_value={"status": "started"})
        monkeypatch.setattr(api, "start_digikey_login", mock)
        shell = ClientShell(api)

        result = shell.start_digikey_login()

        mock.assert_called_once_with()
        assert result == {"status": "started"}

    def test_open_source_file_delegates(self, api, monkeypatch):
        mock = MagicMock(return_value={"path": "/po/1.csv"})
        monkeypatch.setattr(api, "open_source_file", mock)
        shell = ClientShell(api)

        result = shell.open_source_file("po-1")

        mock.assert_called_once_with("po-1")
        assert result == {"path": "/po/1.csv"}


class TestReaderDelegation:
    """The picture/PDF reader lives on this shell, not /v1: it installs and runs
    on the client machine, and in remote-backend mode app.pyw never boots a local
    /v1 at all. ClientShell still only forwards — the managed directory is chosen
    once, inside ScanFacade, so no path ever crosses the bridge."""

    def test_start_reader_install_delegates(self, api, monkeypatch):
        mock = MagicMock(return_value={"job_id": "abc", "phase": "detect"})
        monkeypatch.setattr(api, "start_reader_install", mock)
        shell = ClientShell(api)

        result = shell.start_reader_install()

        mock.assert_called_once_with()
        assert result == {"job_id": "abc", "phase": "detect"}

    def test_get_reader_install_status_forwards_job_id(self, api, monkeypatch):
        mock = MagicMock(return_value={"job_id": "abc", "done": True})
        monkeypatch.setattr(api, "get_reader_install_status", mock)
        shell = ClientShell(api)

        result = shell.get_reader_install_status("abc")

        mock.assert_called_once_with("abc")
        assert result == {"job_id": "abc", "done": True}

    def test_uninstall_reader_delegates(self, api, monkeypatch):
        mock = MagicMock(return_value={"bytes_reclaimed": 42, "existed": True})
        monkeypatch.setattr(api, "uninstall_reader", mock)
        shell = ClientShell(api)

        result = shell.uninstall_reader()

        mock.assert_called_once_with()
        assert result == {"bytes_reclaimed": 42, "existed": True}

    def test_get_reader_status_delegates(self, api, monkeypatch):
        mock = MagicMock(return_value={"install_dir": "/d/reader", "installed": False})
        monkeypatch.setattr(api, "get_reader_status", mock)
        shell = ClientShell(api)

        result = shell.get_reader_status()

        mock.assert_called_once_with()
        assert result == {"install_dir": "/d/reader", "installed": False}

    def test_shell_takes_no_path_argument(self):
        """A path from JS would let the bridge point the installer/uninstaller at
        an arbitrary directory. Every reader method is nullary except the poll,
        which takes only an opaque job id."""
        import inspect
        for name in ("start_reader_install", "uninstall_reader", "get_reader_status"):
            params = inspect.signature(getattr(ClientShell, name)).parameters
            assert list(params) == ["self"], f"{name} grew an argument"
        assert list(
            inspect.signature(ClientShell.get_reader_install_status).parameters
        ) == ["self", "job_id"]


class TestBenchMarkPassthrough:
    def test_bench_mark_forwards_args_and_return(self, api, monkeypatch):
        mock = MagicMock(return_value=True)
        monkeypatch.setattr(api, "bench_mark", mock)
        shell = ClientShell(api)

        result = shell.bench_mark("js_ready", "detail-json")

        mock.assert_called_once_with("js_ready", "detail-json")
        assert result is True

    def test_bench_mark_default_detail(self, api, monkeypatch):
        mock = MagicMock(return_value=False)
        monkeypatch.setattr(api, "bench_mark", mock)
        shell = ClientShell(api)

        shell.bench_mark("some_label")

        mock.assert_called_once_with("some_label", "")
