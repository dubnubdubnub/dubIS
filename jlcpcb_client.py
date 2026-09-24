"""JLCPCB private-parts-library client — pairing, credential intake, library read.

Deliberately NOT a `BaseProductClient`. That ABC is built around
`_fetch_raw(identifier) -> one product`, with a per-identifier session cache;
this client reads a *listing* — the whole of one account's JLC warehouse, paged
— keyed by an account rather than a part, and there is no "product not found"
answer to cache. Forcing it into `fetch_product()` would have meant an
identifier that is not a part number and a cache whose entries are whole
libraries. What it does keep from the house conventions: a `provider` class
attribute, and typed `dubis_errors` exceptions rather than `None` for failure
(`DistributorAuthError` -> 401, `DistributorError` -> 502, via
`server/errors.py`), so a caller can tell "your session expired" from "JLC was
unreachable".

Plain `urllib` throughout (see `jlc_session.fetch_page`): JLC has no bot wall
on this path, so no `browser_page`, no WebView2, no CDP.

Design: `docs/plans/2026-09-20-extension-credential-capture.md`.
"""

from __future__ import annotations

import logging
from typing import Any

import jlc_session
from domain.schema import INVENTORY_FIELDS
from dubis_errors import DistributorAuthError, DistributorError, NotFoundError

logger = logging.getLogger(__name__)

#: Library row field -> dubIS inventory record field. `componentCode` is the
#: LCSC C-number, which is dubIS's primary key, so it leads.
FIELD_MAP = {
    "componentCode": "lcsc",
    "componentModel": "mpn",
    "componentBrand": "manufacturer",
    "componentSpecification": "package",
    "description": "description",
    "componentType": "section",
}

#: JLC reports several stock buckets; this is the one that means "in your
#: warehouse". `overseasStockCount` / `postStockCount` / `idleStockCount` were
#: all zero across every row of the observed library and are not mapped.
QTY_FIELD = "privateStockCount"

#: Safety valve on the pagination loop: 100 rows a page, so this is 50k parts.
#: Hitting it means the stop conditions stopped working, which is a bug to
#: raise on, not a loop to keep running.
MAX_PAGES = 500


def _blank_record() -> dict[str, Any]:
    """An inventory-shaped record at its schema defaults.

    Built from `domain/schema.py`'s `INVENTORY_FIELDS` rather than typed out,
    so a field added to the inventory record flows through here instead of
    silently missing from every JLC row (and failing `InventoryItemModel`
    validation at the route).
    """
    record: dict[str, Any] = {}
    for f in INVENTORY_FIELDS:
        if not f.to_js:
            continue
        record[f.py_key] = list(f.default) if isinstance(f.default, list) else f.default
    return record


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def library_record(row: dict[str, Any]) -> dict[str, Any]:
    """One JLC library row -> one dubIS-shaped inventory record.

    `unit_price` and `ext_price` stay at their defaults: the library response
    carries **no price field at all**. Inventing one (from a catalog lookup, a
    last-purchase price, anything) would put a number in front of the user that
    JLC never quoted — the same failure the cart planner refuses to commit
    (CLAUDE.md, "A cart line prices only from a recorded quote"). Pricing these
    rows is a separate, deliberate step against `lcsc_client` or the purchase
    ledger.
    """
    record = _blank_record()
    for src, dest in FIELD_MAP.items():
        value = row.get(src)
        record[dest] = "" if value is None else str(value)
    record["qty"] = _as_int(row.get(QTY_FIELD))
    return record


class JlcpcbClient:
    """Owns the JLC credential store and the private-library read."""

    provider = "jlcpcb"

    def __init__(self, sessions_file: str | None = None) -> None:
        self._sessions_file = sessions_file

    # ── Pairing + credential intake ───────────────────────────────────────

    def mint_pairing_nonce(self) -> dict[str, Any]:
        """Mint the single-use nonce the extension must present.

        Without it the extension could be pointed at an attacker's "dubIS", and
        a send could fire without a user starting one (threat-model rule 3).
        """
        return {"nonce": jlc_session.mint_nonce(), "ttl": int(jlc_session.NONCE_TTL_SECONDS)}

    def receive_session(
        self,
        nonce: str,
        account: str = "",
        cookies: Any = None,
        label: str = "",
    ) -> dict[str, Any]:
        """Consume *nonce*, validate *cookies* against JLC, and store them.

        Only a `VALID` probe stores. An `INCONCLUSIVE` one is a 502, not an
        accept: the three-state rule says never to *invalidate* on a probe that
        could not run, and the mirror of that is never to *enshrine* an
        unverified credential either — an anonymous JLC request mints a session
        cookie all by itself, so "we could not check" and "this is junk" are
        indistinguishable here.

        The account is whatever the validation call resolved (`customerCode`),
        not what the sender claimed; the claimed value is a fallback only for
        an empty library, which has no row to read it from. That is what makes
        multi-account bookkeeping automatic — the user never declares which
        account they just signed into.
        """
        self._require_store()
        if not jlc_session.consume_nonce(nonce):
            raise DistributorAuthError(
                "Pairing nonce is unknown or expired — click Sign in again to start a new pairing.",
                provider=self.provider,
            )
        kept = jlc_session.filter_cookies(cookies)
        if not kept:
            raise ValueError(
                "No JLC session cookie in the request "
                f"(expected one of {', '.join(jlc_session.SESSION_COOKIE_NAMES)})"
            )
        result = jlc_session.validate(kept)
        state = result["state"]
        if state == jlc_session.EXPIRED:
            raise DistributorAuthError(
                f"JLC rejected the submitted session: {result['message']}",
                provider=self.provider,
            )
        if state != jlc_session.VALID:
            raise DistributorError(
                f"Could not verify the submitted JLC session: {result['message']}",
                provider=self.provider,
            )
        resolved = result["account"] or str(account or "").strip()
        if not resolved:
            raise ValueError(
                "JLC accepted the session but returned no customerCode "
                "(empty library) and no account number was supplied"
            )
        stored = jlc_session.store_session(
            self._sessions_file, account=resolved, cookies=kept, label=label
        )
        logger.info("Stored JLC session for account %s (%d items)", resolved, result["total"])
        return {**stored, "item_count": int(result["total"]), "state": jlc_session.VALID}

    def list_sessions(self) -> dict[str, Any]:
        """Startup-safe status: which accounts are paired, never a cookie."""
        return jlc_session.check_session(self._sessions_file)

    def check_session(self) -> dict[str, Any]:
        """Alias of `list_sessions()` — the name the other clients use."""
        return self.list_sessions()

    def revoke_session(self, account: str) -> dict[str, Any]:
        """Forget one account's credential (threat-model rule 7)."""
        self._require_store()
        account = str(account or "").strip()
        if not jlc_session.remove_session(self._sessions_file, account):
            raise NotFoundError(f"No stored JLC session for account {account!r}")
        return {
            "account": account,
            "revoked": True,
            "accounts": jlc_session.list_public_sessions(self._sessions_file),
        }

    # ── Library read ──────────────────────────────────────────────────────

    def resolve_account(self, account: str = "") -> str:
        """Which account a library read is about.

        An explicit account wins. With exactly one paired account, that one is
        implied. With several and no choice made, refuse by name rather than
        picking — returning the wrong warehouse silently is worse than an error.
        """
        stored = jlc_session.load_sessions(self._sessions_file)
        account = str(account or "").strip()
        if account:
            if account not in stored:
                raise DistributorAuthError(
                    f"No stored JLC session for account {account!r} — pair it first.",
                    provider=self.provider,
                )
            return account
        if not stored:
            raise DistributorAuthError(
                "No JLC account is paired — click Sign in to pair one.",
                provider=self.provider,
            )
        if len(stored) > 1:
            raise ValueError(
                "Several JLC accounts are paired; name one with ?account=: "
                + ", ".join(sorted(stored))
            )
        return next(iter(stored))

    def fetch_library(self, account: str = "", *, page_size: int = jlc_session.MAX_PAGE_SIZE) -> dict[str, Any]:
        """Every row of one account's private library, as dubIS records."""
        self._require_store()
        resolved = self.resolve_account(account)
        cookies = jlc_session.get_cookies(self._sessions_file, resolved)
        if not cookies:
            raise DistributorAuthError(
                f"The stored JLC session for {resolved} has no usable cookie — pair it again.",
                provider=self.provider,
            )
        rows, total = self._fetch_rows(cookies, page_size=page_size)
        jlc_session.touch_last_ok(self._sessions_file, resolved)
        return {
            "account": resolved,
            "total": total or len(rows),
            "records": [library_record(r) for r in rows],
        }

    def _fetch_rows(self, cookies: list[dict[str, str]], *, page_size: int) -> tuple[list[dict], int]:
        """Page through the library until JLC stops handing out rows.

        Stops on the first of: an empty page, the reported page count, or the
        reported total. Three conditions rather than one because the envelope
        is PageHelper-shaped and any of `pages`/`total` can be absent — and an
        unbounded `while True` against a paginated remote is how a fetch turns
        into an infinite loop.
        """
        page_size = max(1, min(int(page_size), jlc_session.MAX_PAGE_SIZE))
        rows: list[dict] = []
        total = 0
        page = 1
        while page <= MAX_PAGES:
            payload = jlc_session.fetch_page(cookies, page_num=page, page_size=page_size)
            code = payload.get("code")
            if code == jlc_session.CODE_NOT_LOGGED_IN:
                raise DistributorAuthError(
                    "The stored JLC session has expired — sign in again to refresh it.",
                    provider=self.provider,
                )
            if code != jlc_session.CODE_OK:
                raise DistributorError(
                    f"JLC library page {page} failed: {payload.get('msg') or code!r}",
                    provider=self.provider,
                )
            data = payload.get("data") or {}
            chunk = [r for r in (data.get("list") or []) if isinstance(r, dict)]
            rows.extend(chunk)
            total = _as_int(data.get("total")) or total
            pages = _as_int(data.get("pages"))
            if not chunk:
                break
            if pages and page >= pages:
                break
            if total and len(rows) >= total:
                break
            page += 1
        else:
            raise DistributorError(
                f"JLC library paging exceeded {MAX_PAGES} pages — refusing to keep fetching.",
                provider=self.provider,
            )
        return rows, total

    # ── Internals ─────────────────────────────────────────────────────────

    def _require_store(self) -> None:
        if not self._sessions_file:
            raise RuntimeError("JLC session store path is not configured")
