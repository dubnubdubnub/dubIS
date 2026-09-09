"""Freeze the public pywebview API surface: ``ClientShell``.

pywebview's ``webview/util.py:get_functions()`` registers exactly the attributes
that (a) do not start with ``_`` and (b) pass ``inspect.ismethod()`` — i.e. bound
instance methods. Those become ``window.pywebview.api.<name>`` and the JS frontend
calls them *positionally* via the ``api("name", ...args)`` bridge in ``js/api.js``.

The shell grew past nine with the cross-platform picture/PDF reader
(``start_reader_install`` / ``get_reader_install_status`` / ``uninstall_reader``
/ ``get_reader_status``, replacing the Windows-only ``install_tesseract``).
That is deliberate, not drift: the reader installs and runs on the **client**
machine, and in remote-backend mode ``app.pyw`` never boots a local ``/v1`` at
all, so there is no local HTTP surface to carry it and the remote one is the
wrong machine. Spawning processes and writing binaries to the client's disk is
squarely the "OS-only concerns … that have no HTTP-y shape" the shell is scoped
to. pywebview cannot stream, hence a job id plus a polled status method rather
than one long call. See ``docs/plans/2026-08-21-cross-platform-reader-design.md``
§"Transport: why the client shell, not /v1".

Since Phase 1b Task 8 (``feat(app): desktop = browser on loopback /v1; bridge
shrinks to 9-method client shell``), ``app.pyw`` passes ``ClientShell``
(``client_shell.py``), not ``InventoryApi``, as ``js_api`` — the desktop app
is a browser pointed at the loopback ``/v1`` server, and all business/
inventory traffic goes over HTTP instead of this bridge. This test freezes
*that* surface — the ~9-method OS/window-integration shell — so it cannot
silently rename, drop, reorder-params, or change-a-default on any method the
frontend's bridge fallback (``window.pywebview.api[method]`` in ``js/api.js``)
still depends on.

Tombstone: before this task, this file froze ``InventoryApi``'s full
~76-method surface (the previous bridge). That surface's *route* equivalent
is now frozen by ``tests/python/server/test_v1_surface.py`` (the /v1 HTTP
API is InventoryApi's successor as the primary JS↔Python contract) — that
module keeps its own copy of the old bridge-name list for its
op-id-legitimacy cross-check, since ``InventoryApi`` itself still has all
those methods (unexposed to pywebview, but still called by /v1 route
handlers).

Annotations and return types are intentionally excluded from the frozen signature:
pywebview only passes positional args, so only parameter names/order/defaults are
part of the JS contract, and dropping annotations keeps this stable across Python
versions (string annotations render differently between interpreters).

If this fails after an *intentional* API change, update ``FROZEN_SURFACE``
deliberately — and check whether ``js/`` callers depend on the changed shape.
"""
import inspect

from client_shell import ClientShell
from inventory_api import InventoryApi

# Hardcoded in js/api.js whenPywebviewReady(): the bridge is probed for this exact
# method name to detect readiness. Losing/renaming it hangs app startup silently.
SENTINEL = "set_bom_dirty"

# Public @staticmethods (NOT part of the pywebview bridge — staticmethods fail
# inspect.ismethod — but public API other Python code uses; assert they survive).
# These live on InventoryApi, not ClientShell — the bridge shrank, InventoryApi
# (and the rest of Python that imports it directly) didn't.
PUBLIC_STATICS = ("fix_double_utf8", "get_part_key")

# Public class attributes read directly by other modules/tests
# (mfg_direct_import.py, test_cache_db.py, test_real_data.py). Also InventoryApi.
PUBLIC_CLASS_ATTRS = (
    "FIELDNAMES",
    "ADJ_FIELDNAMES",
    "SECTION_ORDER",
    "FLAT_SECTION_ORDER",
    "SECTION_HIERARCHY",
)

# name -> annotation-free parameter signature. The frozen pywebview surface
# (ClientShell — see module docstring for how this differs from before Task 8).
FROZEN_SURFACE = {
    'bench_mark': "(label, detail='')",
    'confirm_close': '()',
    'get_reader_install_status': '(job_id)',
    'get_reader_status': '()',
    'load_file': '(path)',
    'notify_webview_ready': '()',
    'open_file_dialog': "(title='Select CSV file', default_dir=None)",
    'open_source_file': '(po_id)',
    'restart_app': '()',
    'save_file_dialog': "(content, default_name='export.csv', default_dir=None, links_json=None)",
    'set_bom_dirty': '(dirty)',
    'start_digikey_login': '()',
    'start_reader_install': '()',
    'uninstall_reader': '()',
}


def _norm_sig(method) -> str:
    """Annotation-free signature: param names, order, defaults — what the JS bridge depends on."""
    parts = []
    for p in inspect.signature(method).parameters.values():
        if p.kind is p.VAR_POSITIONAL:
            parts.append("*" + p.name)
        elif p.kind is p.VAR_KEYWORD:
            parts.append("**" + p.name)
        elif p.default is p.empty:
            parts.append(p.name)
        else:
            parts.append(f"{p.name}={p.default!r}")
    return "(" + ", ".join(parts) + ")"


def _live_surface() -> dict[str, str]:
    """The exact filter pywebview applies: public + bound-method, mapped to its signature."""
    shell = ClientShell(InventoryApi())
    return {
        n: _norm_sig(getattr(shell, n))
        for n in dir(shell)
        if not n.startswith("_") and inspect.ismethod(getattr(shell, n))
    }


def test_public_method_names_frozen():
    live = set(_live_surface())
    frozen = set(FROZEN_SURFACE)
    assert live == frozen, (
        "pywebview public method surface changed — this breaks/loses JS bridge methods.\n"
        f"  ADDED (not in freeze):   {sorted(live - frozen)}\n"
        f"  REMOVED (gone from api): {sorted(frozen - live)}\n"
        "If intentional, update FROZEN_SURFACE and check js/ callers + tests/fixtures/."
    )


def test_public_method_signatures_frozen():
    live = _live_surface()
    drift = {
        n: (FROZEN_SURFACE[n], live[n])
        for n in FROZEN_SURFACE
        if n in live and live[n] != FROZEN_SURFACE[n]
    }
    assert not drift, (
        "parameter signature(s) changed — pywebview passes positional args, so this "
        "silently corrupts JS call sites:\n"
        + "\n".join(
            f"  {n}: frozen {frozen_sig}  !=  live {live_sig}"
            for n, (frozen_sig, live_sig) in sorted(drift.items())
        )
    )


def test_pywebview_ready_sentinel_present():
    assert SENTINEL in _live_surface(), (
        f"{SENTINEL!r} is hardcoded in js/api.js whenPywebviewReady(); removing/renaming it "
        "hangs startup silently."
    )


def test_public_statics_and_class_attrs_present():
    api = InventoryApi()
    for name in PUBLIC_STATICS:
        assert callable(getattr(api, name, None)), f"missing public static {name!r}"
    for name in PUBLIC_CLASS_ATTRS:
        assert getattr(type(api), name, None) is not None, f"missing public class attr {name!r}"
