"""Normalize Digikey product data from various JSON formats."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def normalize_result(
    raw: dict[str, Any], part_number: str
) -> dict[str, Any]:
    """Normalize scraped Digikey data to the same shape as LCSC product.

    Handles: combined envelope (jsonld+nextdata+dom), JSON-LD Product,
    Next.js SSR, unknown format fallback.
    """
    if isinstance(raw, dict) and raw.get("_source") == "dk_combined":
        return _normalize_combined(raw, part_number)

    # JSON-LD Product schema
    if isinstance(raw, dict) and raw.get("@type") == "Product":
        return _normalize_jsonld(raw, part_number)

    # Next.js SSR data
    if isinstance(raw, dict) and raw.get("_source") == "nextdata":
        return _normalize_nextdata(raw, part_number)

    # Unknown format — return empty shell
    return _normalize_fallback(part_number)


def _normalize_combined(
    raw: dict[str, Any], part_number: str
) -> dict[str, Any]:
    """Normalize the combined envelope produced by ``_SCRAPE_JS``.

    Picks the richest structured source (Next.js > JSON-LD > fallback) and
    enriches the result with DOM-scraped fields (price tiers, datasheet URL,
    packaging variants) wherever the structured data is incomplete.
    """
    nextdata = raw.get("nextdata")
    jsonld = raw.get("jsonld")
    dom = raw.get("dom") or {}

    if nextdata:
        result = _normalize_nextdata(
            {"_source": "nextdata", "_props": nextdata}, part_number,
        )
        nd_packagings = _extract_nextdata_packagings(nextdata)
    elif jsonld:
        result = _normalize_jsonld(jsonld, part_number)
        nd_packagings = []
    else:
        result = _normalize_fallback(part_number)
        nd_packagings = []

    # DOM enrichment — only fill fields the structured source missed,
    # except for prices where DOM is preferred when it has more tiers
    # (DK JSON-LD typically only carries lowPrice/highPrice).
    dom_tiers = dom.get("priceTiers") or []
    existing_prices = result.get("prices") or []
    if dom_tiers and len(dom_tiers) > len(existing_prices):
        result["prices"] = [
            {"qty": int(t.get("qty", 0)), "price": float(t.get("price", 0))}
            for t in dom_tiers
            if t.get("qty") and t.get("price") is not None
        ]

    if not result.get("pdfUrl") and dom.get("datasheetUrl"):
        result["pdfUrl"] = dom["datasheetUrl"]

    if not result.get("stock") and dom.get("stock"):
        try:
            result["stock"] = int(dom["stock"])
        except (ValueError, TypeError):
            pass

    if not result.get("digikeyUrl") and raw.get("_url"):
        result["digikeyUrl"] = raw["_url"]

    # Packagings: prefer Next.js (has full price tiers per packaging);
    # fall back to DOM scrape (names/codes only).
    packagings = nd_packagings
    if not packagings:
        packagings = _convert_dom_packagings(
            dom.get("packagings") or [], result.get("prices") or [], part_number,
        )
    if packagings:
        result["packagings"] = packagings
        # Pick the packaging matching the requested PN as the active price
        # source — keeps `prices` aligned with what's currently selected.
        active = _pick_active_packaging(packagings, part_number)
        if active and active.get("prices"):
            result["prices"] = active["prices"]

    return result


def _extract_nextdata_packagings(
    pageprops: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pull all packaging variants out of a Next.js pageProps payload.

    Each pricing entry under ``priceQuantity.pricing`` corresponds to one
    packaging type (Cut Tape, Tape & Reel, Tape & Box, ...). We attempt to
    read the human-readable name from a few common field shapes since the
    exact key has shifted over DK API versions.
    """
    envelope = (pageprops or {}).get("envelope") or {}
    data = envelope.get("data") or {}
    pq = data.get("priceQuantity") or {}
    pricing_list = pq.get("pricing") or []

    packagings: list[dict[str, Any]] = []
    for entry in pricing_list:
        if not isinstance(entry, dict):
            continue
        # Try several packaging-name shapes across DK API versions
        name = ""
        for key in (
            "packageType", "packagingType", "packaging", "packageTypeName",
            "packageName", "type",
        ):
            v = entry.get(key)
            if isinstance(v, dict):
                name = v.get("name") or v.get("label") or v.get("value") or ""
            elif isinstance(v, str):
                name = v
            if name:
                break

        dk_pn = (
            entry.get("digiKeyProductNumber")
            or entry.get("productNumber")
            or entry.get("partNumber")
            or ""
        )

        tiers_raw = entry.get("mergedPricingTiers") or entry.get("pricingTiers") or []
        tiers: list[dict[str, int | float]] = []
        for t in tiers_raw:
            if not isinstance(t, dict):
                continue
            try:
                qty = int(str(t.get("brkQty", t.get("qty", "0"))).replace(",", ""))
                price = float(
                    str(t.get("unitPrice", t.get("price", "0")))
                    .replace("$", "")
                    .replace(",", "")
                )
                if qty and price >= 0:
                    tiers.append({"qty": qty, "price": price})
            except (ValueError, TypeError):
                continue

        if tiers:
            packagings.append({
                "name": name or "Standard",
                "partNumber": dk_pn,
                "prices": tiers,
            })

    return packagings


def _convert_dom_packagings(
    dom_pkgs: list[dict[str, Any]],
    fallback_prices: list[dict[str, Any]],
    part_number: str,
) -> list[dict[str, Any]]:
    """Convert DOM-scraped packaging hints to the standard packaging shape.

    The DOM scrape only sees names/codes/hrefs — we don't have per-packaging
    pricing without navigating to each variant. We attach the currently
    visible tiers to whichever entry seems to match the requested PN.
    """
    if not dom_pkgs:
        return []

    # Try to extract a part number suffix code from the requested PN, e.g.
    # "YAG2274TR-ND" → "TR". DK convention uses 2-3 letter codes before -ND.
    m = re.search(r"([A-Z]{2,4})-ND\b", (part_number or "").upper())
    active_code = m.group(1) if m else ""

    out: list[dict[str, Any]] = []
    for p in dom_pkgs:
        code = (p.get("code") or "").upper()
        href = p.get("href") or ""
        entry = {
            "name": p.get("name") or "",
            "partNumber": "",
            "code": code,
            "href": href,
            "prices": [],
        }
        if active_code and code == active_code:
            entry["partNumber"] = part_number
            entry["prices"] = list(fallback_prices)
        out.append(entry)
    return out


def _pick_active_packaging(
    packagings: list[dict[str, Any]],
    part_number: str,
) -> dict[str, Any] | None:
    """Pick the packaging entry that matches the requested DK part number."""
    pn_norm = (part_number or "").strip().upper()
    if not packagings:
        return None
    for p in packagings:
        if (p.get("partNumber") or "").strip().upper() == pn_norm:
            return p
    # Fallback: code suffix match (e.g. requested ends in TR-ND, code=TR)
    m = re.search(r"([A-Z]{2,4})-ND\b", pn_norm)
    if m:
        suffix = m.group(1)
        for p in packagings:
            if (p.get("code") or "").upper() == suffix:
                return p
    return packagings[0]


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
