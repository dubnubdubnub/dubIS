"""Unit tests for tools/dev-tools-mcp/matchers.py.

Loads matchers.py via importlib (no mcp/FastMCP dependency needed).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# ── Load matchers module without importing mcp ────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MATCHERS = _REPO_ROOT / "tools" / "dev-tools-mcp" / "matchers.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("matchers", _MATCHERS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


matchers = _load_module()

_JS_ROOT = _REPO_ROOT / "js"


# ── api_callers: regression test (the bug) ────────────────────────────────────

def test_api_callers_finds_adjust_part() -> None:
    """find_api_callers must return call sites for adjust_part.

    The real JS writes it through ``apiOn(src, "adjust_part", ...)``
    (js/inventory/adjust-modal.js) so a merged view's adjustment lands on the
    server that holds the stock.  The method name is the SECOND argument there,
    which the string-keyed pattern alone does not match — and a reverse-mapping
    tool that answers "no call sites" for a mutation is worse than no tool.
    """
    results = matchers.find_api_callers("adjust_part", _JS_ROOT, _REPO_ROOT)

    assert len(results) > 0, (
        "find_api_callers('adjust_part') returned [] — the routed-call pattern is broken"
    )

    # Verify the known real file appears in results
    files = {r["file"] for r in results}
    assert any("adjust-modal.js" in f for f in files), (
        f"Expected a hit in js/inventory/adjust-modal.js but got files: {files}"
    )

    # Prove the routed pattern is doing the work (not a legacy dot-call)
    codes = [r["code"] for r in results]
    assert any('apiOn(' in c and '"adjust_part"' in c for c in codes), (
        f'No hit contains apiOn(..., "adjust_part" — routed pattern not matching. codes={codes}'
    )


def test_api_callers_finds_a_plain_string_keyed_call() -> None:
    """The dominant convention — api("method_name", ...) — still matches.

    Kept as its own test now that adjust_part is routed: this is the regression
    the dot-notation-only patterns caused, and it must not go unguarded just
    because the method it was originally written against moved to `apiOn`.
    """
    results = matchers.find_api_callers("list_generic_parts", _JS_ROOT, _REPO_ROOT)
    codes = [r["code"] for r in results]
    assert any('api("list_generic_parts"' in c for c in codes), (
        f"No hit contains the string-keyed form. codes={codes}"
    )


def test_api_callers_negative() -> None:
    """find_api_callers returns [] for a method name that doesn't exist."""
    results = matchers.find_api_callers(
        "definitely_not_a_real_method_xyz", _JS_ROOT, _REPO_ROOT
    )
    assert results == []


# ── event_trace: smoke test ───────────────────────────────────────────────────

def test_event_trace_inventory_updated() -> None:
    """find_event_emitters_listeners finds emitters and listeners for INVENTORY_UPDATED.

    Per CLAUDE.md EventBus table:
      emitters: store.js (onInventoryUpdated)
      listeners: inv-events.js, bom-events.js, app-init.js
    """
    result = matchers.find_event_emitters_listeners(
        "INVENTORY_UPDATED", _JS_ROOT, _REPO_ROOT
    )

    assert result["event"] == "INVENTORY_UPDATED"

    assert len(result["emitters"]) >= 1, (
        "Expected >=1 emitter for INVENTORY_UPDATED (store.js emits it)"
    )
    assert len(result["listeners"]) >= 1, (
        "Expected >=1 listener for INVENTORY_UPDATED (inv-events.js, bom-events.js, app-init.js)"
    )

    # Spot-check: store.js should be an emitter
    emitter_files = {r["file"] for r in result["emitters"]}
    assert any("store.js" in f for f in emitter_files), (
        f"Expected store.js in emitters but got: {emitter_files}"
    )

    # Spot-check: at least one of the known listeners appears
    listener_files = {r["file"] for r in result["listeners"]}
    assert any(
        any(name in f for f in listener_files)
        for name in ("inv-events.js", "bom-events.js", "app-init.js")
    ), (
        f"Expected a known listener file but got: {listener_files}"
    )


def test_event_trace_ignores_comments(tmp_path) -> None:
    """find_event_emitters_listeners must not report EventBus refs that only
    appear inside // or /* */ comments (prose mentioning the pattern).

    Regression for js/store.js:39, where a comment reading
    "Replaces the EventBus.emit(Events.PREFS_CHANGED) pattern" was falsely
    reported as a real emitter.
    """
    js = tmp_path / "js"
    js.mkdir()
    (js / "a.js").write_text(
        "// Replaces the EventBus.emit(Events.FOO_EVENT) pattern\n"
        "/* EventBus.on(Events.FOO_EVENT, x) */\n"
        "EventBus.emit(Events.FOO_EVENT, data);\n",
        encoding="utf-8",
    )

    result = matchers.find_event_emitters_listeners("FOO_EVENT", js, tmp_path)

    assert len(result["emitters"]) == 1
    assert result["emitters"][0]["line"] == 3
    assert result["listeners"] == []
