"""Opt-in *live* distributor test tier — hits real distributor endpoints.

These tests are NOT part of the default suite. Every test here is decorated
``@pytest.mark.live`` and the ``live`` marker is deselected by default via
``addopts = ["-m", "not live"]`` in ``pyproject.toml``. They make real network
calls (and, for DigiKey, may open a browser) and measure real latency.

Run them explicitly with::

    pytest -m live

Latency for each real fetch is printed to stdout and appended to a rolling
JSON log at ``tests/python/.live_latencies.json`` (gitignored, human-inspection
only — no assertions are made on it).

Missing credentials do NOT skip — they ``pytest.fail`` with an actionable
message, because the whole point of this tier is to exercise real fetches.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import time
from datetime import datetime

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

from distributor_manager import DistributorManager  # noqa: E402

# Repo root computed from this file's location (works in any checkout/worktree).
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")

# Secret files we reuse (a cached login) but never mutate in place — copied into
# a tmp data dir so any session-invalidation cleanup hits the COPY, not the original.
SECRET_FILES = ("digikey_cookies.json", "mouser_credentials.json")

# Rolling, human-inspection-only latency log (gitignored).
LATENCY_LOG = os.path.join(os.path.dirname(__file__), ".live_latencies.json")

# Real identifiers used by the live tests.
LCSC_CODE = "C2875244"   # real 16MHz crystal
POLOLU_SKU = "1992"      # reused from POLOLU_HARDCODED in scripts/capture-distributor-fixtures.py
MOUSER_MPN = "LM358DR"   # real dual op-amp MPN
# (DigiKey is a session smoke only — no product fetch is feasible in pytest.)


@pytest.fixture
def live_data(tmp_path):
    """A DistributorManager rooted at a tmp data dir seeded with cached secrets.

    Copies ``digikey_cookies.json`` / ``mouser_credentials.json`` from the
    repo-root ``data/`` dir if they exist (silently skipping missing files), so
    the cached login is reused each run while confining any cleanup that might
    DELETE a cookie file to the copy rather than the real one.
    """
    tmp_data = tmp_path / "data"
    tmp_data.mkdir(parents=True, exist_ok=True)
    for name in SECRET_FILES:
        src = os.path.join(DATA_DIR, name)
        if os.path.exists(src):
            shutil.copy(src, str(tmp_data / name))
    return DistributorManager(str(tmp_data), lambda: None)


@contextlib.contextmanager
def record_latency(distributor: str, identifier: str):
    """Time the wrapped block, print a latency line, and append it to the log.

    Writing the latency log must never fail the test, so all log I/O is wrapped
    and any error is printed rather than raised.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        seconds = time.perf_counter() - start
        print(f"[latency] {distributor} {identifier}: {seconds:.2f}s")
        record = {
            "ts": datetime.now().isoformat(),
            "distributor": distributor,
            "identifier": identifier,
            "seconds": seconds,
        }
        try:
            records = []
            if os.path.exists(LATENCY_LOG):
                try:
                    with open(LATENCY_LOG, encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, list):
                        records = loaded
                except (json.JSONDecodeError, OSError):
                    # Corrupt/unreadable log — start fresh rather than fail.
                    records = []
            records.append(record)
            with open(LATENCY_LOG, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
        except OSError as exc:  # pragma: no cover - defensive, never fatal
            print(f"[latency] WARNING: could not write {LATENCY_LOG}: {exc}")


def _assert_sane_product(result, provider: str) -> None:
    """Shared sanity checks for a distributor product dict."""
    assert isinstance(result, dict), f"expected a dict, got {type(result)!r}"
    assert result.get("provider") == provider, (
        f"expected provider {provider!r}, got {result.get('provider')!r}"
    )
    assert (result.get("mpn") or result.get("productCode")), (
        "expected a non-empty mpn or productCode"
    )
    assert isinstance(result.get("prices"), list), "expected prices to be a list"
    assert isinstance(result.get("stock"), int), "expected stock to be an int"


@pytest.mark.live
def test_lcsc_live(live_data):
    """Fetch a known real LCSC part over the wire."""
    with record_latency("lcsc", LCSC_CODE):
        result = live_data.fetch_lcsc_product(LCSC_CODE)
    assert result is not None, (
        f"LCSC fetch for {LCSC_CODE} returned None — likely a network or "
        f"endpoint problem (check connectivity / LCSC API availability)"
    )
    _assert_sane_product(result, "lcsc")


@pytest.mark.live
def test_pololu_live(live_data):
    """Fetch a known real Pololu SKU over the wire."""
    with record_latency("pololu", POLOLU_SKU):
        result = live_data.fetch_pololu_product(POLOLU_SKU)
    assert result is not None, (
        f"Pololu fetch for SKU {POLOLU_SKU} returned None — likely a network or "
        f"endpoint problem (check connectivity / Pololu site availability)"
    )
    _assert_sane_product(result, "pololu")


@pytest.mark.live
def test_mouser_live(live_data):
    """Fetch a known real Mouser MPN — requires a configured API key."""
    if live_data._mouser.get_api_key() is None:
        pytest.fail(
            "no Mouser API key configured (data/mouser_credentials.json) — "
            "set one in the app first"
        )
    with record_latency("mouser", MOUSER_MPN):
        result = live_data.fetch_mouser_product(MOUSER_MPN)
    assert result is not None, (
        f"Mouser fetch for {MOUSER_MPN} returned None — likely a network or "
        f"endpoint/API-key problem (check connectivity / Mouser API)"
    )
    _assert_sane_product(result, "mouser")


@pytest.mark.live
def test_digikey_session_live(live_data):
    """Validate the cached DigiKey session — requires cached cookies.

    This is a SESSION SMOKE only: a DigiKey product fetch needs a WebView2 GUI
    loop that isn't feasible in pytest, so we only validate that the cached
    session is live. A warm cache returns instantly with no browser; a stale
    cache opens a browser to re-login (the intended behavior of this tier).
    """
    if live_data._digikey._load_cookies() is None:
        pytest.fail(
            "no DigiKey session cached (data/digikey_cookies.json) — "
            "log into DigiKey via the app first"
        )
    with record_latency("digikey", "session"):
        live = live_data._digikey.ensure_session(interactive=True)
    assert live is True, (
        "DigiKey session is not live — re-login via the app (cached cookies "
        "may be expired/invalid)"
    )
