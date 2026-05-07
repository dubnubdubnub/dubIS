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

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from base_client import BaseProductClient
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


class MouserClient(BaseProductClient):
    """Fetches and caches Mouser product details by part number."""

    provider = "mouser"

    def __init__(self, credentials_file: str | None = None) -> None:
        super().__init__()
        self._credentials_file = credentials_file

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

        title = (part.get("Description") or part.get("ManufacturerPartNumber")
                 or part_number)
        return {
            "productCode": part.get("MouserPartNumber") or part_number,
            "title": title,
            "manufacturer": part.get("Manufacturer") or "",
            "mpn": part.get("ManufacturerPartNumber") or "",
            "package": "",
            "description": part.get("Description") or "",
            "stock": stock,
            "prices": prices,
            "imageUrl": part.get("ImagePath") or "",
            "pdfUrl": part.get("DataSheetUrl") or "",
            "mouserUrl": (
                part.get("ProductDetailUrl")
                or f"https://www.mouser.com/ProductDetail/{part_number}"
            ),
            "category": part.get("Category") or "",
            "subcategory": "",
            "attributes": attributes,
            "provider": "mouser",
            "_debug": {
                "source": "api",
                "part_number": part_number,
                "raw": part,
            },
        }

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

    @classmethod
    def _parse_product_page(cls, page_html: str, part_number: str, url: str) -> dict[str, Any] | None:
        """Parse a Mouser product page and extract product details."""
        jsonld = extract_jsonld_product(page_html)

        title = extract_title(page_html, jsonld)
        if not title:
            return None
        if cls._BOT_BLOCK_TITLE_RE.search(title):
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

        product: dict[str, Any] = {
            "productCode": part_number,
            "title": title,
            "manufacturer": manufacturer,
            "mpn": mpn,
            "package": "",
            "description": description,
            "stock": stock,
            "prices": prices,
            "imageUrl": image_url,
            "pdfUrl": pdf_url,
            "mouserUrl": url,
            "category": category,
            "subcategory": subcategory,
            "attributes": attributes,
            "provider": "mouser",
            "_debug": {
                "url": url,
                "part_number": part_number,
                "jsonld": jsonld,
            },
        }

        return product
