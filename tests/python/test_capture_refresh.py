"""Unit tests for refresh_if_stale in capture-distributor-fixtures.py.

Network + credentials are fully mocked; these tests do NOT hit the network and
are NOT live-marked (they run in the default suite). The script filename has
hyphens, so it is imported via importlib.
"""

import importlib.util
import json
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
    monkeypatch.setattr(cap, "capture_mouser_product", _stub("mouser_product"))
    # Nothing here may reach a real server; each test says explicitly whether
    # one is supposed to be configured.
    monkeypatch.setattr(cap, "_server_base_url", lambda: None)
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


# ── the mouser_product block: a deployed server is its credential ────────────
#
# Fetched from a running dubIS server over HTTP rather than by driving a
# browser here, which is what makes it the one Mouser block CI can refresh: it
# costs an API token instead of CDP access. The guard shape is deliberately the
# same as the credentialed ones — no server means the existing block is left
# exactly where it is, never emptied.


def test_stale_mouser_product_with_a_server_is_captured(wired, monkeypatch):
    calls, fixture_path = wired
    _seed(monkeypatch, {"mouser_product": _stale("mouser_product")})
    monkeypatch.setattr(cap, "_server_base_url", lambda: "http://dubis.example")

    result = cap.refresh_if_stale(("mouser_product",), 30)

    assert result is True
    assert "mouser_product" in calls
    import json
    with open(fixture_path, encoding="utf-8") as f:
        written = json.load(f)
    assert written["mouser_product"]["captured_at"] == NOW_ISO
    assert written["mouser_product"]["parts"] == {"mouser_product-new": {}}


def test_stale_mouser_product_without_a_server_is_preserved(wired, monkeypatch):
    """The data-loss guard, server edition."""
    calls, fixture_path = wired
    _seed(monkeypatch, {"mouser_product": _stale("mouser_product")})
    # _server_base_url() already returns None via the `wired` fixture.

    result = cap.refresh_if_stale(("mouser_product",), 30)

    assert result is False
    assert "mouser_product" not in calls
    assert not os.path.exists(fixture_path)


def test_an_all_errors_capture_never_replaces_a_real_one(wired, monkeypatch):
    """A reachable server whose own Mouser fetches are all being challenged
    produces a well-formed capture with nothing in it. Merging that would
    delete the only real data we have, so an empty parts dict is treated as no
    capture at all."""
    calls, fixture_path = wired
    _seed(monkeypatch, {"mouser_product": _stale("mouser_product")})
    monkeypatch.setattr(cap, "_server_base_url", lambda: "http://dubis.example")
    monkeypatch.setattr(
        cap, "capture_mouser_product",
        lambda parts: {"capture_method": "dubis-server", "parts": {},
                       "errors": {p: "challenged" for p in parts}},
    )

    result = cap.refresh_if_stale(("mouser_product",), 30)

    assert result is False
    assert not os.path.exists(fixture_path)
    assert calls == []


def test_the_default_scope_covers_every_capture_block():
    """A block missing from CAPTURE_BLOCKS would never be refreshed by anything
    and would rot in place, so the tuple the default scope reads is worth
    pinning."""
    assert set(cap.distributor_fixtures.CAPTURE_BLOCKS) == {
        "lcsc", "digikey", "mouser", "pololu", "mouser_product",
    }
    assert set(cap.distributor_fixtures.DISTRIBUTORS) <= set(
        cap.distributor_fixtures.CAPTURE_BLOCKS
    )


# ── the server-mediated fetch itself ─────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_request(monkeypatch, payload):
    """Answer any urlopen with *payload* and record the Request it was given."""
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        seen["timeout"] = timeout
        return _FakeResponse(payload)

    monkeypatch.setattr(cap.urllib.request, "urlopen", fake_urlopen)
    return seen


_GOOD_PRODUCT = {
    "provider": "mouser",
    "prices": [{"qty": 1, "price": 0.1}],
    "packagings": [{"name": "Cut Tape", "prices": [{"qty": 1, "price": 0.1}]}],
    "_debug": {"jsonld": {"a lot": "of bytes"}},
}


def test_the_token_travels_as_a_bearer_header(monkeypatch):
    seen = _capture_request(monkeypatch, _GOOD_PRODUCT)
    cap.fetch_mouser_product_via_server("LM358DR", "http://dubis.example", "tok")
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["url"] == (
        "http://dubis.example/v1/distributors/mouser/product/LM358DR"
    )


def test_no_token_sends_no_authorization(monkeypatch):
    """A loopback server with auth off needs none, and sending an empty bearer
    would be rejected rather than ignored."""
    seen = _capture_request(monkeypatch, _GOOD_PRODUCT)
    cap.fetch_mouser_product_via_server("LM358DR", "http://dubis.example", None)
    assert not any(k.lower() == "authorization" for k in seen["headers"])


def test_a_slash_in_the_part_number_is_refused_before_the_request(monkeypatch):
    """Measured against a real server: escaping the slash does not help,
    because the path is decoded before routing, so "MCP3008-I%2FSL" arrives as
    an extra path segment and misses the route. Refusing here turns a mystery
    404 in an unattended weekly job into a sentence explaining itself."""
    seen = _capture_request(monkeypatch, _GOOD_PRODUCT)
    got = cap.fetch_mouser_product_via_server(
        "MCP3008-I/SL", "http://dubis.example", None
    )
    assert "error" in got and "decoded before routing" in got["error"]
    assert "url" not in seen, "it should not have made the doomed request"


def test_no_hardcoded_part_is_one_the_route_cannot_serve():
    """The list is only edited by hand, and a slash-bearing addition would fail
    every week until somebody read the log."""
    unroutable = [p for p in cap.MOUSER_PRODUCT_HARDCODED
                  if cap._reject_unroutable_code(p)]
    assert not unroutable, f"unfetchable part numbers in the list: {unroutable}"


def test_the_debug_blob_is_not_committed(monkeypatch):
    """It is the page's whole JSON-LD and nothing asserts on it."""
    _capture_request(monkeypatch, _GOOD_PRODUCT)
    got = cap.fetch_mouser_product_via_server("LM358DR", "http://dubis.example", None)
    assert "_debug" not in got["product"]
    assert got["product"]["provider"] == "mouser"


def test_a_product_with_no_prices_is_an_error_not_a_capture(monkeypatch):
    """Committing it would swap real data for a shell."""
    _capture_request(monkeypatch, {"provider": "mouser", "prices": []})
    got = cap.fetch_mouser_product_via_server("LM358DR", "http://dubis.example", None)
    assert "error" in got and "no price breaks" in got["error"]


def test_something_that_is_not_a_mouser_product_is_an_error(monkeypatch):
    _capture_request(monkeypatch, {"error": "Product not found"})
    got = cap.fetch_mouser_product_via_server("LM358DR", "http://dubis.example", None)
    assert "error" in got and "not a Mouser product" in got["error"]


def test_an_http_error_names_its_status(monkeypatch):
    """401 is a token problem and 404 is a challenged upstream fetch; the two
    need different fixes, so neither may collapse into "network error"."""
    def boom(req, timeout=None):
        raise cap.urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, None
        )

    monkeypatch.setattr(cap.urllib.request, "urlopen", boom)
    got = cap.fetch_mouser_product_via_server("LM358DR", "http://dubis.example", "bad")
    assert "HTTP 401" in got["error"]


def test_the_url_it_builds_is_a_route_the_server_actually_has():
    """The weekly refresh is the only caller, and it runs unattended against a
    deployed server — so a renamed route would surface as a 404 in a scheduled
    job nobody is watching, months after the rename. Pin it to the generated
    OpenAPI spec instead, which changes in the same commit as the route."""
    spec_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "docs", "openapi-v1.json"
    )
    with open(spec_path, encoding="utf-8") as f:
        paths = json.load(f)["paths"]

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResponse(_GOOD_PRODUCT)

    original = cap.urllib.request.urlopen
    cap.urllib.request.urlopen = fake_urlopen
    try:
        cap.fetch_mouser_product_via_server("LM358DR", "http://dubis.example", None)
    finally:
        cap.urllib.request.urlopen = original

    built = seen["url"].removeprefix("http://dubis.example")
    templated = built.replace("/mouser/", "/{name}/").replace("/LM358DR", "/{code}")
    assert templated in paths, (
        f"the capture script asks for {built}, which is not a /v1 route; "
        f"nearest matches: {[p for p in paths if 'distributors' in p]}"
    )


def test_a_configured_server_url_is_normalised(monkeypatch):
    """A trailing slash from a copy-pasted URL would double up in the path."""
    monkeypatch.setenv(cap.SERVER_URL_ENV, "  http://dubis.example/  ")
    assert cap._server_base_url() == "http://dubis.example"
    monkeypatch.setenv(cap.SERVER_URL_ENV, "   ")
    assert cap._server_base_url() is None
