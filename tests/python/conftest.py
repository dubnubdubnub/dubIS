"""Shared fixtures for Python tests."""

import pytest

import cache_db
from inventory_api import InventoryApi


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
    # Propagate overridden paths to sub-APIs
    inst._gp_api.events_dir = inst.events_dir
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
