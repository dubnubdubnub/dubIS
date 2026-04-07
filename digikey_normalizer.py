"""Normalize Digikey product data from various JSON formats."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def normalize_result(
    raw: dict[str, Any], part_number: str
) -> dict[str, Any]:
    """Normalize scraped Digikey data to the same shape as LCSC product.

    Handles: JSON-LD Product, Next.js SSR, unknown format fallback.
    """
    # JSON-LD Product schema
    if isinstance(raw, dict) and raw.get("@type") == "Product":
        return _normalize_jsonld(raw, part_number)

    # Next.js SSR data
    if isinstance(raw, dict) and raw.get("_source") == "nextdata":
        return _normalize_nextdata(raw, part_number)

    # Unknown format — return empty shell
    return _normalize_fallback(part_number)


def _normalize_jsonld(
    raw: dict[str, Any], part_number: str
) -> dict[str, Any]:
    """Normalize a JSON-LD Product schema result."""
    offers = raw.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price_val: float = 0
    try:
        price_val = float(
            offers.get("price") or offers.get("lowPrice") or 0
        )
    except (ValueError, TypeError):
        pass

    brand = raw.get("brand") or {}
    image = raw.get("image", "")
    if isinstance(image, list):
        image = image[0] if image else ""

    return {
        "productCode": raw.get("sku") or part_number,
        "title": raw.get("name", ""),
        "manufacturer": (
            brand.get("name", "")
            if isinstance(brand, dict)
            else str(brand)
        ),
        "mpn": raw.get("mpn", "") or raw.get("sku", ""),
        "package": "",
        "description": raw.get("description", ""),
        "stock": raw.get("_stock") or (
            1 if "InStock" in str(
                offers.get("availability", "")
            ) else 0
        ),
        "prices": (
            [{"qty": 1, "price": price_val}] if price_val else []
        ),
        "imageUrl": image,
        "pdfUrl": "",
        "digikeyUrl": raw.get("url", ""),
        "attributes": [],
        "provider": "digikey",
    }


def _normalize_nextdata(
    raw: dict[str, Any], part_number: str
) -> dict[str, Any]:
    """Normalize a Next.js SSR envelope.data result."""
    props = raw.get("_props") or {}
    envelope = props.get("envelope") or {}
    data = envelope.get("data") or {}
    overview = data.get("productOverview") or {}
    pq = data.get("priceQuantity") or {}
    pa = data.get("productAttributes") or {}
    media = data.get("carouselMedia") or []
    crumbs = data.get("breadcrumb") or []

    # Stock
    stock = 0
    try:
        stock = int(
            str(pq.get("qtyAvailable", "0")).replace(",", "")
        )
    except (ValueError, TypeError):
        pass

    # Prices — use first pricing option (smallest MOQ packaging)
    prices: list[dict[str, int | float]] = []
    pricing_list = pq.get("pricing") or []
    if pricing_list:
        tiers = pricing_list[0].get("mergedPricingTiers") or []
        for t in tiers:
            try:
                qty = int(
                    str(t.get("brkQty", "0")).replace(",", "")
                )
                price = float(
                    str(t.get("unitPrice", "0"))
                    .replace("$", "")
                    .replace(",", "")
                )
                prices.append({"qty": qty, "price": price})
            except (ValueError, TypeError):
                continue

    # Image — first Image type in carousel
    image_url = ""
    for m in media:
        if m.get("type") == "Image":
            image_url = (
                m.get("displayUrl") or m.get("smallPhoto") or ""
            )
            break
    if image_url.startswith("//"):
        image_url = "https:" + image_url

    # Package and attributes from attribute list
    package = ""
    attrs_out: list[dict[str, str]] = []
    skip_ids = {"-1", "-4", "-5", "1989", "-7"}
    for attr in pa.get("attributes") or []:
        vals = attr.get("values") or []
        val = vals[0].get("value", "") if vals else ""
        if attr.get("label") == "Package / Case":
            package = val
        attr_id = str(attr.get("id", ""))
        if attr_id not in skip_ids and val and val != "-":
            attrs_out.append(
                {"name": attr.get("label", ""), "value": val}
            )

    # Category from categories list
    cats = pa.get("categories") or []
    category = cats[-1]["label"] if cats else ""
    subcategory = cats[-2]["label"] if len(cats) >= 2 else ""

    # Digikey URL from last breadcrumb
    dk_url = ""
    if crumbs:
        dk_url = crumbs[-1].get("url", "")
        if dk_url and not dk_url.startswith("http"):
            dk_url = "https://www.digikey.com" + dk_url

    return {
        "productCode": (
            overview.get("rolledUpProductNumber") or part_number
        ),
        "title": overview.get("title") or "",
        "manufacturer": overview.get("manufacturer") or "",
        "mpn": overview.get("manufacturerProductNumber") or "",
        "package": package,
        "description": (
            overview.get("detailedDescription")
            or overview.get("description")
            or ""
        ),
        "stock": stock,
        "prices": prices,
        "imageUrl": image_url,
        "pdfUrl": overview.get("datasheetUrl") or "",
        "digikeyUrl": dk_url,
        "category": category,
        "subcategory": subcategory,
        "attributes": attrs_out,
        "provider": "digikey",
    }


def _normalize_fallback(part_number: str) -> dict[str, Any]:
    """Return an empty shell for unknown formats."""
    return {
        "productCode": part_number,
        "title": "",
        "manufacturer": "",
        "mpn": "",
        "package": "",
        "description": "",
        "stock": 0,
        "prices": [],
        "imageUrl": "",
        "pdfUrl": "",
        "digikeyUrl": "",
        "attributes": [],
        "provider": "digikey",
    }
