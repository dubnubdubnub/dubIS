"""Mouser product-fetching client — scrapes product pages from mouser.com."""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class MouserClient:
    """Fetches and caches Mouser product details by part number."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any] | None] = {}

    def fetch_product(self, part_number: str) -> dict[str, Any] | None:
        """Fetch Mouser product details by part number (e.g. 736-FGG0B305CLAD52).

        Returns a normalized dict of product info, or None if not found/failed.
        Results (including None) are cached for the session.
        """
        part_number = str(part_number).strip()
        if not part_number or not re.match(r"^[\w.\-/]{2,60}$", part_number):
            raise ValueError(f"Invalid Mouser part number: {part_number!r}")

        if part_number in self._cache:
            return self._cache[part_number]

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
            self._cache[part_number] = None
            return None

        product = self._parse_product_page(page_html, part_number, url)
        self._cache[part_number] = product
        return product

    @staticmethod
    def _parse_product_page(page_html: str, part_number: str, url: str) -> dict[str, Any] | None:
        """Parse a Mouser product page and extract product details."""
        # Try to extract JSON-LD structured data first
        jsonld_match = re.search(
            r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
            page_html, re.DOTALL,
        )
        jsonld = None
        if jsonld_match:
            try:
                jsonld = json.loads(jsonld_match.group(1))
                # Handle array of JSON-LD objects
                if isinstance(jsonld, list):
                    jsonld = next(
                        (j for j in jsonld if isinstance(j, dict) and j.get("@type") == "Product"),
                        None,
                    )
            except json.JSONDecodeError:
                jsonld = None

        # Extract title from JSON-LD or HTML
        title = ""
        if jsonld and jsonld.get("name"):
            title = jsonld["name"]
        else:
            m = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.DOTALL)
            if m:
                title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                title = html_mod.unescape(title)

        if not title:
            return None

        # Extract description from meta tag or JSON-LD
        description = ""
        if jsonld and jsonld.get("description"):
            description = jsonld["description"]
        else:
            m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page_html)
            if m:
                description = html_mod.unescape(m.group(1))

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
        # Fix protocol-relative URLs
        if image_url.startswith("//"):
            image_url = "https:" + image_url

        # Extract price from JSON-LD offers
        prices: list[dict[str, Any]] = []
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

        # Extract volume pricing from page HTML
        # Mouser shows pricing tiers like "10 $5.50" or "10+ $5.50" in tables
        price_matches = re.findall(
            r'(\d[\d,]*)\+?\s*\$(\d+\.?\d*)', page_html,
        )
        for qty_str, price_str in price_matches:
            try:
                qty = int(qty_str.replace(",", ""))
                price = float(price_str)
                if not any(p["qty"] == qty for p in prices):
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
                    stock = 1
            elif isinstance(offers, list) and offers:
                avail = offers[0].get("availability", "")
                if "InStock" in avail:
                    stock = 1

        # Try to get actual stock count from page
        stock_match = re.search(r'(\d[\d,]*)\s+[Ii]n\s+[Ss]tock', page_html)
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
            mpn = jsonld.get("mpn", "") or jsonld.get("sku", "")

        # Extract datasheet PDF URL
        pdf_url = ""
        pdf_match = re.search(r'href="([^"]*\.pdf[^"]*)"', page_html, re.IGNORECASE)
        if pdf_match:
            pdf_url = pdf_match.group(1)
            if pdf_url.startswith("//"):
                pdf_url = "https:" + pdf_url

        # Extract category from breadcrumbs
        category = ""
        subcategory = ""
        breadcrumb_matches = re.findall(
            r'<a[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>([^<]+)</a>', page_html,
        )
        if not breadcrumb_matches:
            # Alternative breadcrumb pattern
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

        # Extract key specs/attributes from product detail tables
        attributes: list[dict[str, str]] = []
        spec_matches = re.findall(
            r'<t[hd][^>]*>([^<]+)</t[hd]>\s*<td[^>]*>([^<]+)</td>',
            page_html,
        )
        for name, value in spec_matches:
            name = html_mod.unescape(name.strip())
            value = html_mod.unescape(value.strip())
            if name and value and name.lower() not in ("quantity", "price", "unit price"):
                attributes.append({"name": name, "value": value})

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
