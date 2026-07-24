"""Exhaustiveness guard: every concrete distributor client satisfies the family
contract — declares a `provider` and is wired into DistributorManager.

A 5th client added but not registered in the manager (no fetch_<provider>_product)
would be silently unreachable — the manager would never route to it. This makes
that a red build. (The `provider`-must-exist leg is enforced at class-definition
time by BaseProductClient.__init_subclass__; this test covers registration and
double-checks provider for the real clients.)
"""

from __future__ import annotations

# Force-load the real client modules so __subclasses__() sees them.
import digikey_client  # noqa: F401
import lcsc_client  # noqa: F401
import mouser_client  # noqa: F401
import pololu_client  # noqa: F401
from base_client import BaseProductClient
from distributor_manager import DistributorManager


def _real_clients():
    # Ship clients are modules named "<provider>_client". Exclude test doubles:
    # pytest imports test files by basename ("test_base_client"), which also ends
    # in "_client", so filter those out by their "test"/"tests." module prefix.
    return [c for c in BaseProductClient.__subclasses__()
            if c.__module__.endswith("_client") and not c.__module__.startswith("test")]


def test_family_is_discovered():
    providers = {c.provider for c in _real_clients()}
    assert providers == {"lcsc", "digikey", "mouser", "pololu"}, providers


def test_each_client_declares_a_provider():
    for c in _real_clients():
        assert isinstance(getattr(c, "provider", None), str) and c.provider, (
            f"{c.__name__} has no non-empty provider"
        )


def test_each_client_is_registered_in_the_manager():
    for c in _real_clients():
        method = f"fetch_{c.provider}_product"
        assert hasattr(DistributorManager, method), (
            f"{c.__name__} (provider={c.provider!r}) is not wired into "
            f"DistributorManager — expected a {method}() method, so it is "
            "silently unreachable. Register it in distributor_manager.py."
        )
