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
native window; DigiKey login/tesseract-install spawn client-machine
subprocesses; ``bench_mark`` is dev-only startup telemetry). It holds no
business logic of its own — every method delegates either to the
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

    # ── Client-machine actions with no HTTP equivalent ───────────────────────

    def start_digikey_login(self) -> dict[str, Any]:
        return self._api.start_digikey_login()

    def open_source_file(self, po_id: str) -> dict[str, str]:
        return self._api.open_source_file(po_id)

    def install_tesseract(self) -> dict[str, Any]:
        return self._api.install_tesseract()

    # ── Dev telemetry ────────────────────────────────────────────────────────

    def bench_mark(self, label: str, detail: str = "") -> bool:
        return self._api.bench_mark(label, detail)
