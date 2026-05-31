"""Unit tests for refresh_if_stale in capture-distributor-fixtures.py.

Network + credentials are fully mocked; these tests do NOT hit the network and
are NOT live-marked (they run in the default suite). The script filename has
hyphens, so it is imported via importlib.
"""

import importlib.util
import os
from datetime import datetime, timedelta

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "capture_distributor_fixtures",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "capture-distributor-fixtures.py"),
)
cap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cap)


NOW = datetime(2026, 5, 31, 12, 0, 0)
NOW_ISO = "2026-05-31T12:00:00"


def _days_before(n: int) -> str:
    return (NOW - timedelta(days=n)).isoformat(timespec="seconds")


def _fresh(dist: str) -> dict:
    return {"captured_at": _days_before(1), "parts": {f"{dist}-old": {}}}


def _stale(dist: str) -> dict:
    return {"captured_at": _days_before(60), "parts": {f"{dist}-old": {}}}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Wire cap with a tmp FIXTURE_PATH, fixed NOW, and recording capture stubs."""
    fixture_path = str(tmp_path / "distributor-scrapes.json")
    monkeypatch.setattr(cap, "FIXTURE_PATH", fixture_path)

    calls: list[str] = []

    def _stub(name):
        def inner(parts=None):
            calls.append(name)
            return {"parts": {f"{name}-new": {}}, "errors": {}}
        return inner

    monkeypatch.setattr(cap, "capture_lcsc", _stub("lcsc"))
    monkeypatch.setattr(cap, "capture_pololu", _stub("pololu"))
    monkeypatch.setattr(cap, "capture_mouser", _stub("mouser"))
    monkeypatch.setattr(cap, "capture_digikey", _stub("digikey"))
    # _lcsc_part_list reads the purchase ledger / hardcoded list; stub it out.
    monkeypatch.setattr(cap, "_lcsc_part_list", lambda: ["C1"])
    monkeypatch.setattr(cap, "get_dynamic_digikey_parts", lambda: [])

    # Freeze "now" by patching the datetime cap uses.
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(cap, "datetime", _FrozenDateTime)

    return calls, fixture_path


def _seed(monkeypatch, fixture):
    monkeypatch.setattr(cap, "_load_fixture", lambda: fixture)


def test_fresh_fixture_no_capture_returns_false(wired, monkeypatch):
    calls, _ = wired
    _seed(monkeypatch, {
        "lcsc": _fresh("lcsc"),
        "pololu": _fresh("pololu"),
        "mouser": _fresh("mouser"),
        "digikey": _fresh("digikey"),
    })
    monkeypatch.setattr(cap, "_load_mouser_api_key", lambda: "key")
    monkeypatch.setattr(cap, "_load_digikey_cookies", lambda: "cookie")

    result = cap.refresh_if_stale(cap.distributor_fixtures.DISTRIBUTORS, 30)

    assert result is False
    assert calls == []


def test_stale_public_distributor_captured_and_stamped(wired, monkeypatch):
    calls, fixture_path = wired
    _seed(monkeypatch, {"lcsc": _stale("lcsc"), "pololu": _fresh("pololu")})
    monkeypatch.setattr(cap, "_load_mouser_api_key", lambda: None)
    monkeypatch.setattr(cap, "_load_digikey_cookies", lambda: None)

    result = cap.refresh_if_stale(("lcsc", "pololu"), 30)

    assert result is True
    assert "lcsc" in calls and "pololu" not in calls
    import json
    with open(fixture_path, encoding="utf-8") as f:
        written = json.load(f)
    assert written["lcsc"]["captured_at"] == NOW_ISO
    assert written["lcsc"]["parts"] == {"lcsc-new": {}}
    assert written["captured_at"] == NOW_ISO


def test_stale_mouser_digikey_with_creds_captured(wired, monkeypatch):
    calls, _ = wired
    _seed(monkeypatch, {"mouser": _stale("mouser"), "digikey": _stale("digikey")})
    monkeypatch.setattr(cap, "_load_mouser_api_key", lambda: "key")
    monkeypatch.setattr(cap, "_load_digikey_cookies", lambda: "cookie")

    result = cap.refresh_if_stale(cap.distributor_fixtures.DISTRIBUTORS, 30)

    assert result is True
    assert "mouser" in calls
    assert "digikey" in calls


def test_stale_mouser_digikey_without_creds_skipped_and_preserved(wired, monkeypatch):
    """THE data-loss guard: stale + no creds -> NOT captured, existing preserved."""
    calls, fixture_path = wired
    mouser_block = _stale("mouser")
    digikey_block = _stale("digikey")
    _seed(monkeypatch, {"mouser": mouser_block, "digikey": digikey_block})
    monkeypatch.setattr(cap, "_load_mouser_api_key", lambda: None)
    monkeypatch.setattr(cap, "_load_digikey_cookies", lambda: None)

    # Scope to only the private distributors so absent public blocks don't get
    # captured and mask the guard under test.
    result = cap.refresh_if_stale(("mouser", "digikey"), 30)

    # Nothing captured, so nothing written -> returns False.
    assert result is False
    assert "mouser" not in calls
    assert "digikey" not in calls
    # File must NOT have been written (no merge happened).
    assert not os.path.exists(fixture_path)


def test_stale_mouser_without_creds_preserved_while_lcsc_refreshes(wired, monkeypatch):
    """Mouser stale+no-creds preserved untouched even when lcsc IS refreshed."""
    calls, fixture_path = wired
    mouser_block = _stale("mouser")
    _seed(monkeypatch, {"lcsc": _stale("lcsc"), "mouser": mouser_block})
    monkeypatch.setattr(cap, "_load_mouser_api_key", lambda: None)
    monkeypatch.setattr(cap, "_load_digikey_cookies", lambda: None)

    result = cap.refresh_if_stale(cap.distributor_fixtures.DISTRIBUTORS, 30)

    assert result is True
    assert "lcsc" in calls
    assert "mouser" not in calls
    import json
    with open(fixture_path, encoding="utf-8") as f:
        written = json.load(f)
    # mouser block preserved byte-identically (data-loss guard)
    assert written["mouser"] == mouser_block
    assert written["lcsc"]["parts"] == {"lcsc-new": {}}


def test_public_only_scope_ignores_stale_private(wired, monkeypatch):
    calls, _ = wired
    _seed(monkeypatch, {
        "lcsc": _fresh("lcsc"),
        "pololu": _fresh("pololu"),
        "digikey": _stale("digikey"),
        "mouser": _stale("mouser"),
    })
    monkeypatch.setattr(cap, "_load_mouser_api_key", lambda: "key")
    monkeypatch.setattr(cap, "_load_digikey_cookies", lambda: "cookie")

    result = cap.refresh_if_stale(("lcsc", "pololu"), 30)

    # Only public scope considered; both public are fresh -> nothing to do.
    assert result is False
    assert calls == []


def test_merge_preserves_untouched_distributor(wired, monkeypatch):
    calls, fixture_path = wired
    digikey_block = _fresh("digikey")
    _seed(monkeypatch, {"lcsc": _stale("lcsc"), "digikey": digikey_block})
    monkeypatch.setattr(cap, "_load_mouser_api_key", lambda: None)
    monkeypatch.setattr(cap, "_load_digikey_cookies", lambda: "cookie")

    result = cap.refresh_if_stale(cap.distributor_fixtures.DISTRIBUTORS, 30)

    assert result is True
    assert "lcsc" in calls
    assert "digikey" not in calls  # fresh, not refreshed
    import json
    with open(fixture_path, encoding="utf-8") as f:
        written = json.load(f)
    # digikey untouched (it was fresh, not in stale set)
    assert written["digikey"] == digikey_block
    assert written["lcsc"]["captured_at"] == NOW_ISO
