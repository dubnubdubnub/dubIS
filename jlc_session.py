"""JLCPCB credential store, pairing nonces, and three-state session validation.

The transport and the auth *semantics* live here; pagination and record
mapping live in `jlcpcb_client.py`. Design: `docs/plans/2026-09-20-extension-credential-capture.md`.

Three things this module is careful about:

**Cookie presence is not evidence of login.** An anonymous request to the JLC
API mints a fresh `JLCPCB_SESSION_ID` and answers `{"code":460}`, so a poll
that waits for the cookie to *appear* succeeds instantly and harvests an
anonymous session, every time. Validity is therefore an API answer
(`code != 460`), never a cookie-name check — the same class of mistake as
DigiKey's `"dkuhint" in cookie_names` heuristic (`digikey_session.py:74-81`),
except here the naive version never works at all.

**Validation is three-state**, mirroring `digikey_session.validate_session_http`
(`digikey_session.py:208-260`): `code 460` is expiry, `code 200` is a live
session, and anything that stops us from *reading* an answer — a 403, a 5xx, a
socket error, unparseable JSON — is `INCONCLUSIVE`. A probe that could not run
must never invalidate a stored credential.

**Nothing here ever hands a cookie back to a caller.** `public_session()` is
the single projection every route response is built from, so a credential can
only leak by someone deliberately bypassing it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from dubis_errors import DistributorError, DistributorTimeout
from secret_store import write_private_json

logger = logging.getLogger(__name__)

PROVIDER = "jlcpcb"

LIBRARY_URL = (
    "https://jlcpcb.com/api/overseas-smt-component-order-platform/v1"
    "/overseasSmtComponentOrder/myLibrary/getCustomerComponentStock"
)

#: JLC application-level codes carried in the JSON envelope's `code` field.
#: 200 = answered, 460 = not signed in (served as HTTP 200, hence the check).
CODE_OK = 200
CODE_NOT_LOGGED_IN = 460

#: `pageSize` above this is refused outright: `{"code":200,"msg":"pageSize cannot exceed 100"}`.
MAX_PAGE_SIZE = 100

#: Only these cookies are ever stored. The extension already filters at the
#: source (threat-model rule 6); this is the server-side half of the same rule,
#: so a buggy or hostile sender cannot park an arbitrary jar in the data dir.
SESSION_COOKIE_NAMES = ("JLCPCB_SESSION_ID",)
COOKIE_DOMAIN_SUFFIX = "jlcpcb.com"

#: Three states of `validate()`. Never collapse INCONCLUSIVE into EXPIRED.
VALID = "valid"
EXPIRED = "expired"
INCONCLUSIVE = "inconclusive"

#: Pairing-nonce lifetime. Ten minutes, because this TTL spans a *human*
#: login, not a machine handshake: dubIS displays the nonce, the user pastes it
#: into the extension popup, signs in to JLC (autofill, possibly SSO, possibly
#: a 2FA detour), and only then does the extension's poll succeed and the POST
#: arrive. The original design put the nonce in the opened tab's URL, where a
#: 60s TTL would have been right -- but reading a tab's URL needs the `tabs`
#: permission or a content script, both forbidden by threat-model rules 1 and
#: 2, so the paste-in flow is what exists. Still single-use: length is not the
#: property that makes this safe, one-shot redemption is.
NONCE_TTL_SECONDS = 600

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Indirection so tests can drive expiry deterministically instead of sleeping.
_monotonic = time.monotonic

# In-memory ONLY, by design: a nonce that outlived the process it was minted in
# would be a durable bearer token for pushing credentials at this server.
_nonces: dict[str, float] = {}
_nonce_lock = threading.Lock()


# ── Pairing nonces ───────────────────────────────────────────────────────────


def mint_nonce(ttl: float = NONCE_TTL_SECONDS) -> str:
    """Mint a single-use pairing nonce valid for *ttl* seconds."""
    nonce = secrets.token_urlsafe(24)
    with _nonce_lock:
        _prune_locked()
        _nonces[nonce] = _monotonic() + ttl
    return nonce


def consume_nonce(nonce: str) -> bool:
    """Redeem *nonce*. True exactly once, for an unexpired nonce."""
    if not nonce:
        return False
    with _nonce_lock:
        _prune_locked()
        return _nonces.pop(nonce, None) is not None


def pending_nonce_count() -> int:
    """How many unexpired nonces are outstanding (diagnostics/tests)."""
    with _nonce_lock:
        _prune_locked()
        return len(_nonces)


def _prune_locked() -> None:
    now = _monotonic()
    for nonce, expiry in list(_nonces.items()):
        if expiry <= now:
            del _nonces[nonce]


# ── Cookies ──────────────────────────────────────────────────────────────────


def filter_cookies(cookies: Any) -> list[dict[str, str]]:
    """Keep only the named JLC session cookies, normalized to name/value/domain.

    Anything else a sender includes — other cookies, extra attributes — is
    dropped rather than stored (threat-model rule 6).
    """
    kept: list[dict[str, str]] = []
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "")
        if name not in SESSION_COOKIE_NAMES or not value:
            continue
        if domain and not domain.lstrip(".").endswith(COOKIE_DOMAIN_SUFFIX):
            continue
        kept.append({"name": name, "value": value, "domain": domain or f".{COOKIE_DOMAIN_SUFFIX}"})
    return kept


def cookie_header(cookies: Any) -> str:
    """`"a=1; b=2"` from a cookie list. Empty string when there is nothing to send."""
    pairs = [
        f"{c['name']}={c['value']}"
        for c in (cookies or [])
        if isinstance(c, dict) and c.get("name") and c.get("value")
    ]
    return "; ".join(pairs)


# ── Transport ────────────────────────────────────────────────────────────────


def library_url(page_num: int = 1, page_size: int = 1, keyword: str = "") -> str:
    """The private-library URL for one page."""
    if page_size > MAX_PAGE_SIZE:
        raise ValueError(f"JLC pageSize caps at {MAX_PAGE_SIZE} (asked for {page_size})")
    query = urllib.parse.urlencode({"pageNum": page_num, "pageSize": page_size, "keyWord": keyword})
    return f"{LIBRARY_URL}?{query}"


def fetch_page(
    cookies: Any,
    *,
    page_num: int = 1,
    page_size: int = 1,
    keyword: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """GET one page of the private library and return the parsed JSON envelope.

    Plain `urllib` on purpose: JLC puts no bot wall on this path (verified — a
    default `curl/8.x` User-Agent reaches it and gets a clean application-level
    `{"code":460}`), so unlike DigiKey there is nothing here that needs a real
    browser, CDP or WebView2.

    Raises `DistributorTimeout` / `DistributorError` for every case where no
    envelope could be read. Callers decide what "could not read" means: for
    `validate()` it is INCONCLUSIVE, for a library fetch it is a failed fetch.
    """
    url = library_url(page_num=page_num, page_size=page_size, keyword=keyword)
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    header = cookie_header(cookies)
    if header:
        headers["Cookie"] = header
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise DistributorError(
            f"JLC library request failed: HTTP {exc.code}", provider=PROVIDER
        ) from exc
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        raise DistributorTimeout(
            f"JLC library request could not be completed: {exc}", provider=PROVIDER
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise DistributorError(
            f"JLC library returned unparseable JSON: {exc}", provider=PROVIDER
        ) from exc
    if not isinstance(payload, dict):
        raise DistributorError("JLC library returned a non-object JSON body", provider=PROVIDER)
    return payload


# ── Validation ───────────────────────────────────────────────────────────────


def validate(cookies: Any, *, timeout: float = 15.0) -> dict[str, Any]:
    """Three-state probe of whether *cookies* are a live JLC session.

    Returns `{"state", "account", "total", "message"}` where `state` is one of
    `VALID` / `EXPIRED` / `INCONCLUSIVE`:

    - `EXPIRED`  — the envelope said `code 460` (not signed in), or there were
      no cookies to send at all. The only two definitive negatives.
    - `VALID`    — `code 200`. `account` is the `customerCode` every row
      carries (empty when the library is empty — there is no row to read it
      from), `total` the library's item count.
    - `INCONCLUSIVE` — nothing could be read: transport failure, a 403/5xx,
      unparseable JSON, or an application code we do not recognize. Callers
      must KEEP the session; a probe that could not run is not an expiry.
    """
    if not cookie_header(cookies):
        return {
            "state": EXPIRED,
            "account": "",
            "total": 0,
            "message": "No JLC session cookie to validate",
        }
    try:
        payload = fetch_page(cookies, page_num=1, page_size=1, timeout=timeout)
    except DistributorError as exc:
        logger.debug("JLC validate inconclusive: %s", exc)
        return {"state": INCONCLUSIVE, "account": "", "total": 0, "message": str(exc)}

    code = payload.get("code")
    msg = str(payload.get("msg") or "")
    if code == CODE_NOT_LOGGED_IN:
        return {"state": EXPIRED, "account": "", "total": 0, "message": msg or "Not signed in"}
    if code != CODE_OK:
        return {
            "state": INCONCLUSIVE,
            "account": "",
            "total": 0,
            "message": f"Unrecognized JLC response code {code!r}: {msg}",
        }

    data = payload.get("data") or {}
    rows = data.get("list") or []
    account = ""
    for row in rows:
        if isinstance(row, dict) and row.get("customerCode"):
            account = str(row["customerCode"])
            break
    try:
        total = int(data.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    return {"state": VALID, "account": account, "total": total, "message": msg or "Signed in"}


# ── Credential store ─────────────────────────────────────────────────────────


def now_iso() -> str:
    """UTC timestamp in the store's format (`2026-09-20T02:04:59Z`)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_sessions(path: str | None) -> dict[str, dict[str, Any]]:
    """Read the store. Missing file is `{}`; a corrupt one is `{}` plus a warning.

    Corruption is not fatal here on purpose: the store is a cache of
    credentials the user can re-push in one click, and a startup route reads
    it. Refusing to start over a truncated JSON file would be the worse
    failure.
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Unreadable JLC session store %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("JLC session store %s is not an object — ignoring", path)
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_sessions(path: str, sessions: dict[str, dict[str, Any]]) -> None:
    """Write the store `0600` (threat-model rule 8)."""
    write_private_json(path, sessions)


def store_session(
    path: str,
    *,
    account: str,
    cookies: Any,
    label: str = "",
    last_ok: str | None = None,
) -> dict[str, Any]:
    """Upsert one account's credential. Returns the *public* projection.

    Re-pairing an account keeps its original `added_at` and its existing label
    when the caller supplies none — the extension re-posts a fresh cookie
    whenever the user is signed in anyway, and that self-heal must not look
    like a brand-new session each time.
    """
    account = str(account or "").strip()
    if not account:
        raise ValueError("JLC session needs an account number")
    kept = filter_cookies(cookies)
    if not kept:
        raise ValueError("No JLC session cookie in the submitted cookies")
    sessions = load_sessions(path)
    existing = sessions.get(account) or {}
    entry = {
        "label": str(label or existing.get("label") or account),
        "cookies": kept,
        "added_at": str(existing.get("added_at") or now_iso()),
        "last_ok": last_ok or now_iso(),
    }
    sessions[account] = entry
    save_sessions(path, sessions)
    return public_session(account, entry)


def remove_session(path: str, account: str) -> bool:
    """Revoke one account. True when something was removed."""
    sessions = load_sessions(path)
    if account not in sessions:
        return False
    del sessions[account]
    save_sessions(path, sessions)
    return True


def get_cookies(path: str | None, account: str) -> list[dict[str, str]]:
    """The stored cookies for *account* — the ONLY read-back of a credential,
    and internal: no route returns this."""
    entry = load_sessions(path).get(account) or {}
    return filter_cookies(entry.get("cookies"))


def touch_last_ok(path: str, account: str) -> None:
    """Record that *account*'s credential just worked."""
    sessions = load_sessions(path)
    entry = sessions.get(account)
    if not entry:
        return
    entry["last_ok"] = now_iso()
    save_sessions(path, sessions)


def public_session(account: str, entry: dict[str, Any]) -> dict[str, str]:
    """The only shape that leaves this module for a caller: never a cookie.

    Precedent: `mouser_client.get_api_key_status`'s `{"configured": bool}`
    (`mouser_client.py:241`). Status answers *which* account and *when*, never
    *what* the credential is.
    """
    return {
        "account": str(account),
        "label": str(entry.get("label") or account),
        "added_at": str(entry.get("added_at") or ""),
        "last_ok": str(entry.get("last_ok") or ""),
    }


def list_public_sessions(path: str | None) -> list[dict[str, str]]:
    """Every stored account, cookie-free, sorted by account."""
    sessions = load_sessions(path)
    return [public_session(a, sessions[a]) for a in sorted(sessions)]


def check_session(path: str | None) -> dict[str, Any]:
    """Truthful startup status. NEVER raises, NEVER touches the network.

    The frontend calls distributor session routes on *every* startup, and a
    raise there has already produced a 500 twice (CLAUDE.md Traps). So this
    answers from the store alone: whether any credential is held, for which
    accounts, and when each last worked. Whether a credential is still live is
    what `validate()` is for, on the paths that actually need to know — making
    startup wait on a JLC round trip would trade a 500 for a hang.
    """
    try:
        accounts = list_public_sessions(path)
    except Exception as exc:  # noqa: BLE001 — a startup route must not 500
        logger.warning("JLC session check failed: %s", exc)
        return {
            "logged_in": False,
            "supported": True,
            "accounts": [],
            "message": f"Could not read the JLC session store: {exc}",
        }
    if not accounts:
        return {
            "logged_in": False,
            "supported": True,
            "accounts": [],
            "message": "No JLC account paired",
        }
    return {
        "logged_in": True,
        "supported": True,
        "accounts": accounts,
        "message": f"{len(accounts)} JLC account(s) paired",
    }


def store_path(base_dir: str) -> str:
    """`<data_dir>/jlc_sessions.json`."""
    return os.path.join(base_dir, "jlc_sessions.json")
