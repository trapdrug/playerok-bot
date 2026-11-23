from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from app.config import AppConfig
from app.playerok.client import ItemInfo, PlayerokClient
from app.playerok.exceptions import LayoutChangedError, PlayerokError
from app.playerok.selectors import Selectors

SELECTORS_FILE = Path(__file__).resolve().parents[1] / "selectors.yaml"


def _client(config: AppConfig) -> PlayerokClient:
    return PlayerokClient(config, Selectors(SELECTORS_FILE))


@pytest.mark.asyncio
@pytest.mark.parametrize("urls", [[], ["https://i.playerok.com/photo.jpg"]])
async def test_missing_picture_fails_only_the_sale(config, monkeypatch, urls):
    client = _client(config)
    item = ItemInfo(
        id="1", slug="sold", name="Example", price=Decimal("100"),
        description="", attributes={}, game_category_id="category",
        obtaining_type_id="type", data_fields=[], attachment_urls=urls,
    )
    page = Mock()
    page.context.request.get = AsyncMock(return_value=Mock(ok=False, status=503))
    create = AsyncMock()
    monkeypatch.setattr(client, "_before_action", AsyncMock())
    monkeypatch.setattr(client, "_create_item_mutation", create)
    with pytest.raises(PlayerokError) as error:
        await client.create_listing(page, item, dry_run=False)
    assert not isinstance(error.value, LayoutChangedError)
    assert "sold" in str(error.value)
    create.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_transient_api_error_does_not_look_like_layout_change(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A temporary 502 plus a broken DOM fallback must not stop the bot."""
    client = _client(config)

    async def graphql(page, username, user_id):  # noqa: ANN001
        raise PlayerokError("GraphQL: некорректный ответ HTTP 502")

    async def dom(page, username):  # noqa: ANN001
        raise LayoutChangedError("Список продаж не найден")

    monkeypatch.setattr(client, "_fetch_sales_graphql", graphql)
    monkeypatch.setattr(client, "_fetch_sales_dom", dom)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    with pytest.raises(PlayerokError) as excinfo:
        await client.fetch_sales(object(), "seller", "user-id")
    assert not isinstance(excinfo.value, LayoutChangedError)


@pytest.mark.asyncio
async def test_real_layout_change_is_still_reported(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(config)

    async def graphql(page, username, user_id):  # noqa: ANN001
        raise LayoutChangedError("Ответ GraphQL не содержит deals")

    async def dom(page, username):  # noqa: ANN001
        raise LayoutChangedError("Список продаж не найден")

    monkeypatch.setattr(client, "_fetch_sales_graphql", graphql)
    monkeypatch.setattr(client, "_fetch_sales_dom", dom)

    with pytest.raises(LayoutChangedError):
        await client.fetch_sales(object(), "seller", "user-id")


@pytest.mark.asyncio
async def test_sales_are_retried_after_a_transient_error(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(config)
    attempts = {"count": 0}

    async def graphql(page, username, user_id):  # noqa: ANN001
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PlayerokError("GraphQL: Operation timeout")
        return ["sale"]

    async def dom(page, username):  # noqa: ANN001
        raise AssertionError("the DOM fallback must not be used")

    monkeypatch.setattr(client, "_fetch_sales_graphql", graphql)
    monkeypatch.setattr(client, "_fetch_sales_dom", dom)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    assert await client.fetch_sales(object(), "seller", "user-id") == ["sale"]
    assert attempts["count"] == 2


async def _no_sleep(*_args, **_kwargs) -> None:
    return None
