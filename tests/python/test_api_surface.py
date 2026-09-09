"""Freeze the public pywebview API surface: ``ClientShell``.

pywebview's ``webview/util.py:get_functions()`` registers exactly the attributes
that (a) do not start with ``_`` and (b) pass ``inspect.ismethod()`` — i.e. bound
instance methods. Those become ``window.pywebview.api.<name>`` and the JS frontend
calls them *positionally* via the ``api("name", ...args)`` bridge in ``js/api.js``.

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
import re
from pathlib import Path

from client_shell import ClientShell
from inventory_api import InventoryApi

REPO_ROOT = Path(__file__).resolve().parents[2]

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
    'install_tesseract': '()',
    'load_file': '(path)',
    'notify_webview_ready': '()',
    'open_file_dialog': "(title='Select CSV file', default_dir=None)",
    'open_source_file': '(po_id)',
    'restart_app': '()',
    'save_file_dialog': "(content, default_name='export.csv', default_dir=None, links_json=None)",
    'set_bom_dirty': '(dirty)',
    'start_digikey_login': '()',
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


def _js_api_call_site_names() -> dict[str, list[str]]:
    """Every `api("name", ...)` name literal in `js/`, mapped to its files."""
    pattern = re.compile(r'\bapi\(\s*["\']([a-z_][a-z0-9_]*)["\']')
    found: dict[str, set[str]] = {}
    for path in sorted((REPO_ROOT / "js").rglob("*.js")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(match.group(1), set()).add(
                str(path.relative_to(REPO_ROOT)))
    return {name: sorted(files) for name, files in found.items()}


def test_js_api_call_sites_resolve_to_a_real_surface():
    """Every `api("name")` in js/ must exist in API_MAP or on the bridge.

    `js/api.js`'s `api()` looks the name up in `API_MAP` (the /v1 HTTP
    surface, generated from `docs/openapi-v1.json`) and *silently* falls
    through to `window.pywebview.api[method]` when it misses. A name in
    neither place is therefore dead on arrival in both clients, with no
    build-time or lint-time signal: in a browser `window.pywebview` is
    undefined ("Cannot read properties of undefined (reading 'api')"), and in
    the desktop app the ~11-method `ClientShell` has no such attribute ("is
    not a function"). Either way `api()` swallows it into an `AppLog.error`
    and returns `undefined`, so the feature just quietly does nothing.

    This is how the three `get_inventory_mirror_info` /
    `enable_inventory_mirror` / `disable_inventory_mirror` calls in
    `js/preferences-modal.js` shipped broken — the /v1 migration moved
    `InventoryApi` off the bridge without giving those three a route. Routes:
    `server/routes/mirror.py`; route tests:
    `tests/python/server/test_mirror_routes.py`.
    """
    api_map = set(re.findall(
        r'^  "([a-z_][a-z0-9_]*)": \{',
        (REPO_ROOT / "js" / "api-map.js").read_text(encoding="utf-8"),
        re.MULTILINE,
    ))
    assert api_map, "parsed no keys out of js/api-map.js — the parse regex is stale"

    bridge = set(_live_surface())
    unresolvable = {
        name: files
        for name, files in _js_api_call_site_names().items()
        if name not in api_map and name not in bridge
    }
    assert not unresolvable, (
        "js/ calls api(\"name\") for method(s) on neither surface — these fail "
        "silently at runtime in both the browser and the desktop app:\n"
        + "\n".join(f"  {name!r} — called from {', '.join(files)}"
                    for name, files in sorted(unresolvable.items()))
        + "\nGive each one a /v1 route (server/routes/, then rerun "
          "`python scripts/gen-openapi.py && python scripts/gen-api-client.py "
          "&& python scripts/gen-cli.py`), or add it to client_shell.py if it "
          "is genuinely an OS-only client concern."
    )
