"""Tests for BaseProductClient caching and protocol."""

import pytest

from base_client import BaseProductClient
from dubis_errors import DistributorError, DistributorTimeout


class DummyClient(BaseProductClient):
    provider = "dummy"

    def _fetch_raw(self, identifier: str) -> dict | None:
        return {"title": identifier, "provider": "dummy"}


class IncompleteClient(BaseProductClient):
    provider = "incomplete"


class CachingClient(BaseProductClient):
    """Test double that tracks calls to _fetch_raw."""

    provider = "caching"

    def __init__(self):
        super().__init__()
        self.call_count = 0
        self.next_result: dict | None = {"title": "result", "provider": "caching"}

    def _fetch_raw(self, identifier: str) -> dict | None:
        self.call_count += 1
        return self.next_result


class ValidatingClient(BaseProductClient):
    """Test double that raises ValueError for invalid identifiers."""

    provider = "validating"

    def _fetch_raw(self, identifier: str) -> dict | None:
        if not identifier.startswith("VALID-"):
            raise ValueError(f"Invalid identifier: {identifier!r}")
        return {"title": identifier, "provider": "validating"}


class ErrorClient(BaseProductClient):
    """Test double that raises non-ValueError exceptions."""

    provider = "error"

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def _fetch_raw(self, identifier: str) -> dict | None:
        self.call_count += 1
        raise RuntimeError("network failure")


class DistributorErrorClient(BaseProductClient):
    """Test double that raises DistributorError/DistributorTimeout."""

    provider = "dist_error"

    def _fetch_raw(self, identifier: str) -> dict | None:
        if identifier == "TIMEOUT":
            raise DistributorTimeout(
                "timed out", provider="test", part_number=identifier,
            )
        raise DistributorError("fetch failed", provider="test")


def test_dummy_client_fetch():
    client = DummyClient()
    result = client.fetch_product("ABC")
    assert result == {"title": "ABC", "provider": "dummy"}


def test_incomplete_client_raises():
    with pytest.raises(TypeError):
        IncompleteClient()


def test_subclass_check():
    assert issubclass(DummyClient, BaseProductClient)


class TestCaching:
    def test_successful_result_cached(self):
        """Successful results are cached and returned on subsequent calls."""
        client = CachingClient()
        result1 = client.fetch_product("X")
        result2 = client.fetch_product("X")
        assert result1 is result2
        assert client.call_count == 1

    def test_none_result_cached(self):
        """None results are cached and returned on subsequent calls."""
        client = CachingClient()
        client.next_result = None
        result1 = client.fetch_product("X")
        result2 = client.fetch_product("X")
        assert result1 is None
        assert result2 is None
        assert client.call_count == 1

    def test_different_identifiers_not_shared(self):
        """Different identifiers have separate cache entries."""
        client = CachingClient()
        client.fetch_product("A")
        client.fetch_product("B")
        assert client.call_count == 2

    def test_cache_dict_accessible(self):
        """The _cache dict is accessible on instances (test_clients.py depends on this)."""
        client = CachingClient()
        client.fetch_product("X")
        assert "X" in client._cache
        assert client._cache["X"] == {"title": "result", "provider": "caching"}

    def test_cache_can_be_prepopulated(self):
        """Pre-populating _cache bypasses _fetch_raw."""
        client = CachingClient()
        cached = {"title": "cached", "provider": "caching"}
        client._cache["PRE"] = cached
        result = client.fetch_product("PRE")
        assert result is cached
        assert client.call_count == 0

    def test_none_can_be_prepopulated(self):
        """Pre-populating _cache with None bypasses _fetch_raw."""
        client = CachingClient()
        client._cache["NOPE"] = None
        result = client.fetch_product("NOPE")
        assert result is None
        assert client.call_count == 0

    def test_clear_cache(self):
        """clear_cache() empties the cache, allowing fresh fetches."""
        client = CachingClient()
        client.fetch_product("X")
        assert client.call_count == 1
        client.clear_cache()
        assert client._cache == {}
        client.fetch_product("X")
        assert client.call_count == 2


class TestErrorHandling:
    def test_value_error_propagates(self):
        """ValueError from _fetch_raw must propagate, not be cached."""
        client = ValidatingClient()
        with pytest.raises(ValueError, match="Invalid identifier"):
            client.fetch_product("BAD")
        # Not cached — same call raises again
        with pytest.raises(ValueError, match="Invalid identifier"):
            client.fetch_product("BAD")

    def test_value_error_not_in_cache(self):
        """ValueError should not create a cache entry."""
        client = ValidatingClient()
        with pytest.raises(ValueError):
            client.fetch_product("BAD")
        assert "BAD" not in client._cache

    def test_other_exceptions_cached_as_none(self):
        """Non-ValueError exceptions are caught, cached as None, and logged."""
        client = ErrorClient()
        result = client.fetch_product("X")
        assert result is None
        assert client._cache["X"] is None
        # Second call returns cached None without calling _fetch_raw again
        result2 = client.fetch_product("X")
        assert result2 is None
        assert client.call_count == 1

    def test_valid_identifier_succeeds(self):
        """Valid identifiers return the product dict."""
        client = ValidatingClient()
        result = client.fetch_product("VALID-1")
        assert result == {"title": "VALID-1", "provider": "validating"}

    def test_distributor_error_propagates(self):
        """DistributorError from _fetch_raw must propagate, not be cached."""
        client = DistributorErrorClient()
        with pytest.raises(DistributorError, match="fetch failed"):
            client.fetch_product("ANY")
        assert "ANY" not in client._cache

    def test_distributor_timeout_propagates(self):
        """DistributorTimeout from _fetch_raw must propagate, not be cached."""
        client = DistributorErrorClient()
        with pytest.raises(DistributorTimeout, match="timed out"):
            client.fetch_product("TIMEOUT")
        assert "TIMEOUT" not in client._cache
