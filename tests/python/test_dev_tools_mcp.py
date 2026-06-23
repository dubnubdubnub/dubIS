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
    """find_api_callers must return call sites for adjust_part (string-keyed convention).

    The real JS uses api("adjust_part", ...) in js/inventory-modals.js.
    The old dot-notation patterns would return [] for this — that was the bug.
    """
    results = matchers.find_api_callers("adjust_part", _JS_ROOT, _REPO_ROOT)

    assert len(results) > 0, (
        "find_api_callers('adjust_part') returned [] — the string-keyed pattern is broken"
    )

    # Verify the known real file appears in results
    files = {r["file"] for r in results}
    assert any("inventory-modals.js" in f for f in files), (
        f"Expected a hit in js/inventory-modals.js but got files: {files}"
    )

    # Prove the string-keyed pattern is doing the work (not a legacy dot-call)
    codes = [r["code"] for r in results]
    assert any('api("adjust_part"' in c for c in codes), (
        f"No hit contains api(\"adjust_part\" — string-keyed pattern not matching. codes={codes}"
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
