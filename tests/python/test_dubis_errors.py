"""Tests for dubIS error hierarchy."""

import pytest

from dubis_errors import (
    DubISError,
    DistributorError,
    DistributorTimeout,
    DistributorAuthError,
    CacheError,
)


def test_hierarchy():
    assert issubclass(DistributorError, DubISError)
    assert issubclass(DistributorTimeout, DistributorError)
    assert issubclass(DistributorAuthError, DistributorError)
    assert issubclass(CacheError, DubISError)


def test_distributor_error_carries_provider():
    err = DistributorError("failed", provider="digikey")
    assert err.provider == "digikey"
    assert "failed" in str(err)


def test_timeout_carries_context():
    err = DistributorTimeout("timed out", provider="mouser", part_number="ABC")
    assert err.provider == "mouser"
    assert err.part_number == "ABC"


def test_auth_error_inherits_provider():
    err = DistributorAuthError("auth failed", provider="digikey")
    assert err.provider == "digikey"


def test_cache_error():
    err = CacheError("db locked")
    assert "db locked" in str(err)


def test_distributor_error_extra_kwargs():
    err = DistributorError("fail", provider="lcsc", url="https://example.com")
    assert err.url == "https://example.com"
