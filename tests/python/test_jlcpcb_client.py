"""Unit tests for `jlcpcb_client` — credential intake, pagination, mapping.

Offline: every page comes from `tests/fixtures/jlc-library-page.json` (an
envelope captured in the shape the real API returns, per the design doc's
Appendix) via a stubbed `jlc_session.fetch_page`. The one test that talks to
jlcpcb.com for real is marked ``live``/``credentials`` and is deselected by
default; a missing credential there FAILS with an actionable message rather
than skipping, per the test policy in CLAUDE.md.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

import jlc_session
import jlcpcb_client
from dubis_errors import DistributorAuthError, DistributorError, NotFoundError
from jlcpcb_client import JlcpcbClient, library_record

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "jlc-library-page.json").read_text()
)
ROWS = FIXTURE["data"]["list"]
ACCOUNT = "12625901A"
COOKIE = {"name": "JLCPCB_SESSION_ID", "value": "s3cr3t-uuid", "domain": ".jlcpcb.com"}


@pytest.fixture(autouse=True)
def _clean_nonces():
    jlc_session._nonces.clear()
    yield
    jlc_session._nonces.clear()


@pytest.fixture
def client(tmp_path):
    return JlcpcbClient(sessions_file=jlc_session.store_path(str(tmp_path)))


@pytest.fixture
def paired(client):
    """A client with one stored, already-validated account."""
    jlc_session.store_session(
        client._sessions_file, account=ACCOUNT, cookies=[COOKIE], label="impossible_hardware"
    )
    return client


def _pages(*payloads):
    """Stub `jlc_session.fetch_page` to serve *payloads* in order."""
    calls = []

    def _fetch(cookies, *, page_num=1, page_size=1, keyword="", timeout=15.0):
        calls.append({"page_num": page_num, "page_size": page_size, "cookies": cookies})
        return payloads[min(page_num - 1, len(payloads) - 1)]

    _fetch.calls = calls
    return _fetch


def _envelope(rows, *, total, pages, code=200):
    return {"code": code, "msg": "success",
            "data": {"total": total, "pages": pages, "list": rows}}


# ── Record mapping ───────────────────────────────────────────────────────────


class TestLibraryRecord:
    def test_maps_every_documented_field(self):
        record = library_record(ROWS[0])
        assert record["lcsc"] == "C1525"
        assert record["mpn"] == "CL05B104KO5NNNC"
        assert record["manufacturer"] == "Samsung Electro-Mechanics"
        assert record["package"] == "0402"
        assert record["section"] == "Capacitors"
        assert record["description"].startswith("50V 100nF")
        assert record["qty"] == 4820

    def test_prices_stay_at_zero_because_the_api_has_none(self):
        # The library response carries NO price field. Inventing one would show
        # the user a number JLC never quoted — the same failure the cart planner
        # refuses to commit (CLAUDE.md, "A cart line prices only from a
        # recorded quote").
        record = library_record(ROWS[0])
        assert record["unit_price"] == 0
        assert record["ext_price"] == 0
        assert "price" not in json.dumps(ROWS[0]).lower()

    def test_record_has_exactly_the_inventory_record_fields(self):
        from domain.schema import INVENTORY_FIELDS

        expected = {f.py_key for f in INVENTORY_FIELDS if f.to_js}
        assert set(library_record(ROWS[0])) == expected

    def test_missing_and_null_fields_degrade_to_defaults(self):
        record = library_record({"componentCode": "C1", "componentModel": None})
        assert record["lcsc"] == "C1"
        assert record["mpn"] == ""
        assert record["qty"] == 0

    def test_non_numeric_stock_is_zero_not_a_crash(self):
        assert library_record({"privateStockCount": "lots"})["qty"] == 0


# ── Pairing + credential intake ──────────────────────────────────────────────


class TestReceiveSession:
    def _valid(self, monkeypatch, account=ACCOUNT, total=191):
        monkeypatch.setattr(
            jlc_session, "validate",
            lambda cookies, **kw: {"state": jlc_session.VALID, "account": account,
                                   "total": total, "message": "ok"},
        )

    def test_happy_path_stores_and_reports(self, client, monkeypatch):
        self._valid(monkeypatch)
        nonce = client.mint_pairing_nonce()["nonce"]
        result = client.receive_session(nonce, ACCOUNT, [COOKIE], "impossible_hardware")
        assert result["account"] == ACCOUNT
        assert result["item_count"] == 191
        assert result["state"] == jlc_session.VALID
        assert jlc_session.get_cookies(client._sessions_file, ACCOUNT) == [COOKIE]

    def test_response_carries_no_cookie(self, client, monkeypatch):
        self._valid(monkeypatch)
        nonce = client.mint_pairing_nonce()["nonce"]
        result = client.receive_session(nonce, ACCOUNT, [COOKIE])
        assert COOKIE["value"] not in json.dumps(result)

    def test_pairing_ttl_is_reported(self, client):
        assert client.mint_pairing_nonce()["ttl"] == jlc_session.NONCE_TTL_SECONDS

    def test_a_nonce_is_single_use(self, client, monkeypatch):
        self._valid(monkeypatch)
        nonce = client.mint_pairing_nonce()["nonce"]
        client.receive_session(nonce, ACCOUNT, [COOKIE])
        with pytest.raises(DistributorAuthError):
            client.receive_session(nonce, ACCOUNT, [COOKIE])

    def test_no_nonce_is_refused(self, client, monkeypatch):
        self._valid(monkeypatch)
        # Without this, anything that can reach the port could push a session.
        with pytest.raises(DistributorAuthError):
            client.receive_session("", ACCOUNT, [COOKIE])

    def test_expired_probe_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(
            jlc_session, "validate",
            lambda c, **kw: {"state": jlc_session.EXPIRED, "account": "", "total": 0,
                             "message": "not login"},
        )
        nonce = client.mint_pairing_nonce()["nonce"]
        with pytest.raises(DistributorAuthError, match="rejected"):
            client.receive_session(nonce, ACCOUNT, [COOKIE])
        assert jlc_session.list_public_sessions(client._sessions_file) == []

    def test_inconclusive_probe_does_not_store(self, client, monkeypatch):
        # Mirror image of the keep-on-inconclusive rule: never invalidate on a
        # probe that could not run, and never enshrine one either — an
        # anonymous JLC request mints a session cookie all by itself.
        monkeypatch.setattr(
            jlc_session, "validate",
            lambda c, **kw: {"state": jlc_session.INCONCLUSIVE, "account": "", "total": 0,
                             "message": "offline"},
        )
        nonce = client.mint_pairing_nonce()["nonce"]
        with pytest.raises(DistributorError):
            client.receive_session(nonce, ACCOUNT, [COOKIE])
        assert jlc_session.list_public_sessions(client._sessions_file) == []

    def test_the_resolved_account_wins_over_the_claimed_one(self, client, monkeypatch):
        self._valid(monkeypatch, account="REAL1A")
        nonce = client.mint_pairing_nonce()["nonce"]
        result = client.receive_session(nonce, "CLAIMED9Z", [COOKIE])
        assert result["account"] == "REAL1A"

    def test_the_claimed_account_is_the_fallback_for_an_empty_library(self, client, monkeypatch):
        self._valid(monkeypatch, account="", total=0)
        nonce = client.mint_pairing_nonce()["nonce"]
        assert client.receive_session(nonce, "CLAIMED9Z", [COOKIE])["account"] == "CLAIMED9Z"

    def test_no_account_anywhere_is_an_error(self, client, monkeypatch):
        self._valid(monkeypatch, account="", total=0)
        nonce = client.mint_pairing_nonce()["nonce"]
        with pytest.raises(ValueError, match="customerCode"):
            client.receive_session(nonce, "", [COOKIE])

    def test_a_body_with_no_session_cookie_is_refused(self, client, monkeypatch):
        self._valid(monkeypatch)
        nonce = client.mint_pairing_nonce()["nonce"]
        with pytest.raises(ValueError, match="JLCPCB_SESSION_ID"):
            client.receive_session(nonce, ACCOUNT, [{"name": "_ga", "value": "1"}])

    def test_a_refused_body_does_not_burn_a_valid_nonce_twice(self, client, monkeypatch):
        # The nonce IS consumed first (it authorizes the attempt), so a second
        # attempt needs a new one. Asserted so the ordering is deliberate.
        self._valid(monkeypatch)
        nonce = client.mint_pairing_nonce()["nonce"]
        with pytest.raises(ValueError):
            client.receive_session(nonce, ACCOUNT, [])
        assert jlc_session.consume_nonce(nonce) is False


class TestSessionsAndRevoke:
    def test_list_sessions_is_cookie_free(self, paired):
        listed = paired.list_sessions()
        assert listed["logged_in"] is True
        assert COOKIE["value"] not in json.dumps(listed)

    def test_check_session_aliases_list_sessions(self, paired):
        assert paired.check_session() == paired.list_sessions()

    def test_revoke(self, paired):
        result = paired.revoke_session(ACCOUNT)
        assert result["revoked"] is True
        assert result["accounts"] == []

    def test_revoking_an_unknown_account_is_not_found(self, client):
        with pytest.raises(NotFoundError):
            client.revoke_session("NOPE")


# ── Library fetch ────────────────────────────────────────────────────────────


class TestFetchLibrary:
    def test_single_page(self, paired, monkeypatch):
        monkeypatch.setattr(jlc_session, "fetch_page", _pages(copy.deepcopy(FIXTURE)))
        result = paired.fetch_library()
        assert result["account"] == ACCOUNT
        assert result["total"] == 2
        assert [r["lcsc"] for r in result["records"]] == ["C1525", "C25804"]

    def test_pages_until_the_reported_page_count(self, paired, monkeypatch):
        page1 = _envelope(ROWS, total=4, pages=2)
        page2 = _envelope(
            [{**ROWS[0], "componentCode": "C3"}, {**ROWS[1], "componentCode": "C4"}],
            total=4, pages=2,
        )
        fetch = _pages(page1, page2)
        monkeypatch.setattr(jlc_session, "fetch_page", fetch)
        result = paired.fetch_library()
        assert [r["lcsc"] for r in result["records"]] == ["C1525", "C25804", "C3", "C4"]
        assert [c["page_num"] for c in fetch.calls] == [1, 2]

    def test_asks_for_the_maximum_page_size(self, paired, monkeypatch):
        # 100 is a hard upstream cap: above it JLC answers
        # `{"code":200,"msg":"pageSize cannot exceed 100"}` with no rows.
        fetch = _pages(copy.deepcopy(FIXTURE))
        monkeypatch.setattr(jlc_session, "fetch_page", fetch)
        paired.fetch_library()
        assert fetch.calls[0]["page_size"] == 100

    def test_an_over_large_page_size_is_clamped_not_sent(self, paired, monkeypatch):
        fetch = _pages(copy.deepcopy(FIXTURE))
        monkeypatch.setattr(jlc_session, "fetch_page", fetch)
        paired.fetch_library(page_size=5000)
        assert fetch.calls[0]["page_size"] == 100

    def test_stops_on_an_empty_page_even_if_total_lies(self, paired, monkeypatch):
        fetch = _pages(_envelope(ROWS, total=999, pages=0), _envelope([], total=999, pages=0))
        monkeypatch.setattr(jlc_session, "fetch_page", fetch)
        result = paired.fetch_library()
        assert len(result["records"]) == 2
        assert len(fetch.calls) == 2

    def test_paging_is_bounded(self, paired, monkeypatch):
        # A remote that always answers "one more page" must not loop forever.
        monkeypatch.setattr(
            jlc_session, "fetch_page",
            _pages(_envelope(ROWS, total=10**9, pages=10**9)),
        )
        monkeypatch.setattr(jlcpcb_client, "MAX_PAGES", 3)
        with pytest.raises(DistributorError, match="exceeded 3 pages"):
            paired.fetch_library()

    def test_expired_session_mid_fetch_is_an_auth_error(self, paired, monkeypatch):
        monkeypatch.setattr(jlc_session, "fetch_page", _pages({"code": 460, "msg": "not login"}))
        with pytest.raises(DistributorAuthError, match="expired"):
            paired.fetch_library()

    def test_an_application_error_is_a_distributor_error(self, paired, monkeypatch):
        monkeypatch.setattr(
            jlc_session, "fetch_page",
            _pages({"code": 500, "msg": "pageSize cannot exceed 100"}),
        )
        with pytest.raises(DistributorError):
            paired.fetch_library()

    def test_a_transport_failure_propagates(self, paired, monkeypatch):
        def _boom(*a, **k):
            raise DistributorError("network down", provider="jlcpcb")

        monkeypatch.setattr(jlc_session, "fetch_page", _boom)
        with pytest.raises(DistributorError):
            paired.fetch_library()

    def test_a_successful_fetch_records_last_ok(self, paired, monkeypatch):
        jlc_session.store_session(
            paired._sessions_file, account=ACCOUNT, cookies=[COOKIE],
            last_ok="2000-01-01T00:00:00Z",
        )
        monkeypatch.setattr(jlc_session, "fetch_page", _pages(copy.deepcopy(FIXTURE)))
        paired.fetch_library()
        assert jlc_session.list_public_sessions(paired._sessions_file)[0]["last_ok"] != \
            "2000-01-01T00:00:00Z"

    def test_the_stored_cookie_is_what_gets_sent(self, paired, monkeypatch):
        fetch = _pages(copy.deepcopy(FIXTURE))
        monkeypatch.setattr(jlc_session, "fetch_page", fetch)
        paired.fetch_library()
        assert fetch.calls[0]["cookies"] == [COOKIE]

    def test_unpaired_is_an_auth_error(self, client):
        with pytest.raises(DistributorAuthError, match="No JLC account"):
            client.fetch_library()

    def test_an_unknown_account_is_an_auth_error(self, paired):
        with pytest.raises(DistributorAuthError, match="No stored JLC session"):
            paired.fetch_library("SOMEONE-ELSE")

    def test_several_accounts_require_naming_one(self, paired):
        jlc_session.store_session(
            paired._sessions_file, account="OTHER9Z", cookies=[{**COOKIE, "value": "b"}]
        )
        with pytest.raises(ValueError, match="OTHER9Z"):
            paired.fetch_library()

    def test_naming_one_of_several_works(self, paired, monkeypatch):
        jlc_session.store_session(
            paired._sessions_file, account="OTHER9Z", cookies=[{**COOKIE, "value": "b"}]
        )
        monkeypatch.setattr(jlc_session, "fetch_page", _pages(copy.deepcopy(FIXTURE)))
        assert paired.fetch_library("OTHER9Z")["account"] == "OTHER9Z"


@pytest.mark.parametrize("module", ["jlcpcb_client.py", "jlc_session.py"])
def test_no_browser_dependency_anywhere(module):
    """JLC has no bot wall on this path, so nothing here may reach for a
    browser. A `browser_page` / WebView2 / CDP import in either module would
    regress the very property that made JLC the right first target — and would
    make the feature desktop-only for no reason.

    Checks imports, not text, so the docstrings can keep SAYING "no browser".
    """
    import ast

    tree = ast.parse((Path(__file__).resolve().parents[2] / module).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"browser_page", "webview", "digikey_cdp", "playwright"}, imported


# ── Live tier ────────────────────────────────────────────────────────────────


@pytest.mark.live
@pytest.mark.credentials
def test_live_library_fetch():
    """Fetch the real private library with the locally stored JLC session.

    Needs a paired account in `data/jlc_sessions.json` (pair one through the
    extension). A missing credential FAILS — a green skip would report success
    for a fetch that never happened.
    """
    repo_root = Path(__file__).resolve().parents[2]
    store = str(repo_root / "data" / "jlc_sessions.json")
    if not os.path.exists(store):
        pytest.fail(
            f"No JLC credential at {store} — pair an account through the jlc-bridge "
            "extension first (dubIS Preferences -> Sign in to JLC)."
        )
    client = JlcpcbClient(sessions_file=store)
    accounts = jlc_session.list_public_sessions(store)
    if not accounts:
        pytest.fail(f"{store} holds no accounts — pair one before running -m live.")
    result = client.fetch_library(accounts[0]["account"])
    assert result["records"], "JLC returned an empty library"
    first = result["records"][0]
    assert first["lcsc"].startswith("C")
    assert first["qty"] >= 0
    print(f"JLC live: {len(result['records'])} rows for {result['account']}")
