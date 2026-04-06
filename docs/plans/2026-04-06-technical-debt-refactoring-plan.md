# Technical Debt Refactoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce codebase complexity to improve AI-assisted development — smaller files, less duplication, clearer state management — while avoiding merge conflicts with concurrent Claude work.

**Architecture:** Bottom-up approach: Phase 1 creates new shared modules (zero conflict risk), Phase 2 touches config/CSS (near-zero risk), Phase 3 splits large JS panel files, Phase 4 splits large Python files after other branches merge.

**Tech Stack:** Python 3.12, vanilla JS (ES modules), CSS, GitHub Actions

**Design spec:** `docs/plans/2026-04-06-technical-debt-refactoring-design.md`

---

## Phase 1 — New Files Only (Zero Conflict Risk)

### Task 1: Consolidate price derivation in price_ops.py

**Files:**
- Modify: `price_ops.py`
- Test: `tests/python/test_price_ops.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/python/test_price_ops.py — append to existing file

class TestDeriveMissingPrice:
    def test_derive_ext_from_unit_and_qty(self):
        unit, ext = derive_missing_price(2.50, None, 10)
        assert unit == 2.50
        assert ext == 25.00

    def test_derive_unit_from_ext_and_qty(self):
        unit, ext = derive_missing_price(None, 25.00, 10)
        assert unit == 2.50
        assert ext == 25.00

    def test_both_provided_returns_unchanged(self):
        unit, ext = derive_missing_price(3.00, 30.00, 10)
        assert unit == 3.00
        assert ext == 30.00

    def test_neither_provided_returns_nones(self):
        unit, ext = derive_missing_price(None, None, 10)
        assert unit is None
        assert ext is None

    def test_zero_qty_does_not_divide(self):
        unit, ext = derive_missing_price(None, 25.00, 0)
        assert unit is None
        assert ext == 25.00

    def test_zero_unit_price_returns_unchanged(self):
        unit, ext = derive_missing_price(0.0, None, 10)
        assert unit == 0.0
        assert ext is None
```

Add import at top of test file:
```python
from price_ops import derive_missing_price
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/python/test_price_ops.py::TestDeriveMissingPrice -v`
Expected: FAIL with `ImportError: cannot import name 'derive_missing_price'`

- [ ] **Step 3: Implement derive_missing_price**

Add to `price_ops.py` after the existing `ensure_parsed` function:

```python
def derive_missing_price(
    unit_price: float | None,
    ext_price: float | None,
    qty: int,
) -> tuple[float | None, float | None]:
    """Fill in whichever of unit/ext is missing given the other + qty.

    Returns (unit_price, ext_price) with the missing value derived,
    or unchanged if both are provided, both are None, or qty is 0.
    """
    if unit_price is not None and ext_price is None and qty > 0:
        ext_price = unit_price * qty
    elif ext_price is not None and unit_price is None and qty > 0:
        unit_price = ext_price / qty
    return unit_price, ext_price
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/python/test_price_ops.py::TestDeriveMissingPrice -v`
Expected: all 6 PASS

- [ ] **Step 5: Replace duplication in inventory_ops.py**

In `inventory_ops.py`, add import at the top (near other imports):
```python
from price_ops import derive_missing_price
```

Replace lines 79-87:
```python
    # Derive missing price from the other price field + qty
    for part in merged.values():
        up = parse_price(part.get("Unit Price($)"))
        ext = parse_price(part.get("Ext.Price($)"))
        qty = parse_qty(part.get("Quantity"))
        if up == 0.0 and ext > 0 and qty > 0:
            part["Unit Price($)"] = f"{ext / qty:.4f}"
        elif ext == 0.0 and up > 0 and qty > 0:
            part["Ext.Price($)"] = f"{up * qty:.2f}"
```

With:
```python
    # Derive missing price from the other price field + qty
    for part in merged.values():
        up = parse_price(part.get("Unit Price($)")) or None
        ext = parse_price(part.get("Ext.Price($)")) or None
        qty = parse_qty(part.get("Quantity"))
        up, ext = derive_missing_price(up, ext, qty)
        if up is not None:
            part["Unit Price($)"] = f"{up:.4f}"
        if ext is not None:
            part["Ext.Price($)"] = f"{ext:.2f}"
```

- [ ] **Step 6: Replace duplication in inventory_api.py**

In `inventory_api.py`, add import near the top:
```python
from price_ops import derive_missing_price
```

Replace lines 427-434 in `update_part_price()`:
```python
                if unit_price is not None and ext_price is None and qty > 0:
                    ext_price = unit_price * qty
                elif ext_price is not None and unit_price is None and qty > 0:
                    unit_price = ext_price / qty
                if unit_price is not None:
                    row["Unit Price($)"] = f"{unit_price:.4f}"
                if ext_price is not None:
                    row["Ext.Price($)"] = f"{ext_price:.2f}"
```

With:
```python
                unit_price, ext_price = derive_missing_price(unit_price, ext_price, qty)
                if unit_price is not None:
                    row["Unit Price($)"] = f"{unit_price:.4f}"
                if ext_price is not None:
                    row["Ext.Price($)"] = f"{ext_price:.2f}"
```

- [ ] **Step 7: Run full test suite to verify no regressions**

Run: `pytest tests/python/ -v`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add price_ops.py inventory_ops.py inventory_api.py tests/python/test_price_ops.py
git commit -m "refactor: consolidate price derivation into price_ops.derive_missing_price

[ci: python]"
```

---

### Task 2: Extract shared HTML product parser

**Files:**
- Create: `html_product_parser.py`
- Create: `tests/python/test_html_product_parser.py`
- Modify: `mouser_client.py`
- Modify: `pololu_client.py`

- [ ] **Step 1: Write failing tests for extraction helpers**

Create `tests/python/test_html_product_parser.py`:

```python
"""Tests for shared HTML product parsing utilities."""

from html_product_parser import (
    extract_jsonld_product,
    extract_title,
    extract_description,
    extract_image_url,
    extract_prices_from_jsonld,
    extract_stock_from_jsonld,
    extract_manufacturer,
    extract_mpn,
    extract_attributes,
)


class TestExtractJsonldProduct:
    def test_extracts_product_from_script_tag(self):
        html = '<script type="application/ld+json">{"@type":"Product","name":"Widget"}</script>'
        result = extract_jsonld_product(html)
        assert result["name"] == "Widget"

    def test_extracts_from_array(self):
        html = '<script type="application/ld+json">[{"@type":"Organization"},{"@type":"Product","name":"Gadget"}]</script>'
        result = extract_jsonld_product(html)
        assert result["name"] == "Gadget"

    def test_returns_none_for_no_product(self):
        html = '<script type="application/ld+json">{"@type":"Organization"}</script>'
        assert extract_jsonld_product(html) is None

    def test_returns_none_for_no_jsonld(self):
        assert extract_jsonld_product("<html><body>nothing</body></html>") is None

    def test_handles_multiple_jsonld_blocks(self):
        html = (
            '<script type="application/ld+json">{"@type":"Organization"}</script>'
            '<script type="application/ld+json">{"@type":"Product","name":"Second"}</script>'
        )
        result = extract_jsonld_product(html)
        assert result["name"] == "Second"


class TestExtractTitle:
    def test_from_jsonld(self):
        assert extract_title("<h1>Fallback</h1>", {"name": "JSON Title"}) == "JSON Title"

    def test_from_h1_fallback(self):
        assert extract_title("<h1>Page Title</h1>", None) == "Page Title"

    def test_strips_inner_html(self):
        assert extract_title("<h1><span>Bold</span> Title</h1>", None) == "Bold Title"

    def test_unescapes_entities(self):
        assert extract_title("<h1>R&amp;D</h1>", None) == "R&D"

    def test_returns_empty_for_no_title(self):
        assert extract_title("<div>no title</div>", None) == ""


class TestExtractDescription:
    def test_from_jsonld(self):
        assert extract_description("", {"description": "From JSON"}) == "From JSON"

    def test_from_meta_tag(self):
        html = '<meta name="description" content="Meta desc">'
        assert extract_description(html, None) == "Meta desc"

    def test_returns_empty_for_none(self):
        assert extract_description("<html></html>", None) == ""


class TestExtractImageUrl:
    def test_from_jsonld_string(self):
        assert extract_image_url("", {"image": "https://example.com/img.jpg"}) == "https://example.com/img.jpg"

    def test_from_jsonld_array(self):
        assert extract_image_url("", {"image": ["https://a.com/1.jpg", "https://a.com/2.jpg"]}) == "https://a.com/1.jpg"

    def test_from_og_image(self):
        html = '<meta property="og:image" content="https://example.com/og.jpg">'
        assert extract_image_url(html, None) == "https://example.com/og.jpg"

    def test_fixes_protocol_relative(self):
        assert extract_image_url("", {"image": "//cdn.example.com/img.jpg"}) == "https://cdn.example.com/img.jpg"


class TestExtractPricesFromJsonld:
    def test_single_offer(self):
        jsonld = {"offers": {"price": "2.50"}}
        assert extract_prices_from_jsonld(jsonld) == [{"qty": 1, "price": 2.50}]

    def test_multiple_offers(self):
        jsonld = {"offers": [{"price": "1.00"}, {"price": "0.80"}]}
        result = extract_prices_from_jsonld(jsonld)
        assert len(result) == 2

    def test_no_offers(self):
        assert extract_prices_from_jsonld({}) == []
        assert extract_prices_from_jsonld(None) == []

    def test_invalid_price_skipped(self):
        jsonld = {"offers": {"price": "call"}}
        assert extract_prices_from_jsonld(jsonld) == []


class TestExtractStockFromJsonld:
    def test_in_stock(self):
        jsonld = {"offers": {"availability": "https://schema.org/InStock"}}
        assert extract_stock_from_jsonld(jsonld) == 1

    def test_out_of_stock(self):
        jsonld = {"offers": {"availability": "https://schema.org/OutOfStock"}}
        assert extract_stock_from_jsonld(jsonld) == 0

    def test_list_offers(self):
        jsonld = {"offers": [{"availability": "InStock"}]}
        assert extract_stock_from_jsonld(jsonld) == 1

    def test_no_offers(self):
        assert extract_stock_from_jsonld({}) == 0


class TestExtractManufacturer:
    def test_from_dict(self):
        assert extract_manufacturer({"brand": {"name": "Texas Instruments"}}) == "Texas Instruments"

    def test_from_string(self):
        assert extract_manufacturer({"brand": "TI"}) == "TI"

    def test_no_brand(self):
        assert extract_manufacturer({}) == ""
        assert extract_manufacturer(None) == ""


class TestExtractMpn:
    def test_from_mpn_field(self):
        assert extract_mpn({"mpn": "LM7805"}) == "LM7805"

    def test_from_sku_fallback(self):
        assert extract_mpn({"sku": "SKU123"}) == "SKU123"

    def test_with_explicit_fallback(self):
        assert extract_mpn({}, fallback="FALL") == "FALL"

    def test_no_mpn(self):
        assert extract_mpn({}) == ""


class TestExtractAttributes:
    def test_extracts_table_rows(self):
        html = "<table><tr><th>Voltage</th><td>5V</td></tr><tr><th>Current</th><td>1A</td></tr></table>"
        attrs = extract_attributes(html)
        assert {"name": "Voltage", "value": "5V"} in attrs
        assert {"name": "Current", "value": "1A"} in attrs

    def test_excludes_price_rows(self):
        html = "<table><tr><th>Price</th><td>$5</td></tr><tr><th>Voltage</th><td>5V</td></tr></table>"
        attrs = extract_attributes(html, excluded_names=["price"])
        names = [a["name"] for a in attrs]
        assert "Price" not in names

    def test_unescapes_html(self):
        html = "<table><tr><th>R&amp;D</th><td>100&deg;C</td></tr></table>"
        attrs = extract_attributes(html)
        assert attrs[0]["name"] == "R&D"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/python/test_html_product_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'html_product_parser'`

- [ ] **Step 3: Implement html_product_parser.py**

Create `html_product_parser.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/python/test_html_product_parser.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit the new module and tests**

```bash
git add html_product_parser.py tests/python/test_html_product_parser.py
git commit -m "feat: add shared HTML product parsing utilities

Extracts common JSON-LD, title, image, price, stock, and attribute
parsing into reusable functions for mouser and pololu clients.

[ci: python]"
```

- [ ] **Step 6: Refactor mouser_client.py to use shared parser**

Replace the `_parse_product_page` method in `mouser_client.py`. Keep only:
- The method signature (unchanged)
- Mouser-specific volume pricing regex (`r'(\d[\d,]*)\+?\s*\$(\d+\.?\d*)'`)
- Mouser-specific stock regex (`r'(\d[\d,]*)\s+[Ii]n\s+[Ss]tock'`)
- Mouser-specific breadcrumb regex
- PDF URL extraction
- Final product dict assembly

Add import at top:
```python
from html_product_parser import (
    extract_jsonld_product,
    extract_title,
    extract_description,
    extract_image_url,
    extract_prices_from_jsonld,
    extract_stock_from_jsonld,
    extract_manufacturer,
    extract_mpn,
    extract_attributes,
)
```

Remove `import html as html_mod` and `import json` (no longer needed directly).

The new `_parse_product_page` should be ~60 lines (down from ~190):

```python
@staticmethod
def _parse_product_page(page_html: str, part_number: str, url: str) -> dict[str, Any] | None:
    jsonld = extract_jsonld_product(page_html)

    title = extract_title(page_html, jsonld)
    if not title:
        return None

    description = extract_description(page_html, jsonld)
    image_url = extract_image_url(page_html, jsonld)
    prices = extract_prices_from_jsonld(jsonld)
    manufacturer = extract_manufacturer(jsonld)
    mpn = extract_mpn(jsonld)

    # Mouser-specific: volume pricing from page text
    for qty_str, price_str in re.findall(r'(\d[\d,]*)\+?\s*\$(\d+\.?\d*)', page_html):
        try:
            qty = int(qty_str.replace(",", ""))
            price = float(price_str)
            if not any(p["qty"] == qty for p in prices):
                prices.append({"qty": qty, "price": price})
        except (ValueError, TypeError):
            pass
    prices.sort(key=lambda p: p["qty"])

    # Stock
    stock = extract_stock_from_jsonld(jsonld)
    stock_match = re.search(r'(\d[\d,]*)\s+[Ii]n\s+[Ss]tock', page_html)
    if stock_match:
        try:
            stock = int(stock_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # PDF URL
    pdf_url = ""
    pdf_match = re.search(r'href="([^"]*\.pdf[^"]*)"', page_html, re.IGNORECASE)
    if pdf_match:
        pdf_url = pdf_match.group(1)
        if pdf_url.startswith("//"):
            pdf_url = "https:" + pdf_url

    # Breadcrumbs
    category, subcategory = "", ""
    breadcrumb_matches = re.findall(
        r'<a[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>([^<]+)</a>', page_html,
    )
    if not breadcrumb_matches:
        breadcrumb_matches = re.findall(
            r'<li[^>]*class="[^"]*breadcrumb[^"]*"[^>]*>[^<]*<a[^>]*>([^<]+)</a>', page_html,
        )
    if breadcrumb_matches:
        crumbs = [c.strip() for c in breadcrumb_matches if c.strip().lower() not in ("home", "mouser", "")]
        if crumbs:
            category = crumbs[-1]
        if len(crumbs) >= 2:
            subcategory = crumbs[-2]

    attributes = extract_attributes(page_html, excluded_names=["quantity", "price", "unit price"])

    return {
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
        "_debug": {"url": url, "part_number": part_number, "jsonld": jsonld},
    }
```

- [ ] **Step 7: Run mouser tests**

Run: `pytest tests/python/test_clients.py::TestMouserClient -v`
Expected: all PASS

- [ ] **Step 8: Refactor pololu_client.py to use shared parser**

Same pattern. Add import, remove `import html`, `import json`. Replace `_parse_product_page` with ~55 lines:

```python
@staticmethod
def _parse_product_page(page_html: str, sku: str, url: str) -> dict[str, Any] | None:
    jsonld = extract_jsonld_product(page_html)

    title = extract_title(page_html, jsonld)
    if not title:
        return None

    description = extract_description(page_html, jsonld)
    image_url = extract_image_url(page_html, jsonld)
    prices = extract_prices_from_jsonld(jsonld)
    manufacturer = extract_manufacturer(jsonld)
    mpn = extract_mpn(jsonld, fallback=sku)

    # Pololu-specific: volume pricing from HTML table
    for qty_str, price_str in re.findall(
        r"<tr>\s*<td>\s*(\d+)\s*</td>\s*<td[^>]*>\s*(\d+\.?\d*)\s*</td>\s*</tr>",
        page_html,
    ):
        try:
            qty = int(qty_str)
            price = float(price_str)
            if qty > 0 and price > 0 and not any(p["qty"] == qty for p in prices):
                prices.append({"qty": qty, "price": price})
        except (ValueError, TypeError):
            pass
    prices.sort(key=lambda p: p["qty"])

    # Stock
    stock = extract_stock_from_jsonld(jsonld)
    stock_match = re.search(r"""data-available-stock=['"](\d+)['"]""", page_html)
    if not stock_match:
        stock_match = re.search(r'(\d[\d,]*)\s+in\s+stock', page_html, re.IGNORECASE)
    if stock_match:
        try:
            stock = int(stock_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # Breadcrumbs
    category, subcategory = "", ""
    breadcrumb_matches = re.findall(
        r'<a[^>]*class="[^"]*crumb[^"]*"[^>]*>([^<]+)</a>', page_html,
    )
    if breadcrumb_matches:
        crumbs = [c.strip() for c in breadcrumb_matches if c.strip().lower() not in ("home", "pololu")]
        if crumbs:
            category = crumbs[0]
        if len(crumbs) >= 2:
            subcategory = crumbs[1]

    attributes = extract_attributes(page_html, excluded_names=["quantity", "price"])

    return {
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
        "_debug": {"url": url, "sku": sku, "jsonld": jsonld},
    }
```

- [ ] **Step 9: Run all client tests**

Run: `pytest tests/python/test_clients.py -v`
Expected: all PASS

- [ ] **Step 10: Run ruff to verify lint**

Run: `ruff check html_product_parser.py mouser_client.py pololu_client.py`
Expected: no errors

- [ ] **Step 11: Commit**

```bash
git add mouser_client.py pololu_client.py
git commit -m "refactor: mouser + pololu clients use shared html_product_parser

Removes ~180 lines of duplication. Each client now only contains
site-specific parsing (volume pricing, stock regex, breadcrumbs).

[ci: python]"
```

---

### Task 3: Add distributor base class

**Files:**
- Create: `base_client.py`
- Create: `tests/python/test_base_client.py`
- Modify: `lcsc_client.py` (add inheritance)
- Modify: `mouser_client.py` (add inheritance)
- Modify: `pololu_client.py` (add inheritance)

- [ ] **Step 1: Write failing test**

Create `tests/python/test_base_client.py`:

```python
"""Tests for BaseProductClient protocol."""

import pytest

from base_client import BaseProductClient


class DummyClient(BaseProductClient):
    provider = "dummy"

    def fetch_product(self, identifier: str) -> dict | None:
        return {"title": identifier, "provider": "dummy"}


class IncompleteClient(BaseProductClient):
    provider = "incomplete"


def test_dummy_client_fetch():
    client = DummyClient()
    result = client.fetch_product("ABC")
    assert result == {"title": "ABC", "provider": "dummy"}


def test_incomplete_client_raises():
    with pytest.raises(TypeError):
        IncompleteClient()


def test_subclass_check():
    assert issubclass(DummyClient, BaseProductClient)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/python/test_base_client.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement base_client.py**

```python
"""Abstract base class for distributor product clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProductClient(ABC):
    """Base class for distributor product clients.

    Subclasses must define:
      - provider: str — distributor name (e.g. "lcsc", "mouser")
      - fetch_product(identifier) — fetch and return product info
    """

    provider: str

    @abstractmethod
    def fetch_product(self, identifier: str) -> dict[str, Any] | None:
        """Fetch product details by identifier.

        Returns a normalized dict of product info, or None if not found/failed.
        """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/python/test_base_client.py -v`
Expected: all 3 PASS

- [ ] **Step 5: Add inheritance to existing clients**

In `lcsc_client.py`, add import and change class declaration:
```python
from base_client import BaseProductClient
```
```python
class LcscClient(BaseProductClient):
    """Fetches and caches LCSC product details by product code."""
    provider = "lcsc"
```

In `mouser_client.py`:
```python
from base_client import BaseProductClient
```
```python
class MouserClient(BaseProductClient):
    """Fetches and caches Mouser product details by part number."""
    provider = "mouser"
```

In `pololu_client.py`:
```python
from base_client import BaseProductClient
```
```python
class PololuClient(BaseProductClient):
    """Fetches and caches Pololu product details by SKU number."""
    provider = "pololu"
```

Note: `digikey_client.py` is complex (browser automation); defer its inheritance to Phase 4.

- [ ] **Step 6: Run all client tests**

Run: `pytest tests/python/test_clients.py tests/python/test_base_client.py -v`
Expected: all PASS

- [ ] **Step 7: Run ruff lint**

Run: `ruff check base_client.py lcsc_client.py mouser_client.py pololu_client.py`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add base_client.py tests/python/test_base_client.py lcsc_client.py mouser_client.py pololu_client.py
git commit -m "refactor: add BaseProductClient ABC, wire lcsc/mouser/pololu

Establishes a common interface for distributor clients.
Digikey deferred to Phase 4 due to browser automation complexity.

[ci: python]"
```

---

## Phase 2 — Config & CSS (Near-Zero Conflict Risk)

### Task 4: Expand ESLint rules

**Files:**
- Modify: `eslint.config.mjs`
- Modify: JS files in `js/` (mechanical fixes from auto-fix)

- [ ] **Step 1: Add rules to eslint.config.mjs**

Replace the `rules` block:

```javascript
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["warn", { vars: "local", args: "none" }],
      "eqeqeq": ["error", "always"],
      "no-throw-literal": "error",
      "prefer-const": "warn",
    },
```

- [ ] **Step 2: Run eslint with auto-fix**

Run: `npx eslint js/ --fix`

This will auto-fix `prefer-const` (let → const where never reassigned). Review the output — `eqeqeq` violations may need manual fixes.

- [ ] **Step 3: Fix remaining eqeqeq violations manually**

Review each `==` / `!=` and replace with `===` / `!==`. Check that `null` comparisons that intentionally catch both `null` and `undefined` use `== null` → explicitly check both, or leave as `== null` and add `// eslint-disable-next-line eqeqeq` with a comment explaining why.

- [ ] **Step 4: Run eslint to verify clean**

Run: `npx eslint js/`
Expected: 0 errors, 0 warnings (or only pre-existing no-unused-vars warnings)

- [ ] **Step 5: Run vitest to verify no regressions**

Run: `npx vitest run`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add eslint.config.mjs js/
git commit -m "refactor: expand ESLint rules (eqeqeq, prefer-const, no-throw-literal)

Mechanical auto-fixes for let→const, manual fixes for == vs ===.

[ci: js]"
```

---

### Task 5: CSS button deduplication

**Files:**
- Modify: `css/buttons.css`
- Modify: `index.html` (add base classes to button elements)

- [ ] **Step 1: Define button size tiers in buttons.css**

Add at the top of `css/buttons.css`, before existing rules:

```css
/* ── Button base tiers ────────────────────────────────── */

.btn-sm {
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--border-default);
  cursor: pointer;
  font-size: 10px;
  transition: all 0.15s;
  background: transparent;
  color: var(--text-primary);
}

.btn-md {
  padding: 3px 12px;
  border-radius: 6px;
  border: 1px solid var(--border-default);
  cursor: pointer;
  font-size: 12px;
  transition: all 0.15s;
  background: transparent;
  color: var(--text-primary);
}

.btn-lg {
  padding: 6px 16px;
  border-radius: 6px;
  border: 1px solid var(--border-default);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.15s;
  background: transparent;
  color: var(--text-primary);
}
```

- [ ] **Step 2: Add tier classes to HTML buttons**

In `index.html`, add appropriate tier class to each button:
- `.adj-btn`, `.link-btn`, `.confirm-btn`, `.unconfirm-btn`, `.swap-btn` → add `btn-sm`
- `.prefs-btn`, `.rebuild-btn`, `.save-bom-btn`, `.consume-btn`, `.clear-bom-btn`, `.filter-btn` → add `btn-md`
- `.btn` (generic) → add `btn-lg`

For dynamically created buttons in JS files (e.g. in renderers), add the tier class alongside the existing class. Search for `class="adj-btn"`, `class="link-btn"`, etc. and add the tier.

- [ ] **Step 3: Remove duplicated base properties from individual button rules**

For each button class in `buttons.css`, remove the properties that are now inherited from the tier class (padding, border-radius, border, cursor, font-size, transition, background, color). Keep only the unique properties (specific border-color, background-color, hover states).

For example, `.consume-btn` keeps only:
```css
.consume-btn {
  border-color: #f0883e40;
  background: #f0883e20;
}
.consume-btn:hover {
  background: #f0883e40;
  border-color: #f0883e;
}
```

- [ ] **Step 4: Verify visually**

Run the app and visually check that all buttons look unchanged.

Run: `npx vitest run` (in case any snapshot or style-audit tests catch differences)

- [ ] **Step 5: Commit**

```bash
git add css/buttons.css index.html js/
git commit -m "refactor: deduplicate CSS button styles into btn-sm/md/lg tiers

Reduces ~80 lines of repeated padding/border/font-size declarations.

[ci: js]"
```

---

### Task 6: CI matrix DRY-up

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Replace js-ubuntu + js-macos with matrix job**

Replace the two separate jobs with:

```yaml
  js:
    needs: [parse-tags, guard]
    if: >-
      contains(fromJSON(needs.parse-tags.outputs.suites), 'js')
      || contains(fromJSON(needs.parse-tags.outputs.suites), 'all')
    strategy:
      matrix:
        include:
          - runner: [self-hosted, pnp-testbox]
            name: ubuntu
            run-e2e: true
          - runner: [self-hosted, m4-air]
            name: macos
            run-e2e: false
    runs-on: ${{ matrix.runner }}
    name: js-${{ matrix.name }}
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - name: Lint + Type-check
        run: npx eslint js/ && npx tsc --noEmit
      - name: Unit tests
        run: npx vitest run --project core
      - name: Install Playwright browsers
        if: matrix.run-e2e
        run: npx playwright install chromium --with-deps
      - name: E2E tests
        if: matrix.run-e2e
        run: npx playwright test tests/js/e2e/ --project=functional
```

- [ ] **Step 2: Replace python-ubuntu + python-macos with matrix job**

```yaml
  python:
    needs: [parse-tags, guard]
    if: >-
      contains(fromJSON(needs.parse-tags.outputs.suites), 'python')
      || contains(fromJSON(needs.parse-tags.outputs.suites), 'all')
    strategy:
      matrix:
        include:
          - runner: [self-hosted, pnp-testbox]
            name: ubuntu
            venv: ~/dubis-venv/bin
          - runner: [self-hosted, m4-air]
            name: macos
            venv: ~/dubis-venv/bin
    runs-on: ${{ matrix.runner }}
    name: python-${{ matrix.name }}
    timeout-minutes: 3
    steps:
      - uses: actions/checkout@v4
      - name: Lint
        run: ${{ matrix.venv }}/ruff check .
      - name: Check test fixtures
        run: ${{ matrix.venv }}/python scripts/generate-test-fixtures.py --check
      - name: Unit tests
        run: ${{ matrix.venv }}/pytest tests/python/ -v
```

- [ ] **Step 3: Replace pnp-e2e + pnp-e2e-macos with matrix job**

Same pattern. Use matrix for the runner, keep the same steps.

- [ ] **Step 4: Update job references**

Update `needs:` references in downstream jobs (cross-compute) to point to the new matrix job names.

- [ ] **Step 5: Verify CI syntax**

Run: `gh workflow view ci.yml` or validate YAML syntax locally.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "refactor: DRY up CI with matrix strategy for ubuntu/macos pairs

Removes ~80 lines of duplicated job definitions.

[ci: lint]"
```

---

## Phase 3 — JS Panel Splits (Safe Zone)

### Task 7: Split bom-panel.js into focused modules

**Files:**
- Create: `js/bom/bom-state.js`
- Create: `js/bom/bom-events.js`
- Modify: `js/bom/bom-panel.js` (slim down to ~80 lines)
- Modify: `js/app-init.js` (import path unchanged — still `./bom/bom-panel.js`)

- [ ] **Step 1: Create bom-state.js**

Create `js/bom/bom-state.js`:

```javascript
/**
 * BOM panel state — centralized module-level state with getters/setters.
 * All BOM state lives here instead of scattered lets in bom-panel.js.
 */

/** @enum {string} */
export const ConsumeState = Object.freeze({
  IDLE: "idle",
  ARMED: "armed",
});

const state = {
  /** @type {HTMLElement} */
  body: document.getElementById("bom-body"),
  /** @type {object|null} */
  lastResults: null,
  /** @type {string} */
  lastFileName: "",
  /** @type {Array} */
  bomRawRows: [],
  /** @type {Array} */
  bomHeaders: [],
  /** @type {object} */
  bomCols: {},
  /** @type {boolean} */
  bomDirty: false,
  /** @type {string} */
  consumeState: ConsumeState.IDLE,
  /** @type {object|null} */
  lastConsumeMeta: null,
  /** @type {import('../ui-helpers.js').ModalInstance|null} */
  consumeModal: null,
};

export default state;
```

- [ ] **Step 2: Create bom-events.js**

Create `js/bom/bom-events.js` — extract init()'s event listener setup (lines 264-552 of current bom-panel.js). This file exports a single `setupEvents()` function that receives the functions it delegates to:

```javascript
/**
 * BOM panel event wiring — all addEventListener and EventBus.on calls.
 */
import { EventBus, Events } from '../event-bus.js';
import { api, AppLog } from '../api.js';
import { showToast, resetDropZoneInput } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { App, snapshotLinks, savePreferences } from '../store.js';
import { generateCSV } from '../csv-parser.js';
import { bomKey } from '../part-keys.js';
import state, { ConsumeState } from './bom-state.js';

/**
 * Wire up all event listeners for the BOM panel.
 * @param {object} handlers — functions from bom-panel.js
 * @param {Function} handlers.reprocessAndRender
 * @param {Function} handlers.renderBomPanel
 * @param {Function} handlers.emitBomData
 * @param {Function} handlers.loadBomText
 * @param {Function} handlers.browseBomFile
 * @param {Function} handlers.createManualLink
 * @param {Function} handlers.openConsumeModal
 * @param {Function} handlers.resetConsumeConfirm
 * @param {Function} handlers.getMultiplier
 */
export function setupEvents(handlers) {
  const {
    reprocessAndRender, renderBomPanel, emitBomData, loadBomText,
    browseBomFile, createManualLink, openConsumeModal, resetConsumeConfirm,
    getMultiplier,
  } = handlers;

  // Multiplier input
  state.body.addEventListener("input", (e) => {
    if (e.target.id === "bom-multiplier") emitBomData();
  });

  // Save BOM button
  state.body.addEventListener("click", async (e) => {
    const btn = e.target.closest(".save-bom-btn");
    if (!btn) return;
    const csvText = generateCSV(state.bomHeaders, state.bomRawRows);
    const result = await api("save_file_dialog", csvText, state.lastFileName || "bom.csv");
    if (result) {
      state.bomDirty = false;
      showToast("BOM saved");
    }
  });

  // Clear BOM button
  state.body.addEventListener("click", async (e) => {
    const btn = e.target.closest(".clear-bom-btn");
    if (!btn) return;
    const snap = {
      _undoType: "bom",
      bomRawRows: [...state.bomRawRows],
      bomHeaders: [...state.bomHeaders],
      bomCols: { ...state.bomCols },
      lastFileName: state.lastFileName,
      lastResults: state.lastResults,
      links: snapshotLinks(),
    };
    state.bomRawRows = [];
    state.bomHeaders = [];
    state.bomCols = {};
    state.lastFileName = "";
    state.lastResults = null;
    state.bomDirty = false;
    App.links.clearAll();
    EventBus.emit(Events.BOM_CLEARED);
    reprocessAndRender();
    UndoRedo.push(snap);
  });

  // Consume button
  state.body.addEventListener("click", (e) => {
    if (e.target.closest(".consume-btn")) openConsumeModal();
  });

  // Consume confirm (on document for modal)
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("#consume-confirm-btn");
    if (!btn) return;
    if (state.consumeState !== ConsumeState.ARMED) {
      state.consumeState = ConsumeState.ARMED;
      btn.textContent = "Yes, consume inventory";
      btn.classList.add("armed");
      return;
    }
    // Armed — actually consume
    resetConsumeConfirm();
    state.consumeModal.close();
    const mult = getMultiplier();
    try {
      const result = await api("consume_bom", state.lastResults, mult);
      state.lastConsumeMeta = { results: state.lastResults, multiplier: mult };
      const snap = { _undoType: "consume", meta: state.lastConsumeMeta };
      UndoRedo.push(snap);
      showToast(`Consumed ${result.consumed} parts`);
    } catch (err) {
      AppLog.error("Consume failed:", err);
      showToast("Consume failed — see console", "error");
    }
  });

  // Staging tbody delegation
  const tbodyEl = state.body.querySelector(".bom-staging tbody");
  if (tbodyEl) {
    _setupStagingDelegation(tbodyEl, handlers);
  }

  // UndoRedo registrations
  UndoRedo.register("bom", (snap) => {
    state.bomRawRows = snap.bomRawRows;
    state.bomHeaders = snap.bomHeaders;
    state.bomCols = snap.bomCols;
    state.lastFileName = snap.lastFileName;
    state.lastResults = snap.lastResults;
    App.links.loadFromSaved(snap.links);
    reprocessAndRender();
    if (snap.lastFileName) {
      EventBus.emit(Events.BOM_LOADED, { results: snap.lastResults, fileName: snap.lastFileName });
    } else {
      EventBus.emit(Events.BOM_CLEARED);
    }
  });

  UndoRedo.register("consume", async (snap) => {
    try {
      await api("undo_consume", snap.meta);
      showToast("Consume undone");
    } catch (err) {
      AppLog.error("Undo consume failed:", err);
    }
  });

  // EventBus subscriptions
  EventBus.on(Events.INVENTORY_UPDATED, () => {
    if (state.lastResults) reprocessAndRender();
  });
  EventBus.on(Events.CONFIRMED_CHANGED, () => {
    if (state.lastResults) reprocessAndRender();
  });
  EventBus.on(Events.LINKS_CHANGED, () => {
    if (state.lastResults) reprocessAndRender();
  });
  EventBus.on(Events.LINKING_MODE, () => {
    if (state.lastResults) renderBomPanel(state.lastResults);
  });
  EventBus.on(Events.SAVE_AND_CLOSE, async () => {
    if (state.bomDirty && state.bomRawRows.length) {
      const csvText = generateCSV(state.bomHeaders, state.bomRawRows);
      await api("save_file_dialog", csvText, state.lastFileName || "bom.csv");
    }
    await savePreferences();
    await api("close_window");
  });
  EventBus.on(Events.INVENTORY_LOADED, () => {
    const lastBom = App.preferences?.lastBom;
    if (lastBom && lastBom.fileName) {
      loadBomText(lastBom.text, lastBom.fileName, lastBom.links);
    }
  });
}

function _setupStagingDelegation(tbodyEl, handlers) {
  // Click delegation for staging rows (delete, link, edit refs)
  tbodyEl.addEventListener("click", (e) => {
    // ... extract from current bom-panel.js lines 390-432
    // Keep the exact same delegation logic
  });

  tbodyEl.addEventListener("change", (e) => {
    // ... extract from current bom-panel.js lines 435-449
  });

  tbodyEl.addEventListener("focusout", (e) => {
    // ... extract from current bom-panel.js lines 452-462
  });
}
```

Note: The `_setupStagingDelegation` body should be copied verbatim from the current bom-panel.js lines 388-462. The code above shows the structure; the implementing engineer should copy the exact event handler bodies.

- [ ] **Step 3: Slim down bom-panel.js**

`js/bom/bom-panel.js` becomes the public API (~120 lines):

```javascript
/**
 * BOM panel — public API and core logic functions.
 */
import { api } from '../api.js';
import { setupDropZone } from '../ui-helpers.js';
import { App } from '../store.js';
import { bomKey, invPartKey } from '../part-keys.js';
import { processBOM, aggregateBomRows } from '../csv-parser.js';
import { matchBOM } from '../matching.js';
import { classifyBomRow, countBomWarnings, computeRows, buildStatusMap, buildLinkableKeys, prepareConsumption, computePriceInfo } from './bom-logic.js';
import { renderDropZone, renderLoadedDropZone, renderBomSummary, renderPriceInfo, renderLinkingBanner, renderStagingHead, renderStagingRow } from './bom-renderer.js';
import state, { ConsumeState } from './bom-state.js';
import { setupEvents } from './bom-events.js';
import { Modal } from '../ui-helpers.js';

function updateSaveBtnState() { /* lines 25-28 unchanged */ }
function aggregateFromRawRows() { /* lines 32-34 unchanged */ }
function getMultiplier() { /* lines 50-53 unchanged */ }
function emitBomData() { /* lines 57-62 unchanged */ }

function reprocessAndRender() {
  // lines 38-46 unchanged
}

function renderBomPanel(rows) {
  // lines 66-127 unchanged
}

async function browseBomFile() {
  // lines 131-142 unchanged
}

function handleFile(file) {
  // lines 144-160 unchanged
}

function loadBomText(text, fileName, savedLinks) {
  // lines 162-213 unchanged
}

function createManualLink(bomRow) {
  // lines 217-229 unchanged
}

function resetConsumeConfirm() {
  state.consumeState = ConsumeState.IDLE;
  // rest of lines 236-244 updated to use state.consumeState
}

function openConsumeModal() {
  // lines 251-260 unchanged, using state.consumeModal
}

export function init() {
  renderDropZone(state.body);
  setupDropZone(state.body.querySelector(".bom-drop"), handleFile);

  state.consumeModal = Modal("consume-modal", {
    onClose: resetConsumeConfirm,
  });

  setupEvents({
    reprocessAndRender,
    renderBomPanel,
    emitBomData,
    loadBomText,
    browseBomFile,
    createManualLink,
    openConsumeModal,
    resetConsumeConfirm,
    getMultiplier,
  });
}
```

- [ ] **Step 4: Run JS tests**

Run: `npx vitest run && npx eslint js/`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add js/bom/bom-state.js js/bom/bom-events.js js/bom/bom-panel.js
git commit -m "refactor: split bom-panel.js into state, events, and panel modules

bom-state.js: centralized state with ConsumeState enum
bom-events.js: all event listeners and EventBus subscriptions
bom-panel.js: slim public API (~120 lines, down from 553)

[ci: js]"
```

---

### Task 8: Split inventory-panel.js into focused modules

**Files:**
- Create: `js/inventory/inv-state.js`
- Create: `js/inventory/inv-events.js`
- Modify: `js/inventory/inventory-panel.js` (slim down)

- [ ] **Step 1: Create inv-state.js**

Create `js/inventory/inv-state.js`:

```javascript
/**
 * Inventory panel state — centralized module-level state.
 */

const state = {
  /** @type {HTMLElement} */
  body: document.getElementById("inventory-body"),
  /** @type {HTMLInputElement} */
  searchInput: document.getElementById("inv-search"),
  /** @type {Set<string>} */
  collapsedSections: new Set(),
  /** @type {object|null} */
  bomData: null,
  /** @type {string} */
  activeFilter: "all",
  /** @type {Set<string>} */
  expandedAlts: new Set(),
  /** @type {Map} */
  rowMap: new Map(),
  /** @type {number} */
  DESC_HIDE_WIDTH: 680,
  /** @type {boolean} */
  hideDescs: true,
};

export default state;
```

- [ ] **Step 2: Create inv-events.js**

Create `js/inventory/inv-events.js` — extract init()'s event setup (lines 47-92) and delegated click handlers:

```javascript
/**
 * Inventory panel event wiring.
 */
import { EventBus, Events } from '../event-bus.js';
import { App } from '../store.js';
import state from './inv-state.js';

/**
 * @param {object} handlers
 * @param {Function} handlers.render
 */
export function setupEvents(handlers) {
  const { render } = handlers;

  // ResizeObserver for description column hiding
  new ResizeObserver((entries) => {
    const w = entries[0].contentRect.width;
    const shouldHide = w < state.DESC_HIDE_WIDTH;
    if (shouldHide !== state.hideDescs) {
      state.hideDescs = shouldHide;
      render();
    }
  }).observe(state.body);

  // Search with debounce
  let searchTimer;
  state.searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, 150);
  });

  // EventBus subscriptions
  EventBus.on(Events.INVENTORY_LOADED, render);
  EventBus.on(Events.INVENTORY_UPDATED, render);
  EventBus.on(Events.PREFS_CHANGED, render);
  EventBus.on(Events.BOM_LOADED, (data) => {
    state.bomData = data;
    state.activeFilter = "all";
    state.expandedAlts = new Set();
    render();
  });
  EventBus.on(Events.BOM_CLEARED, () => {
    state.bomData = null;
    state.activeFilter = "all";
    render();
  });
  EventBus.on(Events.LINKING_MODE, render);

  // Escape to cancel linking mode
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && App.links.linkingMode) {
      App.links.setLinkingMode(false, null);
    }
  });
}
```

- [ ] **Step 3: Slim down inventory-panel.js**

Keep only the rendering functions and public API in `inventory-panel.js`. Replace init() with:

```javascript
import state from './inv-state.js';
import { setupEvents } from './inv-events.js';
// ... other existing imports unchanged

export function init() {
  setupEvents({ render });
}

// All render functions stay here unchanged:
// render(), renderNormalInventory(), renderHierarchySection(),
// renderSubSection(), createPartRow(), renderRemainingInventory(),
// renderRemainingNormalSections(), renderSection(), renderBomComparison(),
// handleBomTableClick(), confirmMatch(), unconfirmMatch(), confirmAltMatch()
```

Update all references from `body` to `state.body`, `bomData` to `state.bomData`, etc.

- [ ] **Step 4: Run tests**

Run: `npx vitest run && npx eslint js/`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inv-state.js js/inventory/inv-events.js js/inventory/inventory-panel.js
git commit -m "refactor: split inventory-panel.js into state, events, and panel modules

inv-state.js: centralized state object
inv-events.js: EventBus subscriptions and DOM event setup
inventory-panel.js: rendering + public API

[ci: js]"
```

---

### Task 9: Clean up store.js dual API

**Files:**
- Modify: `js/store.js`
- Modify: Multiple JS files that reference `App.property`
- Modify: `js/app-init.js` (update window globals)

- [ ] **Step 1: Audit all App.property references**

Run a grep to find all `App.` references across JS files:

```bash
npx grep -rn "App\." js/ --include="*.js" | grep -v "AppLog" | grep -v "node_modules"
```

Categorize into:
- **Read-only** (`App.inventory`, `App.bomResults`, `App.preferences`) — replace with store getters
- **Write** (`App.inventory = ...`, `App.bomResults = ...`) — replace with store setters
- **Links** (`App.links.X`) — keep as-is for now, links sub-object is fine

- [ ] **Step 2: Export getter functions from store.js**

Ensure store.js exports all needed getters. Most already exist but verify:

```javascript
export function getInventory() { return inventory; }
export function getBomResults() { return bomResults; }
export function getBomFileName() { return bomFileName; }
export function getBomHeaders() { return bomHeaders; }
export function getBomCols() { return bomCols; }
export function getBomDirty() { return bomDirty; }
export function getPreferences() { return preferences; }
export function isLinkingActive() { return linkingActive; }
export function getLinkingInvItem() { return linkingInvItem; }
export function getLinkingBomRow() { return linkingBomRow; }
```

- [ ] **Step 3: Replace App.X reads in panel files with store imports**

For each file, replace `App.inventory` with `getInventory()`, etc. Add the appropriate imports from store.js.

Do NOT change `App.links.X` references — the links sub-API is fine and heavily used.

- [ ] **Step 4: Simplify the App Proxy**

In store.js, simplify `App` to only expose `links` and properties needed by Python `evaluate_js`:

```javascript
export const App = {
  get inventory() { return inventory; },
  get preferences() { return preferences; },
  links: _linksProxy,
};
```

Remove the full Proxy with get/set traps. The `window.App` global in app-init.js stays for Python interop but is now a thin shim.

- [ ] **Step 5: Run tests**

Run: `npx vitest run && npx eslint js/`
Expected: all pass

- [ ] **Step 6: Run E2E tests if available**

Run: `npx playwright test tests/js/e2e/ --project=functional`
Expected: all pass (this catches any Python evaluate_js breakage)

- [ ] **Step 7: Commit**

```bash
git add js/
git commit -m "refactor: replace App proxy reads with store getter imports

Panels now import getInventory(), getBomResults(), etc. directly.
App object simplified to thin shim for Python evaluate_js interop.

[ci: js]"
```

---

## Phase 4 — Python Splits (After Data-Architecture Merges)

> **Gate:** Do not start Phase 4 until the `worktree-data-architecture-analysis` branch is merged to main. These tasks modify files that branch is actively changing.

### Task 10: Split inventory_api.py — extract distributor_manager.py

**Files:**
- Create: `distributor_manager.py`
- Modify: `inventory_api.py`

- [ ] **Step 1: Create distributor_manager.py**

Extract from `inventory_api.py`:
- `_infer_distributor()` method
- `_infer_distributor_for_key()` method
- `fetch_part_info()` method
- Distributor client initialization (currently in `__init__`)

```python
"""Manages distributor client instances and inference logic."""

from __future__ import annotations

import logging
import re
from typing import Any

from base_client import BaseProductClient
from lcsc_client import LcscClient
from mouser_client import MouserClient
from pololu_client import PololuClient

logger = logging.getLogger(__name__)


class DistributorManager:
    """Manages distributor clients and maps part keys to distributors."""

    def __init__(self) -> None:
        self._lcsc = LcscClient()
        self._mouser = MouserClient()
        self._pololu = PololuClient()
        self._digikey = None  # Lazily initialized (needs webview window)
        self._clients: dict[str, BaseProductClient] = {
            "lcsc": self._lcsc,
            "mouser": self._mouser,
            "pololu": self._pololu,
        }

    def set_digikey(self, digikey_client) -> None:
        """Set the Digikey client (requires webview window)."""
        self._digikey = digikey_client
        self._clients["digikey"] = digikey_client

    def infer_distributor(self, row: dict[str, Any]) -> str:
        """Infer distributor from a purchase ledger row."""
        # Move logic from inventory_api._infer_distributor()
        ...

    def infer_distributor_for_key(self, part_key: str) -> str:
        """Infer distributor from a part key string."""
        # Move logic from inventory_api._infer_distributor_for_key()
        ...

    def fetch_part_info(self, code: str, provider: str | None = None) -> dict[str, Any] | None:
        """Fetch product info from the appropriate distributor."""
        # Move logic from inventory_api.fetch_part_info()
        ...
```

- [ ] **Step 2: Wire into inventory_api.py**

In `inventory_api.py`:
```python
from distributor_manager import DistributorManager
```

Replace client initialization in `__init__` with:
```python
self._distributors = DistributorManager()
```

Replace all `self._lcsc`, `self._mouser`, etc. with `self._distributors.fetch_part_info(...)` or `self._distributors.infer_distributor(...)`.

- [ ] **Step 3: Run tests**

Run: `pytest tests/python/ -v`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add distributor_manager.py inventory_api.py
git commit -m "refactor: extract DistributorManager from inventory_api.py

Moves distributor client management, inference, and fetching into
a dedicated module. inventory_api.py shrinks by ~120 lines.

[ci: python]"
```

---

### Task 11: Split digikey_client.py — extract CDP and normalizer

**Files:**
- Create: `digikey_cdp.py`
- Create: `digikey_normalizer.py`
- Modify: `digikey_client.py`

- [ ] **Step 1: Create digikey_cdp.py**

Extract `_cdp_get_cookies()` and its WebSocket helpers (~120 lines):

```python
"""CDP WebSocket cookie extraction for Digikey browser sessions."""

from __future__ import annotations

import json
import logging
import socket
import struct

logger = logging.getLogger(__name__)


def cdp_get_cookies(debug_url: str) -> list[dict] | None:
    """Connect to Chrome DevTools Protocol and extract cookies.

    Args:
        debug_url: WebSocket debugger URL (ws://host:port/devtools/...)

    Returns:
        List of cookie dicts, or None on failure.
    """
    # Move logic from digikey_client._cdp_get_cookies()
    ...
```

- [ ] **Step 2: Create digikey_normalizer.py**

Extract `_normalize_result()` (~180 lines) with strategy functions:

```python
"""Normalize Digikey product data from various JSON formats."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def normalize_result(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a raw Digikey result into the standard product schema.

    Handles three formats:
    - JSON-LD Product (from structured data)
    - Next.js SSR (__NEXT_DATA__ props)
    - Unknown format (basic fallback)
    """
    if _is_jsonld_product(raw):
        return _normalize_jsonld(raw)
    if _is_nextdata(raw):
        return _normalize_nextdata(raw)
    return _normalize_fallback(raw)


def _is_jsonld_product(raw: dict) -> bool:
    return raw.get("@type") == "Product"


def _is_nextdata(raw: dict) -> bool:
    return "pageProps" in raw or "product" in raw.get("props", {})


def _normalize_jsonld(raw: dict) -> dict[str, Any] | None:
    # Move lines ~354-396 from digikey_client._normalize_result()
    ...


def _normalize_nextdata(raw: dict) -> dict[str, Any] | None:
    # Move lines ~398-496 from digikey_client._normalize_result()
    ...


def _normalize_fallback(raw: dict) -> dict[str, Any] | None:
    # Move lines ~499-513 from digikey_client._normalize_result()
    ...
```

- [ ] **Step 3: Update digikey_client.py imports**

```python
from digikey_cdp import cdp_get_cookies
from digikey_normalizer import normalize_result
```

Replace `self._cdp_get_cookies(url)` with `cdp_get_cookies(url)`.
Replace `self._normalize_result(raw)` with `normalize_result(raw)`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/python/test_clients.py::TestDigikeyClient -v`
Expected: all pass

- [ ] **Step 5: Run ruff**

Run: `ruff check digikey_cdp.py digikey_normalizer.py digikey_client.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add digikey_cdp.py digikey_normalizer.py digikey_client.py
git commit -m "refactor: extract CDP and normalizer from digikey_client.py

digikey_cdp.py: WebSocket cookie extraction (~120 lines)
digikey_normalizer.py: 3-strategy result normalization (~180 lines)
digikey_client.py: slim session + fetch API (~200 lines, down from 763)

[ci: python]"
```

---

### Task 12: Add error handling infrastructure

**Files:**
- Create: `dubis_errors.py`
- Create: `tests/python/test_dubis_errors.py`
- Modify: `digikey_client.py` (replace broad catches)

- [ ] **Step 1: Write test**

```python
"""Tests for dubIS error hierarchy."""

import pytest

from dubis_errors import (
    DubISError,
    DistributorError,
    DistributorTimeout,
    DistributorAuthError,
    CacheError,
)


def test_hierarchy():
    assert issubclass(DistributorError, DubISError)
    assert issubclass(DistributorTimeout, DistributorError)
    assert issubclass(DistributorAuthError, DistributorError)
    assert issubclass(CacheError, DubISError)


def test_distributor_error_carries_provider():
    err = DistributorError("failed", provider="digikey")
    assert err.provider == "digikey"
    assert "failed" in str(err)


def test_timeout_carries_context():
    err = DistributorTimeout("timed out", provider="mouser", part_number="ABC")
    assert err.provider == "mouser"
    assert err.part_number == "ABC"
```

- [ ] **Step 2: Implement dubis_errors.py**

```python
"""dubIS exception hierarchy."""

from __future__ import annotations


class DubISError(Exception):
    """Base exception for all dubIS errors."""


class DistributorError(DubISError):
    """Error from a distributor client."""

    def __init__(self, message: str, *, provider: str = "", **kwargs):
        super().__init__(message)
        self.provider = provider
        for k, v in kwargs.items():
            setattr(self, k, v)


class DistributorTimeout(DistributorError):
    """Distributor request timed out."""

    def __init__(self, message: str, *, provider: str = "", part_number: str = ""):
        super().__init__(message, provider=provider)
        self.part_number = part_number


class DistributorAuthError(DistributorError):
    """Distributor authentication/session error."""


class CacheError(DubISError):
    """Error in cache database operations."""
```

- [ ] **Step 3: Run test**

Run: `pytest tests/python/test_dubis_errors.py -v`
Expected: all PASS

- [ ] **Step 4: Replace broad catches in digikey_client.py**

Replace the most dangerous `except Exception` blocks with specific catches. Focus on:
- `fetch_product()`: catch `DistributorTimeout`, `DistributorError`, let unexpected errors propagate
- `_poll_loop()`: catch `DistributorAuthError` separately from `DistributorTimeout`
- Session check: raise `DistributorAuthError` instead of returning dict with error message

- [ ] **Step 5: Run tests**

Run: `pytest tests/python/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add dubis_errors.py tests/python/test_dubis_errors.py digikey_client.py
git commit -m "refactor: add error hierarchy, replace broad catches in digikey_client

DubISError → DistributorError → DistributorTimeout/AuthError
CacheError for database operations.

[ci: python]"
```

---

## Post-Completion Verification

After all tasks are done, run the full test suite:

```bash
# JavaScript
npx eslint js/
npx tsc --noEmit
npx vitest run

# Python
ruff check .
pytest tests/python/ -v
```

Verify no file exceeds:
- 350 lines (JS)
- 400 lines (Python, except digikey_client.py which should be ~200)

Create a PR with commit tag `[ci: all]` to run the full CI suite.
