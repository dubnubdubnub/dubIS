"""Pololu product-fetching client — scrapes product pages from pololu.com."""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class PololuClient:
    """Fetches and caches Pololu product details by SKU number."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any] | None] = {}

    def fetch_product(self, sku: str) -> dict[str, Any] | None:
        """Fetch Pololu product details by SKU (e.g. 1992).

        Returns a normalized dict of product info, or None if not found/failed.
        Results (including None) are cached for the session.
        """
        sku = str(sku).strip()
        if not re.match(r"^\d{1,6}$", sku):
            raise ValueError(f"Invalid Pololu SKU: {sku!r}")

        if sku in self._cache:
            return self._cache[sku]

        url = f"https://www.pololu.com/product/{sku}"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "dubIS/1.0",
                "Accept": "text/html",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                page_html = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("Pololu fetch failed for %s: %s", sku, exc)
            self._cache[sku] = None
            return None

        product = self._parse_product_page(page_html, sku, url)
        self._cache[sku] = product
        return product

    @staticmethod
    def _parse_product_page(page_html: str, sku: str, url: str) -> dict[str, Any] | None:
        """Parse a Pololu product page and extract product details."""
        # Try to extract JSON-LD structured data (Pololu uses single-quoted attributes)
        jsonld = None
        for jsonld_match in re.finditer(
            r"""<script[^>]*type=['"]application/ld\+json['"][^>]*>(.*?)</script>""",
            page_html, re.DOTALL,
        ):
            try:
                candidate = json.loads(jsonld_match.group(1))
                if isinstance(candidate, list):
                    candidate = next(
                        (j for j in candidate if isinstance(j, dict) and j.get("@type") == "Product"),
                        None,
                    )
                if isinstance(candidate, dict) and candidate.get("@type") == "Product":
                    jsonld = candidate
                    break
            except json.JSONDecodeError:
                continue

        # Extract title from JSON-LD or HTML
        title = ""
        if jsonld and jsonld.get("name"):
            title = jsonld["name"]
        else:
            m = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.DOTALL)
            if m:
                title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                title = html.unescape(title)

        if not title:
            return None

        # Extract description from meta tag or JSON-LD
        description = ""
        if jsonld and jsonld.get("description"):
            description = jsonld["description"]
        else:
            m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page_html)
            if m:
                description = html.unescape(m.group(1))

        # Extract image
        image_url = ""
        if jsonld and jsonld.get("image"):
            img = jsonld["image"]
            if isinstance(img, list):
                image_url = img[0] if img else ""
            else:
                image_url = str(img)
        if not image_url:
            m = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', page_html)
            if m:
                image_url = m.group(1)

        # Extract price from JSON-LD offers
        prices = []
        if jsonld and jsonld.get("offers"):
            offers = jsonld["offers"]
            if isinstance(offers, dict) and offers.get("price"):
                try:
                    prices.append({"qty": 1, "price": float(offers["price"])})
                except (ValueError, TypeError):
                    pass
            elif isinstance(offers, list):
                for offer in offers:
                    if isinstance(offer, dict) and offer.get("price"):
                        try:
                            prices.append({"qty": 1, "price": float(offer["price"])})
                        except (ValueError, TypeError):
                            pass

        # Extract volume pricing from page HTML.
        # Pololu renders price tiers as <tr><td>QTY</td><td>PRICE</td></tr>
        price_rows = re.findall(
            r"<tr>\s*<td>\s*(\d+)\s*</td>\s*<td[^>]*>\s*(\d+\.?\d*)\s*</td>\s*</tr>",
            page_html,
        )
        for qty_str, price_str in price_rows:
            try:
                qty = int(qty_str)
                price = float(price_str)
                if qty > 0 and price > 0 and not any(p["qty"] == qty for p in prices):
                    prices.append({"qty": qty, "price": price})
            except (ValueError, TypeError):
                pass
        prices.sort(key=lambda p: p["qty"])

        # Extract stock/availability
        stock = 0
        if jsonld and jsonld.get("offers"):
            offers = jsonld["offers"]
            if isinstance(offers, dict):
                avail = offers.get("availability", "")
                if "InStock" in avail:
                    stock = 1  # At least in stock
            elif isinstance(offers, list) and offers:
                avail = offers[0].get("availability", "")
                if "InStock" in avail:
                    stock = 1

        # Try to get actual stock count from data attribute or text
        stock_match = re.search(
            r"""data-available-stock=['"](\d+)['"]""", page_html,
        )
        if not stock_match:
            stock_match = re.search(r'(\d[\d,]*)\s+in\s+stock', page_html, re.IGNORECASE)
        if stock_match:
            try:
                stock = int(stock_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # Extract brand/manufacturer from JSON-LD
        manufacturer = ""
        if jsonld and jsonld.get("brand"):
            brand = jsonld["brand"]
            if isinstance(brand, dict):
                manufacturer = brand.get("name", "")
            elif isinstance(brand, str):
                manufacturer = brand

        # Extract MPN from JSON-LD
        mpn = ""
        if jsonld:
            mpn = str(jsonld.get("mpn", "") or jsonld.get("sku", "") or sku)

        # Extract category from breadcrumbs
        category = ""
        subcategory = ""
        breadcrumb_matches = re.findall(
            r'<a[^>]*class="[^"]*crumb[^"]*"[^>]*>([^<]+)</a>', page_html,
        )
        if breadcrumb_matches:
            # Skip "Home" and "Products" if present
            crumbs = [c.strip() for c in breadcrumb_matches if c.strip().lower() not in ("home", "pololu")]
            if len(crumbs) >= 1:
                category = crumbs[0]
            if len(crumbs) >= 2:
                subcategory = crumbs[1]

        # Extract key specs/attributes from product detail tables
        attributes = []
        spec_matches = re.findall(
            r'<t[hd][^>]*>([^<]+)</t[hd]>\s*<td[^>]*>([^<]+)</td>',
            page_html,
        )
        for name, value in spec_matches:
            name = html.unescape(name.strip())
            value = html.unescape(value.strip())
            if name and value and name.lower() not in ("quantity", "price"):
                attributes.append({"name": name, "value": value})

        product = {
            "productCode": sku,
            "title": title,
            "manufacturer": manufacturer,
            "mpn": mpn,
            "package": "",
            "description": description,
            "stock": stock,
            "prices": prices,
            "imageUrl": image_url,
            "pdfUrl": "",
            "pololuUrl": url,
            "category": category,
            "subcategory": subcategory,
            "attributes": attributes,
            "provider": "pololu",
            "_debug": {
                "url": url,
                "sku": sku,
                "jsonld": jsonld,
            },
        }

        return product
