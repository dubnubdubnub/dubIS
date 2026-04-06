"""Price and quantity parsing utilities."""

from __future__ import annotations

import json
from typing import Any


def parse_qty(value: Any, default: int = 0) -> int:
    """Parse a quantity string to int, tolerating commas and floats."""
    try:
        return int(float(str(value).replace(",", "")))
    except (ValueError, TypeError):
        return default


def parse_price(value: Any, default: float = 0.0) -> float:
    """Parse a price string to float, tolerating commas and dollar signs."""
    try:
        return float(str(value).replace(",", "").replace("$", "") or "0")
    except (ValueError, TypeError):
        return default


def ensure_parsed(value: str | Any) -> Any:
    """Parse JSON string if needed, otherwise return as-is."""
    return json.loads(value) if isinstance(value, str) else value


def derive_missing_price(
    unit_price: float | None,
    ext_price: float | None,
    qty: int,
) -> tuple[float | None, float | None]:
    """Fill in whichever of unit/ext is missing given the other + qty.

    Returns (unit_price, ext_price) with the missing value derived,
    or unchanged if both are provided, both are None, or qty is 0.
    """
    if unit_price is not None and unit_price != 0 and ext_price is None and qty > 0:
        ext_price = unit_price * qty
    elif ext_price is not None and ext_price != 0 and unit_price is None and qty > 0:
        unit_price = ext_price / qty
    return unit_price, ext_price
