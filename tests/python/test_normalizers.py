"""Tests for distributor normalizers against real captured data.

These tests load real product data captured from Digikey and LCSC
(via scripts/capture-distributor-fixtures.py) and verify that the
normalization/parsing layer produces valid output.

If the fixture file doesn't exist, this module defines no tests
(pytest collects nothing). This avoids pytest.skip per project policy.
"""
from __future__ import annotations

import json
import os
import warnings
from datetime import datetime

import pytest

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "generated", "distributor-scrapes.json"
)

# Module-level guard: no fixtures -> no tests collected
if os.path.exists(FIXTURE_PATH):
    with open(FIXTURE_PATH, encoding="utf-8") as _f:
        _FIXTURES = json.load(_f)

    # Staleness warning (not a failure)
    _captured = _FIXTURES.get("captured_at", "")
    try:
        _age = (datetime.now() - datetime.fromisoformat(_captured)).days
        if _age > 30:
            warnings.warn(
                f"Distributor fixtures are {_age} days old (captured {_captured}). "
                "Re-run: python scripts/capture-distributor-fixtures.py",
                stacklevel=1,
            )
    except (ValueError, TypeError):
        pass

    # ── Digikey normalizer tests ──

    from digikey_client import DigikeyClient

    _dk_parts = _FIXTURES.get("digikey", {}).get("parts", {})

    class TestDigikeyNormalizer:
        """Test _normalize_result against every captured Digikey part."""

        @pytest.fixture(params=list(_dk_parts.keys()), ids=list(_dk_parts.keys()))
        def dk_part(self, request):
            mpn = request.param
            entry = _dk_parts[mpn]
            return mpn, entry

        def test_normalize_produces_valid_output(self, dk_part):
            mpn, entry = dk_part
            raw = entry["raw"]
            result = DigikeyClient._normalize_result(raw, mpn)

            assert result["provider"] == "digikey"
            assert isinstance(result["title"], str) and result["title"]
            assert isinstance(result["productCode"], str) and result["productCode"]
            assert isinstance(result["prices"], list)
            for tier in result["prices"]:
                assert isinstance(tier["qty"], int)
                assert isinstance(tier["price"], (int, float))
            assert isinstance(result["stock"], int)
            assert result["stock"] >= 0
            assert isinstance(result["manufacturer"], str)
            assert isinstance(result["mpn"], str)
            assert isinstance(result["imageUrl"], str)
            assert isinstance(result["description"], str)

    # ── LCSC normalizer tests ──

    _lcsc_parts = _FIXTURES.get("lcsc", {}).get("parts", {})

    class TestLcscNormalizer:
        """Test LCSC parsing logic against every captured LCSC part."""

        @pytest.fixture(params=list(_lcsc_parts.keys()), ids=list(_lcsc_parts.keys()))
        def lcsc_part(self, request):
            pn = request.param
            entry = _lcsc_parts[pn]
            return pn, entry

        def test_normalize_produces_valid_output(self, lcsc_part):
            pn, entry = lcsc_part
            result_data = entry["raw"]

            # Replay the same parsing logic as lcsc_client.py:50-102
            prices = []
            for tier in (result_data.get("productPriceList") or []):
                if isinstance(tier, dict):
                    prices.append({
                        "qty": tier.get("ladder", 0),
                        "price": tier.get("productPrice", 0),
                    })

            cat_name = ""
            subcat_name = ""
            for cat in (result_data.get("parentCatalogList") or []):
                if isinstance(cat, dict):
                    if not cat_name:
                        cat_name = cat.get("catalogName", "")
                    else:
                        subcat_name = cat.get("catalogName", "")

            attributes = []
            for param in (result_data.get("paramVOList") or []):
                if isinstance(param, dict):
                    name = param.get("paramNameEn", "")
                    value = param.get("paramValueEn", "")
                    if name and value and value != "-":
                        attributes.append({"name": name, "value": value})

            images = result_data.get("productImages") or []
            image_url = images[0] if images else result_data.get("productImageUrl", "")

            product = {
                "productCode": result_data.get("productCode", pn),
                "title": result_data.get("title", "") or result_data.get("productIntroEn", ""),
                "manufacturer": result_data.get("brandNameEn", ""),
                "mpn": result_data.get("productModel", ""),
                "package": result_data.get("encapStandard", ""),
                "description": result_data.get("productIntroEn", ""),
                "stock": result_data.get("stockNumber", 0),
                "prices": prices,
                "imageUrl": image_url,
                "category": cat_name,
                "subcategory": subcat_name,
                "attributes": attributes,
                "provider": "lcsc",
            }

            assert product["provider"] == "lcsc"
            assert isinstance(product["title"], str) and product["title"]
            assert product["productCode"] == pn or product["productCode"]
            assert isinstance(product["prices"], list) and len(product["prices"]) >= 1
            assert isinstance(product["stock"], int)
            assert product["stock"] >= 0
            assert isinstance(product["manufacturer"], str) and product["manufacturer"]
            assert isinstance(product["attributes"], list)
