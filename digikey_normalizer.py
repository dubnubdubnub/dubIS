"""Normalize Digikey product data from various JSON formats."""

from __future__ import annotations

import logging
import re
from typing import Any

from domain.product import build_product

logger = logging.getLogger(__name__)

# DigiKey's attribute label for the factory reel/package quantity — the
# multiple a whole reel is sold in, rendered like "3,000". "Manufacturer
# Standard Package" is the current label; the shorter form has been seen on
# older pages, so both are accepted.
_STANDARD_PACKAGE_LABELS = frozenset({
    "manufacturer standard package", "standard package",
})


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

    # Work in `build_product` **kwargs** rather than in the assembled dict:
    # packagings/reel metadata have to be known before the product is built,
    # since bolting them onto the finished dict is exactly the drift
    # domain/product.py exists to prevent.
    if nextdata:
        fields = _nextdata_fields(
            {"_source": "nextdata", "_props": nextdata}, part_number,
        )
        nd_packagings = _extract_nextdata_packagings(nextdata)
        reel_fee = _extract_nextdata_reel_fee(nextdata)
    elif jsonld:
        fields = _jsonld_fields(jsonld, part_number)
        nd_packagings = []
        reel_fee = None
    else:
        fields = _fallback_fields(part_number)
        nd_packagings = []
        reel_fee = None

    # DOM enrichment — only fill fields the structured source missed,
    # except for prices where DOM is preferred when it has more tiers
    # (DK JSON-LD typically only carries lowPrice/highPrice).
    dom_tiers = dom.get("priceTiers") or []
    existing_prices = fields.get("prices") or []
    if dom_tiers and len(dom_tiers) > len(existing_prices):
        fields["prices"] = [
            {"qty": int(t.get("qty", 0)), "price": float(t.get("price", 0))}
            for t in dom_tiers
            if t.get("qty") and t.get("price") is not None
        ]

    if not fields.get("pdf_url") and dom.get("datasheetUrl"):
        fields["pdf_url"] = dom["datasheetUrl"]

    if not fields.get("stock") and dom.get("stock"):
        try:
            fields["stock"] = int(dom["stock"])
        except (ValueError, TypeError):
            pass

    if not fields.get("url") and raw.get("_url"):
        fields["url"] = raw["_url"]

    # Packagings: prefer Next.js (has full price tiers per packaging);
    # fall back to DOM scrape (names/codes only).
    packagings = nd_packagings
    if not packagings:
        packagings = _convert_dom_packagings(
            dom.get("packagings") or [], fields.get("prices") or [], part_number,
        )
    if packagings:
        # Pick the packaging matching the requested PN as the active price
        # source — keeps `prices` aligned with what's currently selected.
        active = _pick_active_packaging(packagings, part_number)
        if active and active.get("prices"):
            fields["prices"] = active["prices"]

    return build_product(**fields, packagings=packagings, reel_fee=reel_fee)


def _price_quantity(pageprops: dict[str, Any]) -> dict[str, Any]:
    """``envelope.data.priceQuantity`` from a Next.js pageProps, defensively."""
    envelope = (pageprops or {}).get("envelope") or {}
    data = envelope.get("data") if isinstance(envelope, dict) else None
    pq = data.get("priceQuantity") if isinstance(data, dict) else None
    return pq if isinstance(pq, dict) else {}


def _money(value: Any) -> float | None:
    """Positive float from a DK money field (``7``, ``"7.00"``, ``"$7.00"``)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
        if not value:
            return None
    try:
        fee = float(value)
    except (TypeError, ValueError):
        return None
    return fee if fee > 0 else None


# The custom-reeling surcharge DigiKey charges for a Digi-Reel®. DK's V4 REST
# API calls it `DigiReelFee` on a ProductVariation; the Next.js payload the
# scraper sees has used several camelCase spellings, and we have no captured
# DigiKey fixture to pin one down (DK needs auth), so probe the plausible set
# the same way _extract_nextdata_packagings already probes for the name.
_REEL_FEE_KEYS = (
    "digiReelFee", "digiReelingFee", "reelingFee", "reelFee", "customReelFee",
)


def _extract_nextdata_reel_fee(pageprops: dict[str, Any]) -> float | None:
    """Find DigiKey's Digi-Reel surcharge in a Next.js pageProps payload.

    Looked for on ``priceQuantity`` itself and then on each pricing entry (the
    fee belongs to the Digi-Reel packaging option). Returns None when absent —
    the honest answer for a part DK will not custom-reel.
    """
    pq = _price_quantity(pageprops)
    for key in _REEL_FEE_KEYS:
        fee = _money(pq.get(key))
        if fee is not None:
            return fee
    for entry in pq.get("pricing") or []:
        if not isinstance(entry, dict):
            continue
        for key in _REEL_FEE_KEYS:
            fee = _money(entry.get(key))
            if fee is not None:
                return fee
    return None


def _extract_nextdata_packagings(
    pageprops: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pull all packaging variants out of a Next.js pageProps payload.

    Each pricing entry under ``priceQuantity.pricing`` corresponds to one
    packaging type (Cut Tape, Tape & Reel, Tape & Box, ...). We attempt to
    read the human-readable name from a few common field shapes since the
    exact key has shifted over DK API versions.
    """
    pricing_list = _price_quantity(pageprops).get("pricing") or []

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
    return build_product(**_jsonld_fields(raw, part_number))


def _jsonld_fields(
    raw: dict[str, Any], part_number: str
) -> dict[str, Any]:
    """``build_product`` kwargs for a JSON-LD Product schema result."""
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
        "product_code": raw.get("sku") or part_number,
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
        "image_url": image,
        "pdf_url": "",
        "url": raw.get("url", ""),
        "attributes": [],
        "provider": "digikey",
    }


def _normalize_nextdata(
    raw: dict[str, Any], part_number: str
) -> dict[str, Any]:
    """Normalize a Next.js SSR envelope.data result."""
    return build_product(**_nextdata_fields(raw, part_number))


def _nextdata_fields(
    raw: dict[str, Any], part_number: str
) -> dict[str, Any]:
    """``build_product`` kwargs for a Next.js SSR envelope.data result."""
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
    # `pricing` is scraped, so a non-dict entry is possible; the packaging
    # extractor already skips them and this must not crash on them either.
    if pricing_list and isinstance(pricing_list[0], dict):
        tiers = pricing_list[0].get("mergedPricingTiers") or []
        for t in tiers:
            if not isinstance(t, dict):
                continue
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
    reel_qty: Any = None
    attrs_out: list[dict[str, str]] = []
    skip_ids = {"-1", "-4", "-5", "1989", "-7"}
    for attr in pa.get("attributes") or []:
        vals = attr.get("values") or []
        val = vals[0].get("value", "") if vals else ""
        label = attr.get("label") or ""
        if label == "Package / Case":
            package = val
        # DigiKey's factory reel/package quantity, rendered like "3,000".
        # `build_product` does the comma-stripping and 0/"" → None coercion.
        if label.strip().lower() in _STANDARD_PACKAGE_LABELS:
            reel_qty = val
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
        "product_code": (
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
        "image_url": image_url,
        "pdf_url": overview.get("datasheetUrl") or "",
        "url": dk_url,
        "category": category,
        "subcategory": subcategory,
        "attributes": attrs_out,
        "provider": "digikey",
        "reel_qty": reel_qty,
    }


def _normalize_fallback(part_number: str) -> dict[str, Any]:
    """Return an empty shell for unknown formats."""
    return build_product(**_fallback_fields(part_number))


def _fallback_fields(part_number: str) -> dict[str, Any]:
    """``build_product`` kwargs for an empty shell (unknown format)."""
    return {
        "product_code": part_number,
        "title": "",
        "manufacturer": "",
        "mpn": "",
        "package": "",
        "description": "",
        "stock": 0,
        "prices": [],
        "image_url": "",
        "pdf_url": "",
        "url": "",
        "attributes": [],
        "provider": "digikey",
    }
