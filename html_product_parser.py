"""Shared HTML product page parsing utilities.

Used by mouser_client.py and pololu_client.py to extract structured
product data from HTML pages containing JSON-LD and standard markup.
"""

from __future__ import annotations

import html as html_mod
import json
import re
from typing import Any


def extract_jsonld_product(page_html: str) -> dict[str, Any] | None:
    """Extract the first JSON-LD Product object from HTML."""
    for match in re.finditer(
        r"""<script[^>]*type=['"]application/ld\+json['"][^>]*>(.*?)</script>""",
        page_html,
        re.DOTALL,
    ):
        try:
            candidate = json.loads(match.group(1))
            if isinstance(candidate, list):
                candidate = next(
                    (j for j in candidate if isinstance(j, dict) and j.get("@type") == "Product"),
                    None,
                )
            if isinstance(candidate, dict) and candidate.get("@type") == "Product":
                return candidate
        except json.JSONDecodeError:
            continue
    return None


def extract_title(page_html: str, jsonld: dict[str, Any] | None) -> str:
    """Extract product title from JSON-LD or <h1> fallback."""
    if jsonld and jsonld.get("name"):
        return jsonld["name"]
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.DOTALL)
    if m:
        return html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(1)).strip())
    return ""


def extract_description(page_html: str, jsonld: dict[str, Any] | None) -> str:
    """Extract product description from JSON-LD or meta tag."""
    if jsonld and jsonld.get("description"):
        return jsonld["description"]
    m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page_html)
    if m:
        return html_mod.unescape(m.group(1))
    return ""


def extract_image_url(page_html: str, jsonld: dict[str, Any] | None) -> str:
    """Extract product image URL from JSON-LD or og:image, fixing protocol-relative URLs."""
    image_url = ""
    if jsonld and jsonld.get("image"):
        img = jsonld["image"]
        image_url = img[0] if isinstance(img, list) and img else str(img)
    if not image_url:
        m = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', page_html)
        if m:
            image_url = m.group(1)
    if image_url.startswith("//"):
        image_url = "https:" + image_url
    return image_url


def extract_prices_from_jsonld(jsonld: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract price list from JSON-LD offers."""
    if not jsonld or not jsonld.get("offers"):
        return []
    prices: list[dict[str, Any]] = []
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
    return prices


def extract_stock_from_jsonld(jsonld: dict[str, Any] | None) -> int:
    """Extract stock availability from JSON-LD offers (0 or 1)."""
    if not jsonld or not jsonld.get("offers"):
        return 0
    offers = jsonld["offers"]
    if isinstance(offers, dict):
        if "InStock" in offers.get("availability", ""):
            return 1
    elif isinstance(offers, list) and offers:
        if "InStock" in offers[0].get("availability", ""):
            return 1
    return 0


def extract_manufacturer(jsonld: dict[str, Any] | None) -> str:
    """Extract manufacturer/brand name from JSON-LD."""
    if not jsonld or not jsonld.get("brand"):
        return ""
    brand = jsonld["brand"]
    if isinstance(brand, dict):
        return brand.get("name", "")
    return str(brand) if isinstance(brand, str) else ""


def extract_mpn(jsonld: dict[str, Any] | None, *, fallback: str = "") -> str:
    """Extract MPN from JSON-LD, with optional fallback."""
    if not jsonld:
        return fallback
    return str(jsonld.get("mpn", "") or jsonld.get("sku", "") or fallback)


def extract_attributes(
    page_html: str,
    excluded_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Extract attribute rows from HTML spec tables."""
    excluded = {n.lower() for n in (excluded_names or [])}
    attributes: list[dict[str, str]] = []
    for name_raw, value_raw in re.findall(
        r"<t[hd][^>]*>([^<]+)</t[hd]>\s*<td[^>]*>([^<]+)</td>",
        page_html,
    ):
        name = html_mod.unescape(name_raw.strip())
        value = html_mod.unescape(value_raw.strip())
        if name and value and name.lower() not in excluded:
            attributes.append({"name": name, "value": value})
    return attributes
