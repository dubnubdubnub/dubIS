"""Tests for price_ops module: quantity/price parsing and JSON ensure_parsed."""

import json

import pytest

from price_ops import derive_missing_price, ensure_parsed, parse_price, parse_qty


class TestParseQty:
    """Tests for parse_qty()."""

    def test_integer_string(self):
        assert parse_qty("10") == 10

    def test_float_string_truncates(self):
        assert parse_qty("10.7") == 10

    def test_with_commas(self):
        assert parse_qty("1,000") == 1000

    def test_large_number_with_commas(self):
        assert parse_qty("1,234,567") == 1234567

    def test_empty_string_returns_default(self):
        assert parse_qty("") == 0

    def test_none_returns_default(self):
        assert parse_qty(None) == 0

    def test_custom_default(self):
        assert parse_qty("", default=-1) == -1

    def test_malformed_string_returns_default(self):
        assert parse_qty("abc") == 0

    def test_negative_value(self):
        assert parse_qty("-5") == -5

    def test_zero(self):
        assert parse_qty("0") == 0

    def test_whitespace_around_number(self):
        # str() + float() handles whitespace
        assert parse_qty(" 42 ") == 42

    def test_integer_input(self):
        assert parse_qty(100) == 100

    def test_float_input(self):
        assert parse_qty(3.9) == 3

    def test_negative_float(self):
        assert parse_qty("-2.8") == -2


class TestParsePrice:
    """Tests for parse_price()."""

    def test_plain_number(self):
        assert parse_price("1.25") == 1.25

    def test_dollar_sign(self):
        assert parse_price("$5.99") == 5.99

    def test_with_commas(self):
        assert parse_price("1,234.56") == 1234.56

    def test_dollar_and_commas(self):
        assert parse_price("$10,000.00") == 10000.00

    def test_empty_string_returns_zero(self):
        assert parse_price("") == 0.0

    def test_none_returns_default(self):
        assert parse_price(None) == 0.0

    def test_custom_default(self):
        assert parse_price("bad", default=-1.0) == -1.0

    def test_malformed_returns_default(self):
        assert parse_price("not-a-price") == 0.0

    def test_zero(self):
        assert parse_price("0") == 0.0

    def test_negative_price(self):
        assert parse_price("-3.50") == -3.50

    def test_integer_input(self):
        assert parse_price(5) == 5.0

    def test_float_input(self):
        assert parse_price(2.5) == 2.5

    def test_just_dollar_sign(self):
        """A lone '$' should parse to 0."""
        assert parse_price("$") == 0.0

    def test_whitespace(self):
        assert parse_price(" 4.20 ") == 4.20

    def test_large_price(self):
        assert parse_price("$99,999.99") == 99999.99


class TestEnsureParsed:
    """Tests for ensure_parsed()."""

    def test_parses_json_string(self):
        result = ensure_parsed('{"a": 1}')
        assert result == {"a": 1}

    def test_parses_json_array_string(self):
        result = ensure_parsed('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_returns_dict_as_is(self):
        d = {"key": "value"}
        assert ensure_parsed(d) is d

    def test_returns_list_as_is(self):
        lst = [1, 2, 3]
        assert ensure_parsed(lst) is lst

    def test_returns_int_as_is(self):
        assert ensure_parsed(42) == 42

    def test_returns_none_as_is(self):
        assert ensure_parsed(None) is None

    def test_raises_on_invalid_json_string(self):
        with pytest.raises(json.JSONDecodeError):
            ensure_parsed("{bad json")

    def test_parses_nested_json(self):
        nested = '{"items": [{"name": "R1", "qty": 10}]}'
        result = ensure_parsed(nested)
        assert result["items"][0]["name"] == "R1"

    def test_parses_json_null_string(self):
        assert ensure_parsed("null") is None

    def test_parses_json_boolean_string(self):
        assert ensure_parsed("true") is True
        assert ensure_parsed("false") is False


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
