"""Client shell — the surviving ~9-method pywebview bridge (Phase 1b, Task 8).

Before this task, ``InventoryApi`` (76 public methods) was passed as
``js_api`` to ``webview.create_window`` and every JS→Python call went through
the pywebview bridge. As of this task, ``app.pyw`` starts the /v1 loopback
server and points the WebView2 window at ``http://127.0.0.1:<port>/`` — all
inventory/business traffic now goes over HTTP against ``server/``, and
``InventoryApi`` is never exposed to pywebview again (it's still constructed
and still owns all its methods; /v1 routes call it, they just don't go
through this bridge).

``ClientShell`` is what pywebview sees instead: OS/window-integration actions
that have no HTTP-server equivalent (dialogs run in-process against the
native window; DigiKey login and the local picture/PDF reader spawn
client-machine subprocesses and write to the client's disk; ``bench_mark`` is
dev-only startup telemetry). It holds no business logic of its own — every method delegates either to the
module-level functions in ``file_dialogs.py`` (which already operate
directly on ``webview.windows[0]``, no window reference needed here) or to
the existing ``InventoryApi`` methods for anything stateful.

The BOM-dirty/force-close flags stay on the ``InventoryApi`` instance
(``self._api._bom_dirty`` / ``self._api._force_close``) — that was already
the single source of truth ``app.pyw``'s ``on_closing`` reads from, and
``InventoryApi.set_bom_dirty``/``confirm_close`` already owned writing them.
``ClientShell.set_bom_dirty``/``confirm_close`` simply forward to those
methods rather than keeping a second copy of the state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import file_dialogs

if TYPE_CHECKING:
    from inventory_api import InventoryApi


class ClientShell:
    """The pywebview ``js_api`` surface. Frozen by ``tests/python/test_api_surface.py``."""

    def __init__(self, api: "InventoryApi") -> None:
        self._api = api
        self._window = None  # set post-create by app.pyw once the webview window exists

    # ── File dialogs (delegate to file_dialogs.py; already window-independent) ──

    def open_file_dialog(self, title: str = "Select CSV file",
                         default_dir: str | None = None) -> dict[str, Any] | None:
        return file_dialogs.open_file_dialog(title, default_dir)

    def save_file_dialog(self, content: str, default_name: str = "export.csv",
                         default_dir: str | None = None,
                         links_json: str | list | None = None) -> dict[str, str] | None:
        return file_dialogs.save_file_dialog(content, default_name, default_dir, links_json)

    def load_file(self, path: str) -> dict[str, Any] | None:
        return file_dialogs.load_file(path)

    # ── Window lifecycle (flags live on the InventoryApi instance) ──────────

    def set_bom_dirty(self, dirty) -> None:
        self._api.set_bom_dirty(dirty)

    def confirm_close(self) -> None:
        self._api.confirm_close()

    def notify_webview_ready(self) -> None:
        self._api.notify_webview_ready()

    def restart_app(self) -> None:
        """Relaunch the app so a changed server URL takes effect.

        On the bridge rather than /v1 because it is a client-machine action with
        no HTTP shape: a remote server cannot restart the desktop process that
        is talking to it, and would not know which one to restart.
        """
        self._api.request_restart()

    # ── Client-machine actions with no HTTP equivalent ───────────────────────

    def start_digikey_login(self) -> dict[str, Any]:
        return self._api.start_digikey_login()

    def open_source_file(self, po_id: str) -> dict[str, str]:
        return self._api.open_source_file(po_id)

    # ── Local picture/PDF reader ─────────────────────────────────────────────
    #
    # These four replace the Windows-only ``install_tesseract`` and are the one
    # deliberate expansion of this shell past its original nine methods.
    #
    # **Why here and not /v1.** The local reader installs to, and runs on, the
    # *client* machine. In remote-backend mode ``app.pyw`` skips the local
    # server boot entirely, so there is no local ``/v1`` to carry this — and the
    # remote ``/v1`` is the wrong machine: it would download multi-GiB weights
    # onto a cluster node and start a llama-server nowhere near the operator.
    # Spawning processes and writing binaries to the client's disk is exactly
    # the "OS-only concerns … that have no HTTP-y shape" this shell is scoped
    # to. See ``docs/plans/2026-08-21-cross-platform-reader-design.md``
    # §"Transport: why the client shell, not /v1".
    #
    # **Why polled and not streamed.** pywebview has no server-push channel
    # (that is what SSE on ``/v1`` is for, and ``/v1`` is not available here),
    # so an install cannot report progress from inside one call:
    # ``start_reader_install`` returns immediately with a job id and the
    # frontend polls ``get_reader_install_status`` on a timer.

    def start_reader_install(self) -> dict[str, Any]:
        """Start (or join) the local reader install; returns the initial status dict.

        Returns at once — the download runs on a background thread. The dict
        carries ``job_id``; poll ``get_reader_install_status`` with it.
        """
        return self._api.start_reader_install()

    def get_reader_install_status(self, job_id) -> dict[str, Any]:
        """One poll of an install job. See ``reader_jobs.InstallJob.status``.

        Untyped ``job_id`` on purpose: pywebview passes whatever JS handed it,
        and ``reader_jobs.get_status`` already answers an unrecognised id with a
        terminal error dict rather than raising.
        """
        return self._api.get_reader_install_status(job_id)

    def uninstall_reader(self) -> dict[str, Any]:
        """Stop the local reader, then delete its managed directory."""
        return self._api.uninstall_reader()

    def get_reader_status(self) -> dict[str, Any]:
        """Install directory, size, and running state — the pre-click snapshot.

        Also the source of the byte total and path the uninstall confirm names,
        and of ``active_job_id`` so a panel reopened mid-install re-attaches.
        """
        return self._api.get_reader_status()

    # ── Dev telemetry ────────────────────────────────────────────────────────

    def bench_mark(self, label: str, detail: str = "") -> bool:
        return self._api.bench_mark(label, detail)
