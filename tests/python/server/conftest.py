"""Shared fixtures for /v1 server tests."""

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.python.helpers import make_api, make_part, write_ledger


@pytest.fixture
def api(tmp_path):
    """InventoryApi wired to a temp directory, seeded with a minimal ledger."""
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    return inst


@pytest.fixture
def client(api):
    """TestClient over the /v1 FastAPI app, backed by a real InventoryApi."""
    with TestClient(create_app(api)) as c:
        yield c
    api.shutdown()
