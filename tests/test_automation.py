from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Sale
from app.playerok.client import (
    BoostOutcome,
    ChatInfo,
    ItemInfo,
    ListingResult,
    SaleInfo,
    SessionInfo,
)
from app.services.audit import AuditService
from app.services.automation import AutomationService
from app.services.cookies import CookieStatus
from app.services.settings import SettingsService


class FakePageSession:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return False


class FakeClient:
    def __init__(self, sales: list[SaleInfo]) -> None:
        self.sales = sales
        self.chats: list[ChatInfo] = []
        self.listing_calls: list[str] = []
        self.publish_calls: list[str] = []
        self.publish_options: list[tuple] = []
        self.item_title: str | None = None
        self.fetched_items: list[str] = []
        self.boost_calls: list[tuple[str, bool, Decimal]] = []
        self.boost_limits: list[tuple[Decimal | None, Decimal | None]] = []
        self.stop_check = lambda: False

    def set_stop_check(self, callback):
        self.stop_check = callback

    def page_session(self, cookies):
        return FakePageSession()

    async def check_session(self, page):
        return SessionInfo(username="seller", user_id="seller-id")

    async def fetch_sales(self, page, username, user_id=None):
        return list(self.sales)

    async def fetch_chats(self, page, user_id):
        return list(self.chats)

    async def fetch_items_count(self, page, user_id):
        return None

    async def fetch_item(self, page, slug):
        self.fetched_items.append(slug)
        return ItemInfo(
            id="item-1",
            slug=slug,
            name=self.item_title or f"Item {slug}",
            price=Decimal("100.00"),
            description="Описание",
            attributes={"type": "test"},
            game_category_id="cat-1",
            obtaining_type_id="type-1",
            data_fields=[{"fieldId": "f1", "value": "значение"}],
            attachment_urls=[],
        )

    def skip_boost_reason(self, title):
        lowered = (title or "").casefold()
        for phrase in ("доплата подарка", "доплата подарок"):
            if phrase in lowered:
                return phrase
        return None

    async def create_listing(self, page, item, *, dry_run):
        self.listing_calls.append(item.slug)
        if dry_run:
            return ListingResult(
                slug=None,
                lot_url=None,
                item_id=None,
                price=item.price,
                dry_run=True,
            )
        return ListingResult(
            slug=f"copy-{item.slug}",
            lot_url=f"https://playerok.com/products/copy-{item.slug}",
            item_id="new-item",
            price=item.price,
            dry_run=False,
        )

    async def publish_listing(
        self,
        page,
        lot_url,
        *,
        premium=False,
        max_amount=None,
        item_full_price=None,
    ):
        self.publish_calls.append(lot_url)
        self.publish_options.append((premium, max_amount, item_full_price))
        if premium:
            return BoostOutcome(
                status="success",
                amount=Decimal("10.00"),
                source="main",
            )
        return BoostOutcome(status="success", amount=None, source="main")

    async def restart_browser(self):
        return None

    async def fetch_balance(self, page):
        return Decimal("100.00")

    async def apply_boost(
        self,
        page,
        lot_url,
        *,
        dry_run,
        remaining_budget,
        max_amount=None,
        item_full_price=None,
    ):
        self.boost_calls.append((lot_url, dry_run, remaining_budget))
        self.boost_limits.append((max_amount, item_full_price))
        return BoostOutcome(
            status="dry_run",
            amount=Decimal("10.00"),
            source="main",
        )


class FakeCookies:
    def __init__(self) -> None:
        self.alive = None

    async def get_cookies(self):
        return [
            {
                "name": "session",
                "value": "x",
                "domain": ".playerok.com",
                "path": "/",
            }
        ]

    async def status(self):
        return CookieStatus(exists=True, is_alive=self.alive)

    async def mark_checked(self, alive, error=None):
        self.alive = alive


class FakeNotifications:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, int, datetime | None]] = []

    async def chat_message(self, sender, unread, created_at=None):
        self.chat_calls.append((sender, unread, created_at))
        return 1

    def __getattr__(self, name):
        async def noop(*args, **kwargs):
            return None

        return noop


def make_sale(external_id: str) -> SaleInfo:
    return SaleInfo(
        external_id=external_id,
        title=f"Item {external_id}",
        price=Decimal("100.00"),
        deal_url=f"https://playerok.com/deal/{external_id}",
        lot_url=f"https://playerok.com/products/{external_id}",
        item_slug=external_id,
        status="CONFIRMED",
        created_at=datetime(2026, 1, 1),
    )


def build_automation(db, config, client, cookies, notifications):
    audit = AuditService(db)
    settings = SettingsService(db, config, audit)
    automation = AutomationService(
        config, db, settings, cookies, notifications, audit, client
    )
    return audit, settings, automation


@pytest.mark.asyncio
async def test_baseline_then_dedup(db, config):
    client = FakeClient([make_sale("1")])
    cookies = FakeCookies()
    notifications = FakeNotifications()
    _, settings, automation = build_automation(
        db, config, client, cookies, notifications
    )

    await settings.set_feature("auto_relist_enabled", True)
    await automation.initialize()

    # First cycle stores a baseline and performs no actions.
    await automation.run_cycle()
    assert client.listing_calls == []
    assert (await settings.get()).sales_initialized is True

    async with db.session() as session:
        rows = list(await session.scalars(select(Sale)))
    assert len(rows) == 1
    assert rows[0].status == "baseline"

    # A new sale is relisted exactly once.
    client.sales = [make_sale("1"), make_sale("2")]
    await automation.run_cycle()
    assert client.listing_calls == ["2"]

    # Repeated polling does not relist the same sale again.
    await automation.run_cycle()
    assert client.listing_calls == ["2"]

    async with db.session() as session:
        rows = list(
            await session.scalars(select(Sale).order_by(Sale.external_id))
        )
    assert [row.status for row in rows] == ["baseline", "relisted"]


@pytest.mark.asyncio
async def test_auto_boost_alone_relist_and_dry_run_boost(db, config):
    client = FakeClient([make_sale("1")])
    cookies = FakeCookies()
    notifications = FakeNotifications()
    _, settings, automation = build_automation(
        db, config, client, cookies, notifications
    )

    await settings.set_feature("auto_boost_enabled", True)
    await settings.set_daily_limit(Decimal("500"))
    await automation.initialize()

    # Baseline cycle.
    await automation.run_cycle()
    assert client.listing_calls == []

    # Auto-boost alone must create the new listing first.
    client.sales = [make_sale("1"), make_sale("2")]
    await automation.run_cycle()

    assert client.listing_calls == ["2"]
    # The sold lot data is read before the copy is published.
    assert client.fetched_items == ["2"]
    # In dry-run nothing is published, so the boost has no new listing to pay for.
    assert client.boost_calls == []

    async with db.session() as session:
        row = await session.scalar(
            select(Sale).where(Sale.external_id == "2")
        )
    assert row is not None
    assert row.status == "relisted"


@pytest.mark.asyncio
async def test_relist_without_boost_publishes_listing(db, config):
    client = FakeClient([make_sale("1")])
    cookies = FakeCookies()
    notifications = FakeNotifications()
    _, settings, automation = build_automation(
        db, config, client, cookies, notifications
    )

    await settings.set_feature("auto_relist_enabled", True)
    await settings.set_feature("dry_run", False)
    await automation.initialize()

    await automation.run_cycle()
    client.sales = [make_sale("1"), make_sale("2")]
    await automation.run_cycle()

    assert client.listing_calls == ["2"]
    # Auto-boost is off: the new listing is still published, without premium.
    assert client.boost_calls == []
    assert client.publish_calls == [
        "https://playerok.com/products/copy-2"
    ]

    async with db.session() as session:
        row = await session.scalar(
            select(Sale).where(Sale.external_id == "2")
        )
    assert row is not None
    assert row.status == "relisted"
    assert row.boost_state == "without_premium"


@pytest.mark.asyncio
async def test_skip_trigger_publishes_listing_without_boost(db, config):
    client = FakeClient([make_sale("1")])
    client.item_title = "ДОПЛАТА ПОДАРКА"
    cookies = FakeCookies()
    notifications = FakeNotifications()
    _, settings, automation = build_automation(
        db, config, client, cookies, notifications
    )

    await settings.set_feature("auto_relist_enabled", True)
    await settings.set_feature("auto_boost_enabled", True)
    await settings.set_feature("dry_run", False)
    await automation.initialize()

    await automation.run_cycle()
    client.sales = [make_sale("1"), make_sale("2")]
    await automation.run_cycle()

    assert client.listing_calls == ["2"]
    # The trigger forbids buying premium for this title.
    assert client.boost_calls == []
    # But the listing is still published, without premium.
    assert client.publish_calls == ["https://playerok.com/products/copy-2"]


@pytest.mark.asyncio
async def test_live_mode_creates_copy_and_boosts_it(db, config):
    client = FakeClient([make_sale("1")])
    cookies = FakeCookies()
    notifications = FakeNotifications()
    _, settings, automation = build_automation(
        db, config, client, cookies, notifications
    )

    await settings.set_feature("auto_boost_enabled", True)
    await settings.set_feature("dry_run", False)
    await settings.set_daily_limit(Decimal("500"))
    await automation.initialize()

    await automation.run_cycle()
    client.sales = [make_sale("1"), make_sale("2")]
    await automation.run_cycle()

    assert client.listing_calls == ["2"]
    # The premium is bought while the new listing is published.
    assert client.boost_calls == []
    assert client.publish_calls == ["https://playerok.com/products/copy-2"]
    premium, limit, item_price = client.publish_options[0]
    assert premium is True
    # The single-boost limit and the item price without discount are passed on.
    assert limit == Decimal("925")
    assert item_price == Decimal("100.00")


@pytest.mark.asyncio
async def test_chat_notification_once_per_message(db, config):
    client = FakeClient([])
    client.chats = [
        ChatInfo(
            chat_id="chat-1",
            chat_type="PM",
            unread_count=2,
            last_message_id="msg-1",
            last_message_at=datetime(2026, 1, 1, 12, 0),
            sender_username="buyer",
            sender_id="user-1",
            is_read=False,
        )
    ]
    cookies = FakeCookies()
    notifications = FakeNotifications()
    _, settings, automation = build_automation(
        db, config, client, cookies, notifications
    )
    await automation.initialize()

    await automation.run_cycle()
    assert notifications.chat_calls == [
        ("buyer", 2, datetime(2026, 1, 1, 12, 0))
    ]

    # The same last message must not be notified twice.
    await automation.run_cycle()
    assert len(notifications.chat_calls) == 1
