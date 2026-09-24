"""Unit tests for the JLC credential store, pairing nonces, and validation.

Every network boundary is mocked — nothing here reaches jlcpcb.com, so the
whole file runs in the default (non-``live``) suite. The live counterpart is
``test_jlcpcb_client.py::test_live_library_fetch``.

Design: ``docs/plans/2026-09-20-extension-credential-capture.md``.
"""

from __future__ import annotations

import io
import json
import os
import stat
import urllib.error
from unittest.mock import patch

import pytest

import jlc_session

COOKIE = {"name": "JLCPCB_SESSION_ID", "value": "s3cr3t-uuid", "domain": ".jlcpcb.com"}
ACCOUNT = "12625901A"


@pytest.fixture
def store(tmp_path):
    return jlc_session.store_path(str(tmp_path))


@pytest.fixture(autouse=True)
def _clean_nonces():
    """Nonces are process-global; keep tests from leaking into each other."""
    jlc_session._nonces.clear()
    yield
    jlc_session._nonces.clear()


def _envelope(code=200, rows=None, total=0, msg="success"):
    return {"code": code, "msg": msg, "data": {"total": total, "list": rows or [], "pages": 1}}


def _patched_urlopen(payload: dict):
    """Patch urllib so `fetch_page` reads *payload* as the JSON body."""
    body = io.BytesIO(json.dumps(payload).encode())
    body.__enter__ = lambda self=body: self  # type: ignore[assignment]
    body.__exit__ = lambda *a: False  # type: ignore[assignment]
    return patch("urllib.request.urlopen", return_value=body)


# ── Pairing nonces ───────────────────────────────────────────────────────────


class TestNonces:
    def test_minted_nonce_is_redeemable_once(self):
        nonce = jlc_session.mint_nonce()
        assert jlc_session.consume_nonce(nonce) is True
        # Single-use is the property that makes the pasted nonce safe: a replay
        # of the same POST cannot store a second credential.
        assert jlc_session.consume_nonce(nonce) is False

    def test_unknown_nonce_is_refused(self):
        jlc_session.mint_nonce()
        assert jlc_session.consume_nonce("not-a-real-nonce") is False

    def test_empty_nonce_is_refused(self):
        assert jlc_session.consume_nonce("") is False

    def test_nonce_expires(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(jlc_session, "_monotonic", lambda: clock[0])
        nonce = jlc_session.mint_nonce(ttl=600)
        clock[0] += 599
        assert jlc_session.pending_nonce_count() == 1
        clock[0] += 2
        assert jlc_session.consume_nonce(nonce) is False
        assert jlc_session.pending_nonce_count() == 0

    def test_default_ttl_spans_a_human_login(self):
        # 10 minutes, not 60s: dubIS displays the nonce, the user pastes it into
        # the extension popup, signs in to JLC (autofill/SSO/2FA), and only then
        # does the extension's poll succeed and the POST arrive. A one-minute
        # TTL expires mid-login every time. Reading the nonce out of the tab URL
        # (which would have justified 60s) needs the `tabs` permission, which
        # threat-model rules 1 and 2 forbid.
        assert jlc_session.NONCE_TTL_SECONDS == 600

    def test_nonces_are_distinct(self):
        assert len({jlc_session.mint_nonce() for _ in range(25)}) == 25

    def test_expired_nonces_do_not_accumulate(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr(jlc_session, "_monotonic", lambda: clock[0])
        for _ in range(5):
            jlc_session.mint_nonce(ttl=10)
        clock[0] += 11
        jlc_session.mint_nonce(ttl=10)
        assert jlc_session.pending_nonce_count() == 1


# ── Cookie filtering ─────────────────────────────────────────────────────────


class TestFilterCookies:
    def test_keeps_only_the_named_session_cookie(self):
        kept = jlc_session.filter_cookies([
            COOKIE,
            {"name": "_ga", "value": "GA1.2.3", "domain": ".jlcpcb.com"},
        ])
        assert kept == [COOKIE]

    def test_drops_a_cookie_from_another_domain(self):
        assert jlc_session.filter_cookies([
            {"name": "JLCPCB_SESSION_ID", "value": "x", "domain": ".evil.example"},
        ]) == []

    def test_drops_the_chrome_cookie_attributes(self):
        # The extension sends whatever `chrome.cookies.getAll` returned
        # (secure/httpOnly booleans, an expirationDate number); only
        # name/value/domain are ever stored.
        kept = jlc_session.filter_cookies([
            {**COOKIE, "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1.0},
        ])
        assert kept == [COOKIE]

    def test_ignores_non_dict_entries_and_empty_values(self):
        assert jlc_session.filter_cookies(["nope", None, {"name": "JLCPCB_SESSION_ID"}]) == []


# ── Three-state validation ───────────────────────────────────────────────────


class TestValidate:
    def test_code_460_is_expired(self):
        with _patched_urlopen({"code": 460, "msg": "not login"}):
            result = jlc_session.validate([COOKIE])
        assert result["state"] == jlc_session.EXPIRED

    def test_code_200_is_valid_and_resolves_the_account(self):
        rows = [{"customerCode": ACCOUNT, "componentCode": "C1525"}]
        with _patched_urlopen(_envelope(rows=rows, total=191)):
            result = jlc_session.validate([COOKIE])
        assert result["state"] == jlc_session.VALID
        assert result["account"] == ACCOUNT
        assert result["total"] == 191

    def test_network_error_is_inconclusive_never_expired(self):
        # The whole point of three states: a probe that could not run must not
        # invalidate a stored credential (digikey_session.py:208-260).
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            result = jlc_session.validate([COOKIE])
        assert result["state"] == jlc_session.INCONCLUSIVE

    def test_http_403_is_inconclusive(self):
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            assert jlc_session.validate([COOKIE])["state"] == jlc_session.INCONCLUSIVE

    def test_http_500_is_inconclusive(self):
        err = urllib.error.HTTPError("u", 500, "Server Error", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            assert jlc_session.validate([COOKIE])["state"] == jlc_session.INCONCLUSIVE

    def test_timeout_is_inconclusive(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            assert jlc_session.validate([COOKIE])["state"] == jlc_session.INCONCLUSIVE

    def test_unparseable_body_is_inconclusive(self):
        body = io.BytesIO(b"<html>not json</html>")
        body.__enter__ = lambda self=body: self  # type: ignore[assignment]
        body.__exit__ = lambda *a: False  # type: ignore[assignment]
        with patch("urllib.request.urlopen", return_value=body):
            assert jlc_session.validate([COOKIE])["state"] == jlc_session.INCONCLUSIVE

    def test_unrecognized_code_is_inconclusive(self):
        with _patched_urlopen({"code": 999, "msg": "who knows"}):
            assert jlc_session.validate([COOKIE])["state"] == jlc_session.INCONCLUSIVE

    def test_no_cookies_is_expired(self):
        assert jlc_session.validate([])["state"] == jlc_session.EXPIRED

    def test_valid_with_an_empty_library_has_no_account(self):
        # Nothing to read customerCode from; the caller falls back to the
        # account the sender claimed.
        with _patched_urlopen(_envelope(rows=[], total=0)):
            result = jlc_session.validate([COOKIE])
        assert result["state"] == jlc_session.VALID
        assert result["account"] == ""

    def test_the_request_carries_the_cookie_and_the_page_size_of_one(self):
        seen = {}

        def _capture(req, timeout=None):
            seen["url"] = req.full_url
            seen["cookie"] = req.get_header("Cookie")
            body = io.BytesIO(json.dumps(_envelope()).encode())
            body.__enter__ = lambda self=body: self
            body.__exit__ = lambda *a: False
            return body

        with patch("urllib.request.urlopen", side_effect=_capture):
            jlc_session.validate([COOKIE])
        assert "pageSize=1" in seen["url"]
        assert "getCustomerComponentStock" in seen["url"]
        assert seen["cookie"] == "JLCPCB_SESSION_ID=s3cr3t-uuid"


def test_page_size_above_100_is_refused():
    # `{"code":200,"msg":"pageSize cannot exceed 100"}` upstream; caught here
    # so a caller gets a ValueError instead of a silently empty page.
    with pytest.raises(ValueError, match="100"):
        jlc_session.library_url(page_num=1, page_size=250)


# ── Credential store ─────────────────────────────────────────────────────────


class TestStore:
    def test_round_trip(self, store):
        public = jlc_session.store_session(
            store, account=ACCOUNT, cookies=[COOKIE], label="impossible_hardware"
        )
        assert public["account"] == ACCOUNT
        assert public["label"] == "impossible_hardware"
        assert public["added_at"] and public["last_ok"]
        assert jlc_session.get_cookies(store, ACCOUNT) == [COOKIE]

    def test_store_file_is_0600(self, store):
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE])
        assert stat.S_IMODE(os.stat(store).st_mode) == 0o600

    def test_rewriting_a_loose_file_tightens_it(self, store):
        jlc_session.save_sessions(store, {})
        os.chmod(store, 0o644)
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE])
        assert stat.S_IMODE(os.stat(store).st_mode) == 0o600

    def test_public_projection_never_carries_a_cookie(self, store):
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE])
        listed = jlc_session.list_public_sessions(store)
        assert listed and "cookies" not in listed[0]
        assert COOKIE["value"] not in json.dumps(listed)

    def test_repairing_keeps_added_at_and_label(self, store):
        first = jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE], label="bench")
        second = jlc_session.store_session(
            store, account=ACCOUNT, cookies=[{**COOKIE, "value": "fresh"}]
        )
        assert second["added_at"] == first["added_at"]
        assert second["label"] == "bench"
        assert jlc_session.get_cookies(store, ACCOUNT)[0]["value"] == "fresh"

    def test_two_accounts_coexist(self, store):
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE])
        jlc_session.store_session(store, account="99999B", cookies=[{**COOKIE, "value": "b"}])
        assert [s["account"] for s in jlc_session.list_public_sessions(store)] == [
            ACCOUNT, "99999B",
        ]

    def test_remove_session(self, store):
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE])
        assert jlc_session.remove_session(store, ACCOUNT) is True
        assert jlc_session.remove_session(store, ACCOUNT) is False
        assert jlc_session.list_public_sessions(store) == []

    def test_store_without_an_account_raises(self, store):
        with pytest.raises(ValueError):
            jlc_session.store_session(store, account="", cookies=[COOKIE])

    def test_store_without_a_session_cookie_raises(self, store):
        with pytest.raises(ValueError):
            jlc_session.store_session(store, account=ACCOUNT, cookies=[{"name": "_ga", "value": "1"}])

    def test_touch_last_ok(self, store):
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE], last_ok="2000-01-01T00:00:00Z")
        jlc_session.touch_last_ok(store, ACCOUNT)
        assert jlc_session.list_public_sessions(store)[0]["last_ok"] != "2000-01-01T00:00:00Z"

    def test_missing_file_reads_as_empty(self, tmp_path):
        assert jlc_session.load_sessions(str(tmp_path / "nope.json")) == {}


# ── check_session: truthful, never raises ────────────────────────────────────


class TestCheckSession:
    def test_no_accounts(self, store):
        result = jlc_session.check_session(store)
        assert result["logged_in"] is False
        assert result["accounts"] == []
        assert result["supported"] is True

    def test_with_an_account(self, store):
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE])
        result = jlc_session.check_session(store)
        assert result["logged_in"] is True
        assert [a["account"] for a in result["accounts"]] == [ACCOUNT]

    def test_corrupt_store_does_not_raise(self, store):
        # The frontend hits distributor session routes on EVERY startup; a raise
        # here is the 500 that has already shipped twice (CLAUDE.md Traps).
        with open(store, "w", encoding="utf-8") as f:
            f.write("{not json at all")
        result = jlc_session.check_session(store)
        assert result["logged_in"] is False
        assert isinstance(result["message"], str)

    def test_unreadable_store_does_not_raise(self, store, monkeypatch):
        monkeypatch.setattr(
            jlc_session, "list_public_sessions",
            lambda _p: (_ for _ in ()).throw(RuntimeError("disk on fire")),
        )
        result = jlc_session.check_session(store)
        assert result["logged_in"] is False
        assert "disk on fire" in result["message"]

    def test_never_touches_the_network(self, store, monkeypatch):
        # Startup must not wait on a JLC round trip — a hang is no better than
        # the 500 this shape exists to prevent.
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: pytest.fail("check_session must not make a network call"),
        )
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE])
        assert jlc_session.check_session(store)["logged_in"] is True

    def test_status_carries_no_cookie(self, store):
        jlc_session.store_session(store, account=ACCOUNT, cookies=[COOKIE])
        assert COOKIE["value"] not in json.dumps(jlc_session.check_session(store))
