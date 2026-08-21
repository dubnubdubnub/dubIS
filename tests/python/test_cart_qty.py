import csv
import os

import cart_qty


L = [(1, 9.2), (20, 7.23), (40, 6.83)]  # (break_qty, unit_price)


def _write_events(tmp_path, rows):
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    csv_path = events_dir / "price_observations.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["timestamp", "part_id", "distributor", "unit_price", "currency", "source", "moq", "note"]
        )
        for row in rows:
            writer.writerow(row)
    return str(events_dir)


def test_shortfall_rounds_up_to_nearest_break():
    # need 15 -> nearest break >=15 is 20, and 20 <= 2*15 -> 20
    assert cart_qty.default_qty(15, L) == 20


def test_shortfall_break_more_than_double_rounds_to_ten():
    # need 3 -> nearest break >=3 is 20, but 20 > 2*3 -> round 3 up to nearest 10 => 10
    assert cart_qty.default_qty(3, L) == 10


def test_shortfall_above_all_breaks_uses_largest_break():
    assert cart_qty.default_qty(100, L) == 40


def test_no_shortfall_uses_lowest_break_when_cheap():
    cheap = [(10, 0.05), (100, 0.02)]  # 10*0.05 = 0.5 <= 30
    assert cart_qty.default_qty(None, cheap) == 10


def test_no_shortfall_expensive_lowest_break_defaults_to_five():
    pricey = [(1, 40.0), (5, 35.0)]  # 1*40 = 40 > 30
    assert cart_qty.default_qty(None, pricey) == 5


def test_no_ladder_returns_shortfall_or_one():
    assert cart_qty.default_qty(7, []) == 7
    assert cart_qty.default_qty(None, []) == 1


def test_no_shortfall_unsorted_ladder_uses_smallest_break():
    # ladder deliberately out of order -> must still pick smallest break_qty (10), not ladder[0] (100)
    assert cart_qty.default_qty(None, [(100, 0.02), (10, 0.05)]) == 10


def test_tier_ladder_latest_per_moq_wins(tmp_path):
    rows = [
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "5.00", "USD", "manual", "10", ""),
        ("2026-06-01T00:00:00Z", "PN1", "LCSC", "4.50", "USD", "manual", "10", ""),  # later, wins
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "3.00", "USD", "manual", "50", ""),
        ("2026-01-01T00:00:00Z", "PN1", "MOUSER", "9.99", "USD", "manual", "10", ""),
        ("2026-01-01T00:00:00Z", "OTHER_PN", "LCSC", "1.00", "USD", "manual", "10", ""),
    ]
    events_dir = _write_events(tmp_path, rows)
    ladder = cart_qty.tier_ladder(events_dir, "PN1", "LCSC")
    assert ladder == [(10, 4.5), (50, 3.0)]
    # ascending by qty
    assert [q for q, _ in ladder] == sorted(q for q, _ in ladder)


def test_tier_ladder_missing_file_returns_empty(tmp_path):
    empty_dir = tmp_path / "events_missing"
    empty_dir.mkdir()
    assert cart_qty.tier_ladder(str(empty_dir), "PN1", "LCSC") == []


def test_tier_ladder_skips_malformed_rows(tmp_path):
    rows = [
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "5.00", "USD", "manual", "", ""),  # blank moq
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "bad", "USD", "manual", "10", ""),  # non-numeric price
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "6.00", "USD", "manual", "abc", ""),  # non-numeric moq
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "7.00", "USD", "manual", "25", ""),  # valid
    ]
    events_dir = _write_events(tmp_path, rows)
    ladder = cart_qty.tier_ladder(events_dir, "PN1", "LCSC")
    assert ladder == [(25, 7.0)]


# ── default_qty regression pin ────────────────────────────────────────────
#
# The cost-stepping rule (docs/superpowers/specs/2026-07-24-cart-feature-design.md)
# is deliberate, and `default_qty` takes an already-built ladder — so making
# `tier_ladder` packaging-aware must not move a single one of these outputs.
# This table was captured from the pre-packaging implementation and is asserted
# verbatim: it passes identically before and after the packaging change. Do not
# "fix" an entry here to match new behaviour — a diff in this table IS the bug.

DEFAULT_QTY_PINS = [
    # (shortfall, ladder, expected)
    # ── no ladder: shortfall passes through, else 1 ──
    (None, [], 1),
    (0, [], 1),
    (-5, [], 1),
    (1, [], 1),
    (7, [], 7),
    (999, [], 999),
    # ── shortfall + ladder: next break if <= 2x shortfall, else round up to 10 ──
    (1, L, 1),        # break 1 is exactly the shortfall
    (2, L, 10),       # next break 20 > 2*2 -> round 2 up to 10
    (3, L, 10),       # next break 20 > 2*3 -> round 3 up to 10
    (10, L, 20),      # next break 20 == 2*10 -> take the break (<=, not <)
    (11, L, 20),      # next break 20 <= 22
    (15, L, 20),
    (20, L, 20),      # shortfall lands exactly on a break
    (21, L, 40),      # next break 40 <= 42
    (25, L, 40),
    (30, L, 40),      # 40 == 2*20? no: 40 <= 60 -> take it
    (41, L, 40),      # above every break -> largest break
    (100, L, 40),
    (39, L, 40),
    (26, L, 40),      # 40 <= 52
    # a ladder whose only break is huge: 2x rule pushes to round-up-10
    (7, [(3000, 0.01)], 10),
    (1600, [(3000, 0.01)], 3000),   # 3000 <= 2*1600
    (1400, [(3000, 0.01)], 1400),   # 3000 > 2*1400 -> round 1400 up to 10
    (1495, [(3000, 0.01)], 1500),   # round-up-to-10 actually rounds
    # ── no shortfall: lowest break, or 5 when its extended price > $30 ──
    (None, [(10, 0.05), (100, 0.02)], 10),
    (None, [(1, 9.2), (20, 7.23)], 1),
    (None, [(1, 40.0), (5, 35.0)], 5),        # 1*40 = 40 > 30 -> 5
    (None, [(1, 30.0)], 1),                   # 1*30 == 30, not > 30 -> 1
    (None, [(1, 30.01)], 5),
    (None, [(100, 0.02), (10, 0.05)], 10),    # unsorted -> smallest break
    (None, [(3000, 0.011)], 5),               # 3000*0.011 = 33 > 30 -> 5
    (None, [(3000, 0.009)], 3000),            # 27 <= 30 -> the break itself
    (0, L, 1),                                # falsy shortfall == no shortfall
]


def test_default_qty_pinned_outputs():
    """Every (shortfall, ladder) -> qty pair below is frozen behaviour."""
    actual = [(s, tuple(lad), cart_qty.default_qty(s, lad))
              for s, lad, _ in DEFAULT_QTY_PINS]
    expected = [(s, tuple(lad), exp) for s, lad, exp in DEFAULT_QTY_PINS]
    assert actual == expected
