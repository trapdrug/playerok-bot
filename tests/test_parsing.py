from __future__ import annotations

from decimal import Decimal

import pytest

from app.utils.parsing import money, parse_money, truncate, validate_username


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 234,50 ₽", Decimal("1234.50")),
        ("1\u00a0234,50 ₽", Decimal("1234.50")),
        ("12.5", Decimal("12.50")),
        (100, Decimal("100.00")),
        (Decimal("99.999"), Decimal("100.00")),
        (None, None),
        ("нет цены", None),
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


def test_money_format():
    assert money(Decimal("1234.00")) == "1 234 ₽"
    assert money(Decimal("1234.50")) == "1 234.50 ₽"
    assert money(None) == "—"


def test_validate_username():
    assert validate_username(" Seller_1 ") == "seller_1"
    with pytest.raises(ValueError):
        validate_username("ab")
    with pytest.raises(ValueError):
        validate_username("плохой")


def test_truncate():
    assert truncate("abc", 10) == "abc"
    assert truncate("abcdef", 4) == "abc…"
