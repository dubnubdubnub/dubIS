"""Shared fixtures for Python tests."""

import importlib.util
import os
from pathlib import Path

import pytest

import cache_db
import distributor_fixtures
from inventory_api import InventoryApi


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    """When a live-tier run is actually executing (not --collect-only), refresh
    any stale distributor fixtures first. Fires ONLY when live tests are selected,
    so default/CI runs (which deselect `live`) never hit the network.

    ``trylast=True`` ensures pytest's own ``-m`` marker filtering runs first and
    removes deselected items from *items*; otherwise *items* would still contain
    the deselected live tests on a default run and we'd refresh fixtures there."""
    # --collect-only must never touch the network or rewrite fixtures.
    if config.option.collectonly:
        return
    # Ordering-independent guard: the default run and CI use `-m "not live"`
    # (via addopts). A CLI `-m` overrides addopts, so `getoption("-m")` reflects
    # the effective expression. Bail whenever live is *excluded*. NOTE: a naive
    # `"live" not in markexpr` check is wrong because "live" is a substring of
    # "not live"; we must test for the exclusion term itself. Unlike the
    # items-based check below, this does not depend on pytest's hook ordering.
    markexpr = config.getoption("-m", default="") or ""
    if "not live" in markexpr:
        return
    # Second layer (ordering-dependent): relies on `trylast=True` so pytest's own
    # `-m` deselection has already pruned live items from *items* on a default run.
    # Only fire when at least one selected test is in the live tier.
    if not any(item.get_closest_marker("live") for item in items):
        return

    # The capture script filename is hyphenated, so it can't be a normal import.
    # tests/python/conftest.py -> repo root is two levels up.
    repo_root = Path(__file__).resolve().parents[2]
    # Fail loudly rather than silently swallowing a FileNotFoundError later.
    assert (repo_root / "scripts").is_dir(), f"unexpected repo root: {repo_root}"
    script_path = os.path.join(repo_root, "scripts", "capture-distributor-fixtures.py")
    spec = importlib.util.spec_from_file_location("_capture_distributor_fixtures", script_path)
    cap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cap)

    # A refresh failure (e.g. network down) must NOT abort the session — the live
    # tests themselves will surface the real problem. refresh_if_stale skips any
    # distributor whose creds are absent, so this is safe to call for all of them.
    # print() is swallowed during collection (not affected by -s), so use the
    # terminal writer to ensure these messages are actually visible.
    tw = config.get_terminal_writer()
    try:
        refreshed = cap.refresh_if_stale(distributor_fixtures.DISTRIBUTORS)
        if refreshed:
            tw.line("[live] stale distributor fixtures were refreshed")
    except Exception as exc:  # noqa: BLE001 - never let a refresh failure abort the run
        tw.line(f"[live] fixture refresh skipped: {exc}")


@pytest.fixture
def api(tmp_path):
    """InventoryApi wired to a temp directory."""
    inst = InventoryApi()
    inst.base_dir = str(tmp_path)
    inst.input_csv = str(tmp_path / "purchase_ledger.csv")
    inst.output_csv = str(tmp_path / "inventory.csv")
    inst.adjustments_csv = str(tmp_path / "adjustments.csv")
    inst.prefs_json = str(tmp_path / "preferences.json")
    inst.events_dir = str(tmp_path / "events")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    inst.cache_db_path = str(data_dir / "cache.db")
    return inst


@pytest.fixture
def db(tmp_path):
    """SQLite cache database with schema."""
    conn = cache_db.connect(str(tmp_path / "cache.db"))
    cache_db.create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def events_dir(tmp_path):
    """Temporary events directory."""
    d = tmp_path / "events"
    d.mkdir()
    return str(d)
