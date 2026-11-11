from __future__ import annotations

from decimal import Decimal

from app.playerok.client import PlayerokClient


def test_parse_premium_price_from_promo_button():
    assert PlayerokClient._parse_premium_price(
        "Выставить Премиум за 25 ₽"
    ) == Decimal("25")
    assert PlayerokClient._parse_premium_price(
        "Повысить до Премиум за 99 ₽ 149"
    ) == Decimal("99")
    assert PlayerokClient._parse_premium_price("99 ₽") == Decimal("99")
    assert PlayerokClient._parse_premium_price("без цены") is None


def test_parse_premium_price_uses_non_breaking_space():
    assert PlayerokClient._parse_premium_price(
        "Выставить Премиум за 25\u00a0₽"
    ) == Decimal("25")
