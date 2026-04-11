"""Mouser product-fetching client — scrapes product pages from mouser.com."""

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


class MouserClient(BaseProductClient):
    """Fetches and caches Mouser product details by part number."""

    provider = "mouser"

    def _fetch_raw(self, part_number: str) -> dict[str, Any] | None:
        """Fetch Mouser product details by part number (e.g. 736-FGG0B305CLAD52).

        Returns a normalized dict of product info, or None if not found/failed.
        Raises ValueError for invalid part numbers.
        """
        part_number = str(part_number).strip()
        if not part_number or not re.match(r"^[\w.\-/]{2,60}$", part_number):
            raise ValueError(f"Invalid Mouser part number: {part_number!r}")

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

        return self._parse_product_page(page_html, part_number, url)

    @staticmethod
    def _parse_product_page(page_html: str, part_number: str, url: str) -> dict[str, Any] | None:
        """Parse a Mouser product page and extract product details."""
        jsonld = extract_jsonld_product(page_html)

        title = extract_title(page_html, jsonld)
        if not title:
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
