"""Default purchase-quantity computation for cart items.

Reconstructs the price-break ladder from events/price_observations.csv and
applies the cost-stepping rule (see docs/superpowers/specs/2026-07-24-cart-feature-design.md).
"""
from __future__ import annotations

import csv
import math
import os


def _round_up_10(n: int) -> int:
    return int(math.ceil(n / 10.0) * 10)


def tier_ladder(events_dir: str, part_id: str, distributor: str) -> list[tuple[int, float]]:
    path = os.path.join(events_dir, "price_observations.csv")
    if not os.path.exists(path):
        return []
    latest: dict[int, tuple[str, float]] = {}  # moq -> (timestamp, unit_price)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("part_id") != part_id or row.get("distributor") != distributor:
                continue
            moq_raw = (row.get("moq") or "").strip()
            price_raw = (row.get("unit_price") or "").strip()
            if not moq_raw or not price_raw:
                continue
            try:
                moq = int(float(moq_raw))
                price = float(price_raw)
            except ValueError:
                continue
            ts = row.get("timestamp", "")
            if moq not in latest or ts >= latest[moq][0]:
                latest[moq] = (ts, price)
    return sorted((q, p) for q, (_ts, p) in latest.items())


def default_qty(shortfall: int | None, ladder: list[tuple[int, float]]) -> int:
    has_shortfall = isinstance(shortfall, int) and shortfall > 0
    base = shortfall if has_shortfall else 1
    if not ladder:
        return base
    breaks = [q for q, _ in ladder]
    if has_shortfall:
        candidates = [q for q in breaks if q >= base]
        if not candidates:
            return max(breaks)
        step = min(candidates)
        return step if step <= 2 * base else _round_up_10(base)
    # no shortfall: lowest break, unless its extended price > $30 -> 5
    low_q, low_price = min(ladder)
    return 5 if low_q * low_price > 30 else low_q
