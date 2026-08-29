"""Mouser product-fetching client.

Two paths:
  - API: when an API key is configured, calls Mouser's Search API v2.
    This is the preferred path — clean JSON, no bot detection. Free tier
    is 1000 calls/day / 30/min, plenty for tooltip use.
  - Scrape: legacy HTML scraping of mouser.com product pages. Only used
    when no API key is set. Often blocked by Mouser's bot protection
    (DataDome) so we detect block pages and return None.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import browser_page
from base_client import BaseProductClient
from domain.packaging import carrier_of
from domain.product import build_product
from html_product_parser import (
    extract_attributes,
    extract_description,
    extract_image_url,
    extract_jsonld_product,
    extract_manufacturer,
    extract_mpn,
    extract_prices_from_jsonld,
    extract_stock_from_jsonld,
    extract_title,
)

logger = logging.getLogger(__name__)

_API_SEARCH_URL = "https://api.mouser.com/api/v2/search/partnumber"
_API_KEYWORD_URL = "https://api.mouser.com/api/v2/search/keyword"
_PRODUCT_URL = "https://www.mouser.com/ProductDetail/{}"
_SEARCH_URL = "https://www.mouser.com/c/?q={}"

# A Mouser part number is a numeric vendor prefix and a dash ("736-FGG0B305CLAD52",
# "81-GRM155R71H103KA88D"); anything else in that column is an MPN, which
# /ProductDetail/ does not resolve -- it 404s rather than searching. Getting
# this wrong only costs a wasted page load, since each path falls back to the
# other.
_MOUSER_PN_RE = re.compile(r"^\d{2,4}-\S")
_PRODUCT_LINK_RE = re.compile(
    r'href="(/(?:[a-z]{2}/)?ProductDetail/[^"\s]+)"', re.IGNORECASE,
)

# Mouser fires `loaded` and then fills the price table in, so the document has
# to be read a beat later or the ladder is simply absent. Measured against the
# real site; the value is a guess with margin, not a threshold.
_RENDER_SETTLE_S = 3.5

# Mouser publishes the carrier as an ordinary parametric attribute
# ("Packaging": "Cut Tape" / "Tape & Reel" / "MouseReel" / "Tray" / "Tube" /
# "Bulk"), both in the Search API's ProductAttributes and in the product
# page's spec table. Deliberately excludes a bare "Package" — on Mouser that
# is the footprint/case ("0402"), not the carrier.
_PACKAGING_ATTR_NAMES = frozenset({"packaging", "packaging type", "package type"})

# "Full Reel (Order in multiples of 3,000)" — the reel-anchored form is the
# only one we trust as a *reel* quantity, since a bare "order in multiples of
# 5" on a bulk part is an order multiple, not a reel.
_REEL_MULTIPLE_RE = re.compile(
    r"reel[^.]{0,100}?multiples?\s+of\s+([\d,]+)", re.IGNORECASE,
)
_ORDER_MULTIPLE_RE = re.compile(r"multiples?\s+of\s+([\d,]+)", re.IGNORECASE)

# "MouseReel ... $7.00" — the custom-reeling surcharge, rendered next to the
# MouseReel option on the product page. The Search API does not expose it.
_MOUSEREEL_FEE_RE = re.compile(
    r"MouseReel[^$]{0,160}\$\s*(\d+(?:\.\d+)?)", re.IGNORECASE,
)


def _clean_int(value: Any) -> int | None:
    """Best-effort positive int from Mouser's string-typed numeric fields.

    Mouser reports Min/Mult as strings ("1", "3,000"); anything unparseable or
    non-positive means "not published", which is None.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _packaging_name(attributes: list[dict[str, str]]) -> str:
    """Pull the carrier name out of an already-normalized attribute list."""
    for attr in attributes:
        if (attr.get("name") or "").strip().lower() in _PACKAGING_ATTR_NAMES:
            value = (attr.get("value") or "").strip()
            if value:
                return value
    return ""


# Mouser renders every price break in one `table.pricing-table`, split into
# per-packaging blocks by a `th.sub-heading` row:
#
#   Qty. | Unit Price | Ext. Price
#   -- Cut Tape / MouseReel(tm) --
#   1    | $0.10      | $0.10
#   10   | $0.015     | $0.15
#   -- Full Reel (Order in multiples of 10000) --
#   10,000 | $0.004   | $40.00
#
# That is the whole packaging model dubIS wants, already separated by the
# vendor: two carriers, two ladders, and a reel multiple written into the
# sub-heading. Worth parsing properly rather than scraping loose "qty $price"
# pairs out of the page, which cannot tell the two ladders apart and, because
# the cells are separated by markup, mostly matches nothing at all.
_PRICING_TABLE_RE = re.compile(
    r'<table[^>]*\bclass="[^"]*\bpricing-table\b[^"]*"[^>]*>(.*?)</table>',
    re.IGNORECASE | re.DOTALL,
)
_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.IGNORECASE | re.DOTALL)
_SUB_HEADING_RE = re.compile(
    r'<th[^>]*\bclass="[^"]*\bsub-heading\b[^"]*"[^>]*>(.*?)</th>',
    re.IGNORECASE | re.DOTALL,
)
_BREAK_QTY_RE = re.compile(
    r'<th[^>]*\bclass="[^"]*\bpricebreak-col\b[^"]*"[^>]*>(.*?)</th>',
    re.IGNORECASE | re.DOTALL,
)
_CELL_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
_WS_RE = re.compile(r"\s+")


def _cell_text(fragment: str) -> str:
    """Visible text of one table cell: tags out, entities decoded, spaces collapsed."""
    return _WS_RE.sub(" ", html_mod.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _packaging_heading(fragment: str) -> str:
    """A sub-heading as a packaging name, minus its footnote marker.

    Mouser hangs a dagger off "Cut Tape / MouseReel(tm) \u2020" pointing at a note
    further down the page. The name becomes the key an observation is stored
    and grouped under, so carrying a typographic mark into it would split one
    packaging into two the day Mouser renumbers its footnotes.
    """
    return _cell_text(fragment).rstrip(" \u2020\u2021*").strip()


def _parse_pricing_table(page_html: str) -> list[dict[str, Any]]:
    """Per-packaging price ladders from a Mouser product page.

    Returns ``[{"name": str, "prices": [{"qty": int, "price": float}, ...]}]``
    in page order, which puts the packaging Mouser defaults to first. Returns
    [] when the table is absent (a bot-block page, or a product with no
    published pricing) so the caller can fall back rather than treat an empty
    ladder as a real one.

    Rows before the first sub-heading are ignored: a table with prices but no
    packaging block has no carrier to attribute them to, and guessing one is
    exactly the invention the packaging model exists to prevent.
    """
    table = _PRICING_TABLE_RE.search(page_html)
    if not table:
        return []
    groups: list[dict[str, Any]] = []
    for row in _ROW_RE.findall(table.group(1)):
        heading = _SUB_HEADING_RE.search(row)
        if heading:
            name = _packaging_heading(heading.group(1))
            if name:
                groups.append({"name": name, "prices": []})
            continue
        if not groups:
            continue
        qty_cell = _BREAK_QTY_RE.search(row)
        if not qty_cell:
            continue
        qty = _clean_int(_cell_text(qty_cell.group(1)))
        if qty is None:
            continue
        # The first <td> is Unit Price; the second is Ext. Price, which is
        # unit x qty and would be a wildly wrong unit price if taken.
        cells = _CELL_RE.findall(row)
        if not cells:
            continue
        money = _MONEY_RE.search(_cell_text(cells[0]))
        if not money:
            continue
        try:
            price = float(money.group(1).replace(",", ""))
        except ValueError:
            continue
        groups[-1]["prices"].append({"qty": qty, "price": price})
    return [g for g in groups if g["prices"]]


class MouserClient(BaseProductClient):
    """Fetches and caches Mouser product details by part number."""

    provider = "mouser"

    def __init__(self, credentials_file: str | None = None) -> None:
        super().__init__()
        self._credentials_file = credentials_file
        # Created on first browser fetch and reused: one hidden window per
        # client, not one per part.
        self._page: browser_page.BrowserPage | None = None

    # ── API key persistence ───────────────────────────────────────────────

    def get_api_key(self) -> str | None:
        """Return the configured Mouser API key, or None if unset/unreadable."""
        if not self._credentials_file:
            return None
        try:
            with open(self._credentials_file, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read Mouser credentials: %s", exc)
            return None
        key = (data.get("api_key") or "").strip() if isinstance(data, dict) else ""
        return key or None

    def get_api_key_status(self) -> dict[str, bool]:
        """Return whether an API key is currently configured."""
        return {"configured": self.get_api_key() is not None}

    def set_api_key(self, key: str) -> None:
        """Persist a Mouser API key. Empty/whitespace clears the credentials."""
        if not self._credentials_file:
            raise RuntimeError("Mouser credentials file not configured")
        key = (key or "").strip()
        # Stale results from before the key change would be misleading.
        self.clear_cache()
        if not key:
            self.clear_api_key()
            return
        with open(self._credentials_file, "w", encoding="utf-8") as f:
            json.dump({"api_key": key}, f)

    def clear_api_key(self) -> None:
        """Remove the credentials file. Idempotent."""
        if not self._credentials_file:
            return
        self.clear_cache()
        try:
            os.remove(self._credentials_file)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to remove Mouser credentials: %s", exc)

    # ── Fetch ─────────────────────────────────────────────────────────────

    def _fetch_raw(self, part_number: str) -> dict[str, Any] | None:
        """Fetch Mouser product details. Uses the API when a key is configured,
        falls back to HTML scraping otherwise."""
        part_number = str(part_number).strip()
        if not part_number or not re.match(r"^[\w.\-/]{2,60}$", part_number):
            raise ValueError(f"Invalid Mouser part number: {part_number!r}")

        api_key = self.get_api_key()
        if api_key:
            return self._fetch_via_api(part_number, api_key)
        # No key: render the page in a real browser. Mouser refuses a plain
        # urllib request (verified from two networks -- it is the request that
        # is refused, not the address), so the legacy path below is kept only
        # for the container, where there is no browser to render with and
        # something is still better than an unconditional None.
        product = self._fetch_via_browser(part_number)
        if product is not None:
            return product
        return self._fetch_via_scrape(part_number)

    def _fetch_via_api(self, part_number: str, api_key: str) -> dict[str, Any] | None:
        # Try the partnumber endpoint first — fastest path for valid Mouser PNs.
        parts = self._call_api(
            _API_SEARCH_URL,
            {"SearchByPartRequest": {
                "mouserPartNumber": part_number,
                "partSearchOptions": "",
            }},
            api_key, part_number,
        )
        if parts:
            return self._normalize_api_part(parts[0], part_number)

        # No hit on partnumber. Fall back to keyword search, which matches
        # against MPNs and descriptions — handles the case where the user has
        # an MPN like "FGG.0B.305.CLAD52" in the Mouser column instead of the
        # Mouser PN "736-FGG0B305CLAD52".
        parts = self._call_api(
            _API_KEYWORD_URL,
            {"SearchByKeywordRequest": {
                "keyword": part_number,
                "records": 5,
                "startingRecord": 0,
                "searchOptions": "",
                "searchWithYourSignUpLanguage": "false",
            }},
            api_key, part_number,
        )
        if not parts:
            logger.debug("Mouser API: no keyword results for %s", part_number)
            return None

        # Pick the part whose MPN exactly matches the user's input (case-
        # insensitive). Falls back to the first result (Mouser's relevance
        # ranking) when nothing is an exact MPN match.
        target = part_number.strip().lower()
        best = next(
            (p for p in parts
             if (p.get("ManufacturerPartNumber") or "").strip().lower() == target),
            parts[0],
        )
        return self._normalize_api_part(best, part_number)

    def _call_api(
        self, url: str, body: dict[str, Any], api_key: str, part_number: str,
    ) -> list[dict[str, Any]] | None:
        """Hit a Mouser API endpoint. Returns the Parts list, or None on error."""
        full_url = f"{url}?apiKey={urllib.parse.quote(api_key, safe='')}"
        req = urllib.request.Request(
            full_url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning("Mouser API call failed for %s: %s", part_number, exc)
            return None

        errors = payload.get("Errors") or []
        if errors:
            messages = "; ".join(e.get("Message", "") for e in errors if isinstance(e, dict))
            logger.warning("Mouser API error for %s: %s", part_number, messages)
            return None

        results = payload.get("SearchResults") or {}
        return results.get("Parts") or []

    def _fetch_via_browser(self, part_number: str) -> dict[str, Any] | None:
        """Render the product page in a hidden window and parse it.

        Two ways in, because the column this string came from may hold either
        kind of identifier. A Mouser PN resolves at /ProductDetail/ directly; an
        MPN does not (that URL 404s rather than searching), so it goes through
        the search page and follows the first result. Whichever is tried first,
        the other is the fallback -- misjudging the format costs a page load,
        not the fetch.
        """
        if not browser_page.available():
            return None
        if self._page is None:
            self._page = browser_page.BrowserPage(title="Mouser")

        attempts = (
            [self._via_product_url, self._via_search]
            if _MOUSER_PN_RE.match(part_number)
            else [self._via_search, self._via_product_url]
        )
        for attempt in attempts:
            try:
                product = attempt(part_number)
            except (RuntimeError, AttributeError) as exc:
                logger.warning("Mouser browser fetch failed for %s: %s",
                               part_number, exc)
                return None
            if product is not None:
                return product
        return None

    def _via_product_url(self, part_number: str) -> dict[str, Any] | None:
        url = _PRODUCT_URL.format(urllib.parse.quote(part_number, safe=""))
        html = self._page.fetch_html(url, settle_s=_RENDER_SETTLE_S)
        return self._parse_product_page(html, part_number, url) if html else None

    def _via_search(self, part_number: str) -> dict[str, Any] | None:
        """Search, then open the first product the results offer.

        The results page carries price text but no JSON-LD and, when several
        products match, several interleaved ladders -- so it is never parsed as
        a product. It is only read for where to go next. An exact single match
        redirects straight to the product page, which is why the landing URL is
        checked before looking for a link at all.
        """
        search = _SEARCH_URL.format(urllib.parse.quote(part_number, safe=""))
        html = self._page.fetch_html(search, settle_s=_RENDER_SETTLE_S)
        if not html:
            return None
        landed = self._page.current_url() or ""
        if "/ProductDetail/" in landed:
            return self._parse_product_page(html, part_number, landed)
        link = _PRODUCT_LINK_RE.search(html)
        if not link:
            logger.debug("Mouser search for %s offered no product link", part_number)
            return None
        url = urllib.parse.urljoin("https://www.mouser.com", link.group(1))
        page = self._page.fetch_html(url, settle_s=_RENDER_SETTLE_S)
        return self._parse_product_page(page, part_number, url) if page else None

    def _fetch_via_scrape(self, part_number: str) -> dict[str, Any] | None:
        url = f"https://www.mouser.com/ProductDetail/{part_number}"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "dubIS/1.0",
                "Accept": "text/html",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                page_html = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Mouser fetch failed for %s: %s", part_number, exc)
            return None

        product = self._parse_product_page(page_html, part_number, url)
        if product is None:
            self._log_parse_diagnostics(page_html, part_number, url)
        return product

    # ── API normalization ─────────────────────────────────────────────────

    @staticmethod
    def _normalize_api_part(part: dict[str, Any], part_number: str) -> dict[str, Any]:
        """Convert a Mouser Search API Parts[i] dict to the tooltip schema."""
        prices: list[dict[str, Any]] = []
        for pb in part.get("PriceBreaks") or []:
            qty = pb.get("Quantity")
            raw_price = pb.get("Price")
            if not isinstance(qty, int) or not isinstance(raw_price, str):
                continue
            # Mouser returns price as "$37.55" or "37,55 €" depending on region.
            # Strip non-numeric prefix, accept either decimal separator.
            cleaned = re.sub(r"[^\d.,]", "", raw_price).replace(",", ".")
            try:
                prices.append({"qty": qty, "price": float(cleaned)})
            except ValueError:
                continue
        prices.sort(key=lambda p: p["qty"])

        # "500 In Stock" / "0" / "Available on Backorder" — pull leading digits.
        stock = 0
        avail = part.get("Availability") or ""
        m = re.match(r"\s*([\d,]+)", avail)
        if m:
            try:
                stock = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

        attributes = []
        for a in part.get("ProductAttributes") or []:
            name = (a.get("AttributeName") or "").strip()
            value = (a.get("AttributeValue") or "").strip()
            if name and value:
                attributes.append({"name": name, "value": value})

        # Packaging: Mouser's Search API carries the carrier as a
        # "Packaging" ProductAttribute, the order floor/step as Min/Mult, and
        # a `Reeling` boolean meaning "MouseReel is available for this part"
        # (availability of the service — *not* a claim that this packaging is
        # itself a reel, so it is exposed as its own key rather than used to
        # override the name-derived isReel).
        packaging_name = _packaging_name(attributes)
        order_multiple = _clean_int(part.get("Mult"))
        reeling = part.get("Reeling")
        packagings: list[dict[str, Any]] = []
        if packaging_name:
            packagings.append({
                "name": packaging_name,
                "partNumber": part.get("MouserPartNumber") or part_number,
                "minBuyQty": _clean_int(part.get("Min")),
                "orderMultiple": order_multiple,
                "reelingAvailable": None if reeling is None else bool(reeling),
                "prices": prices,
            })

        # A >1 order multiple is the reel quantity only when the carrier is
        # tape — Mouser also uses Mult for bulk pack sizes ("multiples of 5"),
        # which is not a reel of 5.
        reel_qty = (
            order_multiple
            if order_multiple and order_multiple > 1
            and carrier_of(packaging_name) == "tape"
            else None
        )

        title = (part.get("Description") or part.get("ManufacturerPartNumber")
                 or part_number)
        return build_product(
            product_code=part.get("MouserPartNumber") or part_number,
            title=title,
            manufacturer=part.get("Manufacturer") or "",
            mpn=part.get("ManufacturerPartNumber") or "",
            package="",
            description=part.get("Description") or "",
            stock=stock,
            prices=prices,
            image_url=part.get("ImagePath") or "",
            pdf_url=part.get("DataSheetUrl") or "",
            url=(
                part.get("ProductDetailUrl")
                or f"https://www.mouser.com/ProductDetail/{part_number}"
            ),
            category=part.get("Category") or "",
            subcategory="",
            attributes=attributes,
            provider="mouser",
            packagings=packagings,
            reel_qty=reel_qty,
            # The Search API publishes no MouseReel surcharge field — only the
            # product page renders it — so reel_fee stays None on this path.
            debug={
                "source": "api",
                "part_number": part_number,
                "raw": part,
            },
        )

    @staticmethod
    def _log_parse_diagnostics(page_html: str, part_number: str, url: str) -> None:
        """Log diagnostics when _parse_product_page returns None.

        Same diagnostic pattern as PR #204 for DigiKey: lets us tell bot-block
        pages apart from format changes without needing to reproduce locally.
        """
        title_match = re.search(r"<title[^>]*>([^<]*)</title>", page_html, re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""
        ld_count = len(re.findall(
            r"""<script[^>]*type=['"]application/ld\+json['"]""", page_html,
        ))
        logger.warning(
            "Mouser scrape failed for %s: url=%s title=%r length=%d ld_count=%d",
            part_number, url, title, len(page_html), ld_count,
        )

    # Bot-block pages can have an <h1> like "Access Denied" — treat those as
    # parse failures rather than rendering a tooltip with the deny page text.
    _BOT_BLOCK_TITLE_RE = re.compile(
        r"\b(access denied|access to this page has been denied|please enable js)\b",
        re.IGNORECASE,
    )

    # Mouser answers an unresolvable /ProductDetail/ with a 200 and a friendly
    # not-found page, and that page parses: it has a title, so everything below
    # runs and returns a "product" called "Sorry, we can't find the page you're
    # looking for." with no prices and no packagings. Two ways that hurts. A
    # tooltip renders the apology as though it were a part, and `_fetch_raw`
    # stops looking, because a non-None answer is a successful one -- so an MPN
    # that the search page would have resolved never gets searched for, since
    # /ProductDetail/<mpn> is tried first for anything shaped like a Mouser PN.
    # A page that could not be found is not a product; say so and let the
    # caller's fallback run.
    #
    # Phrases only, deliberately: a bare "404" would match the title of any
    # part whose number happens to contain those digits, and getting this
    # wrong in that direction hides a real product rather than an apology.
    # The apostrophe in "can't" is a typographic one on the real page and
    # could arrive as an entity, so the match starts after it.
    _NOT_FOUND_TITLE_RE = re.compile(
        r"find the page you|page (?:cannot be|not) found", re.IGNORECASE,
    )

    @classmethod
    def _parse_product_page(cls, page_html: str, part_number: str, url: str) -> dict[str, Any] | None:
        """Parse a Mouser product page and extract product details."""
        jsonld = extract_jsonld_product(page_html)

        title = extract_title(page_html, jsonld)
        if not title:
            return None
        if cls._BOT_BLOCK_TITLE_RE.search(title):
            return None
        if cls._NOT_FOUND_TITLE_RE.search(title):
            logger.debug("Mouser: %s is a not-found page, not a product", url)
            return None

        description = extract_description(page_html, jsonld)
        image_url = extract_image_url(page_html, jsonld)
        manufacturer = extract_manufacturer(jsonld)
        mpn = extract_mpn(jsonld)

        # Prices: start from JSON-LD, then add Mouser-specific volume pricing tiers
        # Mouser shows pricing tiers like "10 $5.50" or "10+ $5.50" in tables
        prices: list[dict[str, Any]] = extract_prices_from_jsonld(jsonld)
        price_matches = re.findall(r'(\d[\d,]*)\+?\s*\$(\d+\.?\d*)', page_html)
        for qty_str, price_str in price_matches:
            try:
                qty = int(qty_str.replace(",", ""))
                price = float(price_str)
                if not any(p["qty"] == qty for p in prices):
                    prices.append({"qty": qty, "price": price})
            except (ValueError, TypeError):
                pass
        prices.sort(key=lambda p: p["qty"])

        # Stock: start from JSON-LD availability, then try actual count from page
        stock = extract_stock_from_jsonld(jsonld)
        stock_match = re.search(r'(\d[\d,]*)\s+[Ii]n\s+[Ss]tock', page_html)
        if stock_match:
            try:
                stock = int(stock_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # Mouser-specific: extract datasheet PDF URL
        pdf_url = ""
        pdf_match = re.search(r'href="([^"]*\.pdf[^"]*)"', page_html, re.IGNORECASE)
        if pdf_match:
            pdf_url = pdf_match.group(1)
            if pdf_url.startswith("//"):
                pdf_url = "https:" + pdf_url

        # Mouser-specific: breadcrumb uses "breadcrumb" class, category = last crumb
        category = ""
        subcategory = ""
        breadcrumb_matches = re.findall(
            r'<a[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>([^<]+)</a>', page_html,
        )
        if not breadcrumb_matches:
            breadcrumb_matches = re.findall(
                r'<li[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>[^<]*<a[^>]*>([^<]+)</a>',
                page_html,
            )
        if breadcrumb_matches:
            crumbs = [c.strip() for c in breadcrumb_matches
                      if c.strip().lower() not in ("home", "mouser", "")]
            if len(crumbs) >= 1:
                category = crumbs[-1]
            if len(crumbs) >= 2:
                subcategory = crumbs[-2]

        attributes = extract_attributes(
            page_html, excluded_names=["quantity", "price", "unit price"]
        )

        # Packaging: reuse the page's own spec table (already parsed into
        # `attributes`) rather than adding a second carrier-name regex.
        packaging_name = _packaging_name(attributes)

        # Reel quantity: prefer the reel-anchored phrasing the product page
        # uses ("Full Reel (Order in multiples of 3,000)"); fall back to a bare
        # order multiple only when the carrier is already known to be tape.
        reel_match = _REEL_MULTIPLE_RE.search(page_html)
        if reel_match:
            reel_qty = _clean_int(reel_match.group(1))
        else:
            mult_match = _ORDER_MULTIPLE_RE.search(page_html)
            reel_qty = (
                _clean_int(mult_match.group(1))
                if mult_match and carrier_of(packaging_name) == "tape"
                else None
            )

        # The pricing table, when present, is strictly better than everything
        # above: it names each carrier and gives it its own ladder, where the
        # spec-table attribute names one carrier and the loose page-wide regex
        # cannot attribute a break to any of them. So it wins outright, and the
        # default packaging's ladder (Mouser lists it first) becomes the
        # product's headline `prices`.
        table_groups = _parse_pricing_table(page_html)
        if table_groups:
            packagings = [{
                "name": group["name"],
                "partNumber": part_number,
                "prices": group["prices"],
            } for group in table_groups]
            prices = table_groups[0]["prices"]
        elif packaging_name:
            packagings = [{
                "name": packaging_name,
                "partNumber": part_number,
                "prices": prices,
            }]
        else:
            packagings = []

        # Every Mouser product page renders the same "Packaging Choice"
        # explainer, MouseReel(tm) (Add $7.00 reeling fee) and all, whether or
        # not the part is offered that way -- so the figure is real but its
        # applicability is not. Reeling cuts a tape; a part that does not come
        # on tape cannot be reeled, and a part whose carriers we could not read
        # has not told us it can. Unknown is not permission, the same rule
        # domain/predicates.py applies to substitutions, and the cost of
        # getting it wrong here is a "Tray + reeling" offer that the reel
        # preset would happily choose.
        #
        # Searched with the pricing table removed: its sub-heading reads
        # "Cut Tape / MouseReel(tm)" and the next dollar figure after it is the
        # first price break, which a page-wide search returns as the fee.
        offers_tape = any(carrier_of(entry["name"]) == "tape" for entry in packagings)
        fee_match = (_MOUSEREEL_FEE_RE.search(_PRICING_TABLE_RE.sub(" ", page_html))
                     if offers_tape else None)
        reel_fee = fee_match.group(1) if fee_match else None

        product: dict[str, Any] = build_product(
            product_code=part_number,
            title=title,
            manufacturer=manufacturer,
            mpn=mpn,
            package="",
            description=description,
            stock=stock,
            prices=prices,
            image_url=image_url,
            pdf_url=pdf_url,
            url=url,
            category=category,
            subcategory=subcategory,
            attributes=attributes,
            provider="mouser",
            packagings=packagings,
            reel_qty=reel_qty,
            reel_fee=reel_fee,
            debug={
                "url": url,
                "part_number": part_number,
                "jsonld": jsonld,
            },
        )

        return product
