"""LCSC product-fetching client — extracted from inventory_api.py."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from base_client import BaseProductClient
from domain.product import build_product

logger = logging.getLogger(__name__)


def _clean_int(value: Any) -> int | None:
    """Best-effort positive int from LCSC's loosely-typed numeric fields."""
    if value is None:
        return None
    try:
        n = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


class LcscClient(BaseProductClient):
    """Fetches and caches LCSC product details by product code."""

    provider = "lcsc"

    def _fetch_raw(self, product_code: str) -> dict[str, Any] | None:
        """Fetch LCSC product details by product code (e.g. C2040).

        Returns a normalized dict of product info, or None if not found/failed.
        Raises ValueError for invalid product codes.
        """
        product_code = str(product_code).strip().upper()
        if not re.match(r"^C\d{4,}$", product_code):
            raise ValueError(f"Invalid LCSC product code: {product_code!r}")

        url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={product_code}"
        try:
            # LCSC's API returns HTTP 403 for the default ``Python-urllib`` agent,
            # so send an explicit User-Agent (matches the mouser/pololu clients).
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "dubIS/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("LCSC fetch failed for %s: %s", product_code, exc)
            return None

        result_data = data.get("result") if isinstance(data, dict) else None
        if not result_data or not isinstance(result_data, dict):
            logger.warning("LCSC returned no result for %s", product_code)
            return None

        # Extract price tiers
        prices = []
        for tier in (result_data.get("productPriceList") or []):
            if isinstance(tier, dict):
                prices.append({
                    "qty": tier.get("ladder", 0),
                    "price": tier.get("productPrice", 0),
                })

        # Build normalized response
        cat_name = ""
        subcat_name = ""
        for cat in (result_data.get("parentCatalogList") or []):
            if isinstance(cat, dict):
                if not cat_name:
                    cat_name = cat.get("catalogName", "")
                else:
                    subcat_name = cat.get("catalogName", "")

        # Extract key attributes from paramVOList
        attributes = []
        for param in (result_data.get("paramVOList") or []):
            if isinstance(param, dict):
                name = param.get("paramNameEn", "")
                value = param.get("paramValueEn", "")
                if name and value and value != "-":
                    attributes.append({"name": name, "value": value})

        # Image: API returns productImages array, fall back to productImageUrl
        images = result_data.get("productImages") or []
        image_url = images[0] if images else result_data.get("productImageUrl", "")

        # Packaging: LCSC publishes the packet quantity (`minPacketNumber`),
        # its unit ("Reel"/"Tray"), a boolean `isReel`, and the custom-reeling
        # surcharge (`reelPrice`). `isReel` is authoritative and can disagree
        # with the unit name — C393939 reports unit "Reel" with isReel False —
        # so it is passed explicitly rather than inferred from the name.
        packet_qty = result_data.get("minPacketNumber")
        packet_unit = result_data.get("minPacketUnit") or ""
        packagings = []
        if packet_unit or packet_qty:
            packagings.append({
                "name": packet_unit or "Standard",
                "packetQty": _clean_int(packet_qty),
                "minBuyQty": _clean_int(result_data.get("minBuyNumber")),
                "isReel": bool(result_data.get("isReel")),
                "prices": prices,
            })

        product = build_product(
            product_code=result_data.get("productCode", product_code),
            title=result_data.get("title", "") or result_data.get("productIntroEn", ""),
            manufacturer=result_data.get("brandNameEn", ""),
            mpn=result_data.get("productModel", ""),
            package=result_data.get("encapStandard", ""),
            description=result_data.get("productIntroEn", ""),
            stock=result_data.get("stockNumber", 0),
            prices=prices,
            image_url=image_url,
            pdf_url=result_data.get("pdfUrl", ""),
            url=f"https://www.lcsc.com/product-detail/{product_code}.html",
            category=cat_name,
            subcategory=subcat_name,
            attributes=attributes,
            provider="lcsc",
            packagings=packagings,
            reel_qty=packet_qty,
            reel_fee=result_data.get("reelPrice"),
            debug=result_data,
        )

        return product
