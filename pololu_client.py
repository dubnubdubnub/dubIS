"""Pololu product-fetching client — scrapes product pages from pololu.com."""

from __future__ import annotations

import logging
import re
import urllib.error
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


class PololuClient(BaseProductClient):
    """Fetches and caches Pololu product details by SKU number."""

    provider = "pololu"

    def _fetch_raw(self, sku: str) -> dict[str, Any] | None:
        """Fetch Pololu product details by SKU (e.g. 1992).

        Returns a normalized dict of product info, or None if not found/failed.
        Raises ValueError for invalid SKUs.
        """
        sku = str(sku).strip()
        if not re.match(r"^\d{1,6}$", sku):
            raise ValueError(f"Invalid Pololu SKU: {sku!r}")

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
            return None

        return self._parse_product_page(page_html, sku, url)

    @staticmethod
    def _parse_product_page(page_html: str, sku: str, url: str) -> dict[str, Any] | None:
        """Parse a Pololu product page and extract product details."""
        jsonld = extract_jsonld_product(page_html)

        title = extract_title(page_html, jsonld)
        if not title:
            return None

        description = extract_description(page_html, jsonld)
        image_url = extract_image_url(page_html, jsonld)
        manufacturer = extract_manufacturer(jsonld)

        # Pololu-specific: MPN falls back to sku parameter
        mpn = extract_mpn(jsonld, fallback=sku)

        # Prices: start from JSON-LD, then add Pololu-specific volume pricing tiers
        # Pololu renders price tiers as <tr><td>QTY</td><td>PRICE</td></tr>
        prices: list[dict[str, Any]] = extract_prices_from_jsonld(jsonld)
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

        # Stock: start from JSON-LD, then try Pololu-specific data-available-stock attribute
        stock = extract_stock_from_jsonld(jsonld)
        stock_match = re.search(r"""data-available-stock=['"](\d+)['"]""", page_html)
        if not stock_match:
            stock_match = re.search(r'(\d[\d,]*)\s+in\s+stock', page_html, re.IGNORECASE)
        if stock_match:
            try:
                stock = int(stock_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # Pololu-specific: breadcrumb uses "crumb" class, category = first crumb
        category = ""
        subcategory = ""
        breadcrumb_matches = re.findall(
            r'<a[^>]*class="[^"]*crumb[^"]*"[^>]*>([^<]+)</a>', page_html,
        )
        if breadcrumb_matches:
            crumbs = [c.strip() for c in breadcrumb_matches
                      if c.strip().lower() not in ("home", "pololu")]
            if len(crumbs) >= 1:
                category = crumbs[0]
            if len(crumbs) >= 2:
                subcategory = crumbs[1]

        attributes = extract_attributes(page_html, excluded_names=["quantity", "price"])

        product: dict[str, Any] = {
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
