import cart_qty


L = [(1, 9.2), (20, 7.23), (40, 6.83)]  # (break_qty, unit_price)


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
