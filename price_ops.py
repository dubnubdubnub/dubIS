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
