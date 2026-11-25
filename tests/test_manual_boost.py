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


@pytest.mark.asyncio
async def test_worker_can_open_select_and_submit_boost():
    from contextlib import asynccontextmanager

    from app.bot.handlers.boost import open_boost, select_boost
    from app.bot.keyboards import main_menu
    from app.models import User

    worker = User(role="worker", telegram_id=42, is_active=True)
    menu = main_menu(worker).inline_keyboard
    assert any(button.callback_data == "menu:boost" for row in menu for button in row)

    @asynccontextmanager
    async def session():
        yield None, "seller"

    async def purchase(seller, selected, dry_run, actor):
        assert (seller, selected, dry_run, actor) == ("seller", [("slug", "Gift")], False, worker)
        yield "Gift — ✅"

    ctx = Mock()
    ctx.automation.manual_session = session
    ctx.automation.boost_selected = purchase
    ctx.client.active_listings = AsyncMock(return_value={"slug": "Gift"})
    ctx.settings.get = AsyncMock(return_value=SimpleNamespace(dry_run=False))
    callback = AsyncMock()
    callback.from_user.id = worker.telegram_id
    await open_boost(callback, ctx, worker)
    rows = callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
    callback.data = rows[0][0].callback_data
    await select_boost(callback, ctx, worker)
    callback.data = rows[-2][0].callback_data
    await select_boost(callback, ctx, worker)
    assert callback.message.edit_text.await_args.args == ("Gift — ✅",)


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run,external,expected,clicks", [
    (False, False, "success", 1),
    (True, False, "dry_run", 0),
    (False, True, "deferred", 0),
])
async def test_renew_existing_premium_from_balance(config, dry_run, external, expected, clicks):
    from pathlib import Path
    from playwright.async_api import async_playwright
    from app.playerok.selectors import Selectors

    html = """
    <button onclick="promo.hidden=false">Поднять в топ</button>
    <section id="promo" hidden>
      <label>Премиум<input type="radio" name="itemPriority" checked></label>
      <button onclick="payment.hidden=false">Обновить Премиум за 13 ₽ <del>37</del></button>
    </section>
    <section id="payment" role="dialog" hidden>
      <label>Оплатить с баланса 6 523,10 ₽
        <input type="checkbox" name="providerId" value="LOCAL">
      </label>
      <label><input type="radio" name="providerId" value="SBP">СБП</label>
      <div>13 ₽</div><div>🚀 Премиум</div>
      <button onclick="if(document.querySelector('[value=LOCAL]').checked){window.paid++;payment.hidden=true;receipt.hidden=false}">Перейти к оплате</button>
    </section>
    <div hidden id="receipt">Успешно оплачено</div><script>window.paid=0</script>
    """
    client = PlayerokClient(config, Selectors(Path(__file__).resolve().parents[1] / "selectors.yaml"))
    client._before_action = AsyncMock()
    client._dismiss_cookie_banner = AsyncMock()
    client.fetch_balance = AsyncMock(return_value=Decimal("6523.10"))
    async with async_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Install Playwright Chromium for the checkout test")
        browser = await playwright.chromium.launch(executable_path=playwright.chromium.executable_path)
        page = await browser.new_page()
        async def open_listing(page, url):
            assert url.endswith("/products/test-gift")
            await page.set_content(html)
            if external:
                await page.locator('[value="SBP"]').evaluate("(radio) => radio.checked = true")
        client._goto = open_listing
        item = SimpleNamespace(slug="test-gift", status="APPROVED", priority="PREMIUM")
        result = await client.manual_premium(page, item, Decimal("13"), dry_run)
        assert (result.status, result.amount) == (expected, Decimal("13"))
        assert await page.evaluate("window.paid") == clicks
        await browser.close()
