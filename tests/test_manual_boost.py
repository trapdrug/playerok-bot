from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.bot.handlers.boost import Selection
from app.playerok.client import PlayerokClient
from app.playerok.exceptions import PaymentUncertainError


def test_selection_keeps_checks_across_pages():
    selection = Selection("seller", [(str(i), f"Gift {i}") for i in range(10)], False)
    selection.selected.update({0, 9})
    selection.page = 1
    rows = selection.keyboard().inline_keyboard
    assert rows[0][0].text == "❌ Gift 8"
    assert rows[1][0].text == "✅ Gift 9"
    assert rows[-2][0].text.endswith("(2)")


@pytest.mark.asyncio
async def test_active_listings_follow_cursor_and_filter_seller():
    client = Mock()
    client._items_page = AsyncMock(side_effect=[
        {"edges": [{"node": {"slug": "a", "name": "First"}}],
         "pageInfo": {"hasNextPage": True, "endCursor": "next"}},
        {"edges": [{"node": {"slug": "b", "name": "Second"}}],
         "pageInfo": {"hasNextPage": False}},
    ])
    assert await PlayerokClient.active_listings(client, None, "seller") == {
        "a": "First", "b": "Second"
    }
    assert client._items_page.await_args.args == (None, "seller", ["APPROVED"], "next")


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,preview,priority,expected", [
    (9, False, "DEFAULT", "skipped"),
    (10, True, "DEFAULT", "preview"),
    (10, False, "PREMIUM", "skipped"),
    (10, False, "DEFAULT", "success"),
])
async def test_manual_premium(budget, preview, priority, expected):
    item = SimpleNamespace(id="item", status="APPROVED", priority=priority)
    client = Mock()
    client._priority_options = AsyncMock(return_value=[{"type": "PREMIUM", "id": "opt", "price": 10}])
    client.fetch_balance = AsyncMock(return_value=Decimal("100"))
    client._before_action = AsyncMock()
    client.increase_priority = AsyncMock(return_value=SimpleNamespace(status="success"))
    result = await PlayerokClient.manual_premium(client, None, item, Decimal(budget), preview)
    assert result.status == expected
    assert client.increase_priority.await_count == int(expected == "success")


@pytest.mark.asyncio
async def test_manual_purchase_does_not_resubmit_on_lost_response():
    from app.playerok.exceptions import PlayerokError

    client = Mock()
    client._graphql = AsyncMock(side_effect=PlayerokError("network"))
    with pytest.raises(PaymentUncertainError):
        await PlayerokClient.increase_priority(client, None, "item", "premium", manual_price=Decimal("10"))
    client._graphql.assert_awaited_once()
    assert client._graphql.await_args.kwargs["retry"] is False
