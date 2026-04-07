"""Tests for BaseProductClient protocol."""

import pytest

from base_client import BaseProductClient


class DummyClient(BaseProductClient):
    provider = "dummy"

    def fetch_product(self, identifier: str) -> dict | None:
        return {"title": identifier, "provider": "dummy"}


class IncompleteClient(BaseProductClient):
    provider = "incomplete"


def test_dummy_client_fetch():
    client = DummyClient()
    result = client.fetch_product("ABC")
    assert result == {"title": "ABC", "provider": "dummy"}


def test_incomplete_client_raises():
    with pytest.raises(TypeError):
        IncompleteClient()


def test_subclass_check():
    assert issubclass(DummyClient, BaseProductClient)
