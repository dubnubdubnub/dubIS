"""LCSC product-fetching client — extracted from inventory_api.py."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class LcscClient:
    """Fetches and caches LCSC product details by product code."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any] | None] = {}

    def fetch_product(self, product_code: str) -> dict[str, Any] | None:
        """Fetch LCSC product details by product code (e.g. C2040).

        Returns a normalized dict of product info, or None if not found/failed.
        Results (including None) are cached for the session.
        """
        product_code = str(product_code).strip().upper()
        if not re.match(r"^C\d{4,}$", product_code):
            raise ValueError(f"Invalid LCSC product code: {product_code!r}")

        if product_code in self._cache:
            return self._cache[product_code]

        url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={product_code}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("LCSC fetch failed for %s: %s", product_code, exc)
            self._cache[product_code] = None
            return None

        result_data = data.get("result") if isinstance(data, dict) else None
        if not result_data or not isinstance(result_data, dict):
            logger.warning("LCSC returned no result for %s", product_code)
            self._cache[product_code] = None
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

        product = {
            "productCode": result_data.get("productCode", product_code),
            "title": result_data.get("title", "") or result_data.get("productIntroEn", ""),
            "manufacturer": result_data.get("brandNameEn", ""),
            "mpn": result_data.get("productModel", ""),
            "package": result_data.get("encapStandard", ""),
            "description": result_data.get("productIntroEn", ""),
            "stock": result_data.get("stockNumber", 0),
            "prices": prices,
            "imageUrl": image_url,
            "pdfUrl": result_data.get("pdfUrl", ""),
            "lcscUrl": f"https://www.lcsc.com/product-detail/{product_code}.html",
            "category": cat_name,
            "subcategory": subcat_name,
            "attributes": attributes,
            "provider": "lcsc",
            "_debug": result_data,
        }

        self._cache[product_code] = product
        return product
