from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable

from playwright.async_api import Page
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import AppConfig
from app.db import Database
from app.models import ChatState, PendingBoost, Sale, User
from app.playerok.client import (
    BoostOutcome,
    PlayerokClient,
    SaleInfo,
    SessionInfo,
)
from app.playerok.exceptions import (
    CaptchaError,
    LayoutChangedError,
    PaymentUncertainError,
    PlayerokError,
    RateLimitError,
    SessionExpiredError,
    StopRequestedError,
)
from app.services.audit import AuditService
from app.services.cookies import CookieService
from app.services.notifications import NotificationService
from app.services.settings import SettingsService
from app.utils.parsing import truncate, utcnow


logger = logging.getLogger(__name__)

# Errors that must stop every task and never be retried blindly.
CRITICAL_ERRORS = (
    SessionExpiredError,
    CaptchaError,
    RateLimitError,
    LayoutChangedError,
    PaymentUncertainError,
    StopRequestedError,
)

PENDING_BOOST_MAX_ATTEMPTS = 3
PENDING_BOOST_RETRY_SECONDS = 600
RELIST_MAX_ATTEMPTS = 3
UNFINISHED_SALE_MAX_AGE_HOURS = 24


class AutomationService:
    """Single-cycle automation engine.

    A cycle is deliberately linear and guarded by a lock: check the Playerok
    session, fetch sales, relist/boost new sales, then retry deferred boosts.
    Any critical condition stops every task and notifies the admins.
    """

    def __init__(
        self,
        config: AppConfig,
        db: Database,
        settings: SettingsService,
        cookies: CookieService,
        notifications: NotificationService,
        audit: AuditService,
        client: PlayerokClient,
    ) -> None:
        self.config = config
        self.db = db
        self.settings = settings
        self.cookies = cookies
        self.notifications = notifications
        self.audit = audit
        self.client = client

        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._no_cookies_notified = False
        self._chat_error_notified = False
        self._cycles_since_restart = 0

        # Let the Playwright client abort long actions when /stop is pressed.
        self.client.set_stop_check(self._stop_requested)

    @property
    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    def _stop_requested(self) -> bool:
        return self._stop_event.is_set()

    async def initialize(self) -> None:
        settings = await self.settings.get()
        if settings.stopped:
            self._stop_event.set()
        else:
            self._stop_event.clear()
        await self._warn_about_uncertain_operations()

    async def run_cycle(self) -> None:
        if self._lock.locked():
            logger.debug("Automation cycle is already running; skipping")
            return

        async with self._lock:
            task = asyncio.ensure_future(self._run_cycle_locked())
            try:
                # The cycle is shielded: on timeout it is abandoned instead of
                # blocking the scheduler forever.
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self.config.cycle_timeout_seconds,
                )
            except asyncio.TimeoutError:
                task.cancel()
                logger.warning(
                    "Cycle exceeded %ss and was aborted",
                    self.config.cycle_timeout_seconds,
                )
            except StopRequestedError:
                logger.info("Automation cycle stopped by request")
            except SessionExpiredError as exc:
                await self._handle_session_expired(exc)
            except CaptchaError:
                await self._stop_for("Обнаружена капча", self.notifications.captcha)
            except RateLimitError:
                await self._stop_for(
                    "Playerok вернул 429 / слишком много запросов",
                    self.notifications.rate_limited,
                )
            except LayoutChangedError as exc:
                await self._stop_for(
                    "Похоже, изменилась вёрстка Playerok",
                    lambda: self.notifications.layout_changed(str(exc)),
                )
            except PaymentUncertainError as exc:
                await self._stop_for(
                    "Результат оплаты буста не подтверждён",
                    lambda: self.notifications.notify(
                        "🛑 <b>Оплата буста не подтверждена</b>\n"
                        "Автобуст остановлен. Проверьте аккаунт вручную: бот "
                        "не будет повторять платёж вслепую.\n"
                        f"<code>{html.escape(truncate(str(exc), 500))}</code>",
                        critical=True,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - keep the scheduler alive
                if "Execution context was destroyed" in str(exc):
                    # The page navigated while the bot was reading it. This is
                    # a transient browser hiccup, not a failure of the task.
                    logger.warning("Cycle interrupted by a page navigation: %s", exc)
                    return
                logger.exception("Automation cycle failed")
                await self.notifications.generic_error(str(exc))

    # ------------------------------------------------------------------ cycle

    async def _run_cycle_locked(self) -> None:
        settings = await self.settings.get()
        if settings.stopped:
            self._stop_event.set()
            return

        cookies = await self.cookies.get_cookies()
        if not cookies:
            if not self._no_cookies_notified:
                self._no_cookies_notified = True
                await self.notifications.no_cookies()
            return

        previous_status = await self.cookies.status()
        was_alive = previous_status.is_alive

        try:
            async with self.client.page_session(cookies) as page:
                logger.info("Cycle: checking the Playerok session")
                session = await self.client.check_session(page)
                await self.cookies.mark_checked(True)
                self._no_cookies_notified = False
                if was_alive is False:
                    await self.audit.log(
                        "session_restored", "Сессия Playerok снова активна"
                    )

                logger.info("Cycle: polling chats")
                await self._poll_chats(page, session)

                if session.user_id:
                    try:
                        items_count = await self.client.fetch_items_count(
                            page, session.user_id
                        )
                    except (SessionExpiredError, RateLimitError, CaptchaError):
                        raise
                    except PlayerokError as exc:
                        logger.warning(
                            "Cannot read the number of items: %s", exc
                        )
                    else:
                        if items_count is not None:
                            await self.settings.set_items_in_sale(items_count)

                logger.info("Cycle: fetching sales")
                sales = await self.client.fetch_sales(
                    page, session.username, session.user_id
                )
                if not settings.sales_initialized:
                    await self._store_baseline(sales)
                    return

                await self._process_unfinished_sales(page)
                await self._process_new_sales(page, sales)
                await self._process_pending_publishes(page)
                await self._process_pending_boosts(page)
        finally:
            # A fresh browser every few cycles keeps the memory and the page
            # state clean without paying the start-up cost every time.
            self._cycles_since_restart += 1
            if self._cycles_since_restart >= self.config.browser_restart_cycles:
                self._cycles_since_restart = 0
                await self.client.restart_browser()

    async def _store_baseline(self, sales: list[SaleInfo]) -> None:
        inserted = 0
        async with self.db.session() as session:
            existing = await self._existing_ids(
                session, [sale.external_id for sale in sales]
            )
            for info in sales:
                if info.external_id in existing:
                    continue
                session.add(self._sale_row(info, status="baseline"))
                inserted += 1

        await self.settings.set_sales_initialized(True)
        await self.audit.log(
            "sales_baseline",
            f"Базовая линия продаж: найдено {len(sales)}, "
            f"сохранено без действий {inserted}",
        )
        logger.info(
            "Sales baseline stored: found=%s inserted=%s", len(sales), inserted
        )

    async def _poll_chats(
        self, page: Page, session: SessionInfo
    ) -> None:
        """Notify recipients about new private messages and remember seen ones."""
        if not session.user_id:
            logger.debug("Skipping chat polling: user_id is unknown")
            return

        try:
            chats = await self.client.fetch_chats(page, session.user_id)
        except (SessionExpiredError, RateLimitError, CaptchaError):
            raise
        except LayoutChangedError as exc:
            logger.warning("Chat polling failed: %s", exc)
            if not self._chat_error_notified:
                self._chat_error_notified = True
                await self.notifications.generic_error(
                    f"Не удалось проверить чаты Playerok: {exc}"
                )
            return
        except PlayerokError as exc:
            logger.warning("Chat polling failed: %s", exc)
            return
        self._chat_error_notified = False

        now = utcnow()
        for chat in chats:
            if not chat.chat_id or not chat.last_message_id:
                continue
            if chat.chat_type.upper() != "PM":
                continue
            sender = chat.sender_username
            if not sender:
                continue
            if (
                session.username
                and sender.casefold() == session.username.casefold()
            ):
                continue

            async with self.db.session() as db_session:
                state = await db_session.get(ChatState, chat.chat_id)
                is_new = (
                    state is None
                    or state.last_message_id != chat.last_message_id
                )
                if state is None:
                    state = ChatState(chat_id=chat.chat_id)
                    db_session.add(state)
                state.peer_username = sender
                state.last_message_id = chat.last_message_id
                state.last_message_at = chat.last_message_at
                state.unread_count = chat.unread_count
                state.updated_at = now

                should_notify = (
                    is_new
                    and chat.unread_count > 0
                    and not chat.is_read
                )
                if should_notify:
                    state.notified_at = now

            if not should_notify:
                continue

            sent = await self.notifications.chat_message(
                sender, chat.unread_count, chat.last_message_at
            )
            if sent:
                await self.audit.log(
                    "chat_notified",
                    f"Новое сообщение в чате Playerok от {sender} "
                    f"(непрочитанных: {chat.unread_count})",
                )

    async def _process_new_sales(self, page: Page, sales: list[SaleInfo]) -> None:
        if not sales:
            return

        async with self.db.session() as session:
            existing = await self._existing_ids(
                session, [sale.external_id for sale in sales]
            )
        new_sales = [sale for sale in sales if sale.external_id not in existing]
        if not new_sales:
            return

        new_sales.sort(key=lambda item: item.created_at or datetime.min)
        for info in new_sales[: self.config.max_sales_per_cycle]:
            if self._stop_event.is_set():
                return
            settings = await self.settings.get()
            if settings.stopped:
                self._stop_event.set()
                return
            await self._handle_sale(page, info, settings)

    async def _process_unfinished_sales(self, page: Page) -> None:
        """Retry sales that were detected but not confirmed as relisted.

        Rows in ``relisting`` / ``relist_uncertain`` are deliberately excluded:
        the bot may have clicked the relist button already and must not create
        a duplicate listing without a human checking the account.
        """
        cutoff = utcnow() - timedelta(hours=UNFINISHED_SALE_MAX_AGE_HOURS)
        async with self.db.session() as session:
            rows = list(
                await session.scalars(
                    select(Sale)
                    .where(
                        Sale.status.in_(("detected", "relist_failed")),
                        Sale.relisted_at.is_(None),
                        Sale.relist_attempts < RELIST_MAX_ATTEMPTS,
                        Sale.detected_at >= cutoff,
                    )
                    .order_by(Sale.detected_at.asc())
                    .limit(self.config.max_sales_per_cycle)
                )
            )
        for row in rows:
            if self._stop_event.is_set():
                return
            settings = await self.settings.get()
            if settings.stopped:
                self._stop_event.set()
                return
            if not (settings.auto_relist_enabled or settings.auto_boost_enabled):
                return
            await self._relist_and_maybe_boost(
                page, row, self._sale_info_from_row(row), settings
            )

    # ------------------------------------------------------------------ sales

    async def _handle_sale(
        self, page: Page, info: SaleInfo, settings: Any
    ) -> None:
        row = await self._record_sale(info)
        if row is None:
            return

        await self.notifications.sale_detected(row)
        await self.audit.log(
            "sale_detected",
            f"Сделка {info.external_id}: {truncate(info.title or 'без названия', 200)}",
        )

        if not (settings.auto_relist_enabled or settings.auto_boost_enabled):
            await self._set_sale_fields(row.id, status="notified")
            return

        await self._relist_and_maybe_boost(page, row, info, settings)

    async def _relist_and_maybe_boost(
        self, page: Page, row: Sale, info: SaleInfo, settings: Any
    ) -> None:
        attempts = (row.relist_attempts or 0) + 1
        await self._set_sale_fields(
            row.id,
            status="relisting",
            relist_attempts=attempts,
            error=None,
        )
        row.status = "relisting"
        row.relist_attempts = attempts

        slug = info.item_slug or row.item_slug
        try:
            if not slug:
                raise PlayerokError(
                    "Не найден slug проданного товара — невозможно создать копию"
                )
            # Read the sold listing and publish a brand new identical listing.
            # The Playerok button "Выставить повторно" is not used: it only
            # reactivates the old item instead of creating a new one.
            item = await self.client.fetch_item(page, slug)
            skip_reason = self.client.skip_boost_reason(item.name)
            result = await self.client.create_listing(
                page, item, dry_run=bool(settings.dry_run)
            )
            raw_price = item.raw_price or item.price
        except (SessionExpiredError, CaptchaError, RateLimitError) as exc:
            message = truncate(str(exc), 900)
            await self._set_sale_fields(
                row.id, status="relist_failed", error=message
            )
            raise
        except LayoutChangedError as exc:
            message = truncate(str(exc), 900)
            await self._set_sale_fields(
                row.id, status="relist_uncertain", error=message
            )
            raise
        except StopRequestedError:
            # The listing may have already been published. Keep the row in
            # "relisting" so a human can verify before anything is retried.
            raise
        except PaymentUncertainError as exc:
            await self._set_sale_fields(
                row.id, status="relist_uncertain", error=truncate(str(exc), 900)
            )
            raise
        except PlayerokError as exc:
            message = truncate(str(exc), 900)
            await self._set_sale_fields(
                row.id, status="relist_failed", error=message
            )
            await self.audit.log(
                "relist_failed", f"Сделка {info.external_id}: {message}"
            )
            await self.notifications.generic_error(
                f"Не удалось создать новое объявление по сделке "
                f"{info.external_id}: {message}"
            )
            return

        lot_url = result.lot_url
        relisted_at = utcnow()
        await self._set_sale_fields(
            row.id,
            status="relisted",
            relisted_at=relisted_at,
            lot_url=lot_url or row.lot_url,
            item_raw_price=raw_price,
            error=None,
        )
        row.status = "relisted"
        row.relisted_at = relisted_at
        row.item_raw_price = raw_price
        if lot_url:
            row.lot_url = lot_url
        await self.notifications.relisted(row, lot_url, dry_run=result.dry_run)
        await self.audit.log(
            "relisted",
            (
                "DRY-RUN: было бы создано новое объявление по сделке "
                f"{info.external_id}"
                if result.dry_run
                else f"Сделка {info.external_id}: создано новое объявление"
            ),
        )

        if skip_reason:
            # The trigger forbids buying premium for such listings, but the
            # listing still goes on sale.
            await self.audit.log(
                "boost_skipped_trigger",
                f"Сделка {info.external_id}: буст пропущен, сработал триггер "
                f"«{skip_reason}»",
            )
            await self.notifications.boost_skipped(
                row, f"Сработал триггер «{skip_reason}»"
            )

        if not lot_url or result.dry_run:
            if settings.auto_boost_enabled and result.dry_run:
                await self.notifications.boost_deferred(
                    row, "DRY-RUN: буст не выполнялся без нового объявления"
                )
            return

        # The new listing is published right away, with premium when it is
        # allowed. Everything goes through the API, so no browser page is
        # opened and the bot fits into 1 GB of RAM.
        await self._publish_new_listing(
            page,
            row,
            lot_url,
            premium=bool(settings.auto_boost_enabled) and not skip_reason,
            reason=skip_reason,
            item_full_price=raw_price,
        )

    async def _publish_new_listing(
        self,
        page: Page,
        sale: Sale,
        lot_url: str,
        *,
        premium: bool,
        reason: str | None = None,
        item_full_price: Decimal | None = None,
    ) -> None:
        """Publish a fresh listing through the API, with premium if allowed."""
        settings = await self.settings.get()
        max_amount = Decimal(settings.max_boost_amount or 0)
        balance_before = await self.client.fetch_balance(page)
        try:
            outcome = await self.client.publish_listing(
                page,
                lot_url,
                premium=premium,
                max_amount=max_amount,
                item_full_price=item_full_price,
            )
        except (SessionExpiredError, CaptchaError, RateLimitError):
            raise
        except PlayerokError as exc:
            message = truncate(str(exc), 900)
            await self._set_sale_fields(
                sale.id, boost_state="publish_failed", error=message
            )
            sale.boost_state = "publish_failed"
            await self.audit.log(
                "publish_failed", f"Сделка {sale.external_id}: {message}"
            )
            await self.notifications.generic_error(
                f"Не удалось выставить новое объявление: {message}"
            )
            return

        if outcome.status != "success":
            await self._set_sale_fields(
                sale.id, boost_state="publish_failed", error=outcome.reason
            )
            sale.boost_state = "publish_failed"
            await self.notifications.generic_error(
                "Не удалось выставить новое объявление: "
                f"{outcome.reason or 'неизвестная ошибка'}"
            )
            return

        # The listing is on sale. The premium is bought in a separate step:
        # Playerok processes a new listing first and only then allows to buy
        # the priority.
        await self._set_sale_fields(
            sale.id, status="relisted", boost_state="without_premium", error=None
        )
        sale.status = "relisted"
        sale.boost_state = "without_premium"
        await self.audit.log(
            "published_without_premium",
            f"Сделка {sale.external_id}: объявление выставлено"
            + (f" ({reason})" if reason else ""),
        )

        if not premium:
            return

        balance_before = await self.client.fetch_balance(page)
        bought = await self._buy_premium(
            page, sale, lot_url, item_full_price=item_full_price
        )
        if bought is None:
            return

        if bought.status == "success":
            balance_after = await self.client.fetch_balance(page)
            if (
                balance_before is not None
                and balance_after is not None
                and balance_before > balance_after
            ):
                # The real price of the premium is the balance difference.
                bought.amount = balance_before - balance_after
            if bought.amount:
                await self._set_sale_fields(
                    sale.id,
                    status="boosted",
                    boost_state="boosted",
                    boosted_at=utcnow(),
                    error=None,
                )
                sale.status = "boosted"
                sale.boost_state = "boosted"
                await self.settings.record_boost_spend(bought.amount)
                await self.notifications.boost_paid(
                    sale, bought.amount, dry_run=False
                )
                await self.audit.log(
                    "boost_paid",
                    f"Сделка {sale.external_id}: {bought.amount}",
                )
                return
            # Playerok accepted the request but the premium was not applied.
            await self._set_sale_fields(
                sale.id, boost_state="without_premium", error=None
            )
            sale.boost_state = "without_premium"
            await self.notifications.boost_deferred(
                sale, bought.reason or "Премиум не применён"
            )
            return

        if bought.status == "skipped":
            await self._set_sale_fields(
                sale.id,
                boost_state="skipped",
                error=truncate(bought.reason or "", 500),
            )
            sale.boost_state = "skipped"
            await self.notifications.boost_skipped(
                sale, bought.reason or "Превышен лимит стоимости буста"
            )
            await self.audit.log(
                "boost_skipped",
                f"Сделка {sale.external_id}: {bought.reason}",
            )
            return

        # The listing is still processed by Playerok: try again later.
        await self._defer_boost(sale, bought.amount, bought.reason or "Ожидание")

    async def _buy_premium(
        self,
        page: Page,
        sale: Sale,
        lot_url: str,
        *,
        item_full_price: Decimal | None = None,
    ) -> BoostOutcome | None:
        """Buy the premium for a published listing through the API."""
        settings = await self.settings.get()
        max_amount = Decimal(settings.max_boost_amount or 0)
        slug = lot_url.rstrip("/").split("/")[-1]
        item = await self.client._wait_for_item(page, slug, timeout_ms=30000)
        if item is None:
            return BoostOutcome(
                status="deferred",
                amount=None,
                source="main",
                reason="Объявление недоступно на Playerok",
            )
        try:
            return await self.client.boost_listing(
                page,
                item,
                max_amount=max_amount,
                item_full_price=item_full_price,
            )
        except (SessionExpiredError, CaptchaError, RateLimitError):
            raise
        except PlayerokError as exc:
            logger.warning("Cannot buy the premium: %s", exc)
            return BoostOutcome(
                status="deferred",
                amount=None,
                source="main",
                reason=truncate(str(exc), 300),
            )

    async def _publish_without_premium(
        self,
        page: Page,
        sale: Sale,
        lot_url: str,
        *,
        reason: str | None = None,
    ) -> None:
        """Put a new listing on sale without premium.

        Used when auto-boost is disabled or when the single-boost limit was
        exceeded: the listing must be published anyway.
        """
        try:
            published = await self.client.publish_listing(page, lot_url)
        except (SessionExpiredError, CaptchaError, RateLimitError):
            raise
        except PlayerokError as exc:
            message = truncate(str(exc), 900)
            first_failure = sale.boost_state != "publish_failed"
            await self._set_sale_fields(
                sale.id, boost_state="publish_failed", error=message
            )
            sale.boost_state = "publish_failed"
            await self.audit.log(
                "publish_failed", f"Сделка {sale.external_id}: {message}"
            )
            if first_failure:
                await self.notifications.generic_error(
                    f"Не удалось выставить новое объявление без буста: {message}"
                )
            return

        if not published:
            first_failure = sale.boost_state != "publish_failed"
            await self._set_sale_fields(
                sale.id,
                boost_state="publish_failed",
                error="Объявление осталось черновиком",
            )
            sale.boost_state = "publish_failed"
            if first_failure:
                await self.notifications.generic_error(
                    "Новое объявление не удалось опубликовать — оно осталось "
                    "черновиком. Бот повторит публикацию через пару минут."
                )
            return

        await self._set_sale_fields(
            sale.id, status="relisted", error=None
        )
        sale.status = "relisted"
        if sale.boost_state not in ("skipped", "limit"):
            await self._set_sale_fields(sale.id, boost_state="without_premium")
            sale.boost_state = "without_premium"
        await self.audit.log(
            "published_without_premium",
            f"Сделка {sale.external_id}: объявление выставлено без буста"
            + (f" ({reason})" if reason else ""),
        )

    async def _boost_sale(
        self, page: Page, sale: Sale, lot_url: str | None, info: SaleInfo
    ) -> None:
        settings = await self.settings.get()
        if not settings.auto_boost_enabled:
            return
        if not lot_url:
            await self._defer_boost(
                sale, None, "Не найден URL лота для буста"
            )
            return

        sale.lot_url = lot_url
        await self._set_sale_fields(sale.id, boost_state="boosting", error=None)
        sale.boost_state = "boosting"

        remaining = await self.settings.remaining_boost_budget()
        if not settings.dry_run and remaining <= 0:
            await self._set_sale_fields(sale.id, boost_state="limit")
            sale.boost_state = "limit"
            await self._disable_boost_for_limit(None)
            return

        try:
            outcome = await self.client.apply_boost(
                page,
                lot_url,
                dry_run=settings.dry_run,
                remaining_budget=remaining,
                max_amount=Decimal(settings.max_boost_amount or 0),
                item_full_price=sale.item_raw_price,
            )
        except (SessionExpiredError, CaptchaError, RateLimitError) as exc:
            await self._set_sale_fields(
                sale.id, boost_state="failed", error=truncate(str(exc), 900)
            )
            raise
        except LayoutChangedError as exc:
            await self._set_sale_fields(
                sale.id, boost_state="uncertain", error=truncate(str(exc), 900)
            )
            raise
        except StopRequestedError:
            # Payment may have been submitted; never auto-retry it.
            raise
        except PaymentUncertainError as exc:
            await self._set_sale_fields(
                sale.id, boost_state="uncertain", error=truncate(str(exc), 900)
            )
            raise
        except PlayerokError as exc:
            message = truncate(str(exc), 900)
            await self._set_sale_fields(
                sale.id, boost_state="failed", error=message
            )
            await self.audit.log(
                "boost_failed", f"Сделка {info.external_id}: {message}"
            )
            await self.notifications.generic_error(
                f"Не удалось применить буст к {info.external_id}: {message}"
            )
            return

        await self._apply_boost_outcome(page, sale, outcome)

    async def _apply_boost_outcome(
        self, page: Page, sale: Sale, outcome: BoostOutcome
    ) -> None:
        if outcome.status == "success":
            await self.settings.record_boost_spend(
                outcome.amount or Decimal("0")
            )
            await self._set_sale_fields(
                sale.id,
                status="boosted",
                boost_state="boosted",
                boosted_at=utcnow(),
                error=None,
            )
            sale.status = "boosted"
            sale.boost_state = "boosted"
            await self.notifications.boost_paid(
                sale, outcome.amount, dry_run=False
            )
            await self.audit.log(
                "boost_paid", f"Сделка {sale.external_id}: {outcome.amount}"
            )
            return

        if outcome.status == "dry_run":
            await self._set_sale_fields(
                sale.id, boost_state="dry_run", error=None
            )
            sale.boost_state = "dry_run"
            await self.notifications.boost_paid(
                sale, outcome.amount, dry_run=True
            )
            await self.audit.log(
                "boost_dry_run", f"Сделка {sale.external_id}: {outcome.amount}"
            )
            return

        if outcome.status == "deferred":
            await self._defer_boost(
                sale,
                outcome.amount,
                outcome.reason or "Источник оплаты не основной баланс",
            )
            return

        if outcome.status == "limit":
            await self._set_sale_fields(sale.id, boost_state="limit")
            sale.boost_state = "limit"
            await self._disable_boost_for_limit(outcome.amount)
            if sale.lot_url:
                await self._publish_without_premium(page, sale, sale.lot_url)

        if outcome.status == "skipped":
            # The single-boost limit was reached. Auto-boost stays enabled:
            # only this listing is skipped, but it still goes on sale.
            await self._set_sale_fields(
                sale.id,
                boost_state="skipped",
                error=truncate(outcome.reason or "", 900),
            )
            sale.boost_state = "skipped"
            await self.notifications.boost_deferred(
                sale,
                outcome.reason or "Стоимость буста выше лимита за один буст",
                outcome.amount,
            )
            await self.audit.log(
                "boost_skipped",
                f"Сделка {sale.external_id}: {outcome.reason}",
            )
            if sale.lot_url:
                await self._publish_without_premium(page, sale, sale.lot_url)

    async def _defer_boost(
        self, sale: Sale, amount: Decimal | None, reason: str
    ) -> None:
        reason = truncate(reason, 500)
        if not sale.lot_url:
            await self._set_sale_fields(
                sale.id, boost_state="deferred", error=truncate(reason, 900)
            )
            sale.boost_state = "deferred"
            await self.notifications.boost_deferred(sale, reason, amount)
            return

        async with self.db.session() as session:
            existing = await session.scalar(
                select(PendingBoost).where(
                    PendingBoost.sale_external_id == sale.external_id
                )
            )
            if existing is None:
                session.add(
                    PendingBoost(
                        sale_external_id=sale.external_id,
                        lot_url=sale.lot_url,
                        title=sale.title,
                        item_price=sale.item_raw_price,
                        amount=amount,
                        reason=reason,
                        status="pending",
                        attempts=0,
                    )
                )
            elif existing.status in ("pending", "deferred"):
                existing.status = "pending"
                existing.lot_url = sale.lot_url
                existing.item_price = sale.item_raw_price
                existing.amount = amount
                existing.reason = reason
                existing.updated_at = utcnow()

        await self._set_sale_fields(
            sale.id, boost_state="deferred", error=truncate(reason, 900)
        )
        sale.boost_state = "deferred"
        await self.notifications.boost_deferred(sale, reason, amount)
        await self.audit.log(
            "boost_deferred",
            f"Сделка {sale.external_id}: {truncate(reason, 300)}",
        )

    # --------------------------------------------------------- pending boosts

    async def _process_pending_publishes(self, page: Page) -> None:
        """Publish listings that were created but could not be published yet."""
        cutoff = utcnow() - timedelta(minutes=2)
        async with self.db.session() as session:
            rows = list(
                await session.scalars(
                    select(Sale)
                    .where(
                        Sale.status == "relisted",
                        Sale.boost_state == "publish_failed",
                        Sale.relisted_at.is_not(None),
                        Sale.relisted_at <= cutoff,
                    )
                    .order_by(Sale.relisted_at.asc())
                    .limit(self.config.max_sales_per_cycle)
                )
            )
        for row in rows:
            if self._stop_event.is_set():
                return
            if not row.lot_url:
                continue
            logger.info("Retrying the publication of %s", row.external_id)
            await self._publish_without_premium(page, row, row.lot_url)

    async def _process_pending_boosts(self, page: Page) -> None:
        settings = await self.settings.get()
        if not settings.auto_boost_enabled or settings.dry_run:
            return

        cutoff = utcnow() - timedelta(seconds=PENDING_BOOST_RETRY_SECONDS)
        async with self.db.session() as session:
            pending = list(
                await session.scalars(
                    select(PendingBoost)
                    .where(
                        PendingBoost.status == "pending",
                        PendingBoost.updated_at <= cutoff,
                    )
                    .order_by(PendingBoost.created_at.asc())
                    .limit(self.config.max_sales_per_cycle)
                )
            )

        for item in pending:
            if self._stop_event.is_set():
                return
            settings = await self.settings.get()
            if settings.stopped or not settings.auto_boost_enabled:
                return

            remaining = await self.settings.remaining_boost_budget()
            if remaining <= 0:
                await self._disable_boost_for_limit(None)
                return

            try:
                outcome = await self.client.apply_boost(
                    page,
                    item.lot_url,
                    dry_run=False,
                    remaining_budget=remaining,
                    max_amount=Decimal(settings.max_boost_amount or 0),
                    item_full_price=item.item_price,
                )
            except (SessionExpiredError, CaptchaError, RateLimitError) as exc:
                await self._mark_pending(
                    item.id, "failed", truncate(str(exc), 500)
                )
                raise
            except LayoutChangedError as exc:
                await self._mark_pending(
                    item.id, "uncertain", truncate(str(exc), 500)
                )
                raise
            except StopRequestedError:
                raise
            except PaymentUncertainError as exc:
                await self._mark_pending(
                    item.id, "uncertain", truncate(str(exc), 500)
                )
                raise
            except PlayerokError as exc:
                message = truncate(str(exc), 500)
                await self._mark_pending(
                    item.id, "failed", message, increment=True
                )
                await self.notifications.generic_error(
                    f"Не удалось повторить буст для {item.sale_external_id}: "
                    f"{message}"
                )
                continue

            await self._apply_pending_outcome(item, outcome)

    async def _apply_pending_outcome(
        self, item: PendingBoost, outcome: BoostOutcome
    ) -> None:
        if outcome.status == "success":
            await self.settings.record_boost_spend(
                outcome.amount or Decimal("0")
            )
            await self._mark_pending(item.id, "completed", None)
            await self._set_sale_by_external(
                item.sale_external_id,
                status="boosted",
                boost_state="boosted",
                boosted_at=utcnow(),
                error=None,
            )
            sale = await self._get_sale_by_external(item.sale_external_id)
            if sale is not None:
                await self.notifications.boost_paid(
                    sale, outcome.amount, dry_run=False
                )
            await self.audit.log(
                "boost_paid_retry",
                f"Сделка {item.sale_external_id}: {outcome.amount}",
            )
            return

        if outcome.status == "deferred":
            attempts = (item.attempts or 0) + 1
            if attempts >= PENDING_BOOST_MAX_ATTEMPTS:
                await self._mark_pending(
                    item.id,
                    "abandoned",
                    outcome.reason,
                    attempts=attempts,
                )
                await self.notifications.generic_error(
                    f"Буст для сделки {item.sale_external_id} больше не "
                    f"повторяется: {outcome.reason}"
                )
            else:
                await self._mark_pending(
                    item.id, "pending", outcome.reason, attempts=attempts
                )
            return

        if outcome.status == "limit":
            await self._mark_pending(item.id, "cancelled", outcome.reason)
            await self._disable_boost_for_limit(outcome.amount)
            return

        # Defensive fallback: leave the item pending without burning attempts.
        await self._mark_pending(item.id, "pending", outcome.reason)

    # ------------------------------------------------------------- stop / run

    async def request_stop(
        self, reason: str, actor: User | None = None
    ) -> None:
        self._stop_event.set()
        await self.settings.emergency_stop(reason, actor)
        await self.notifications.notify(
            "⛔ <b>Все задачи остановлены</b>\n"
            f"Причина: {html.escape(truncate(reason, 300))}",
            critical=True,
        )

    async def resume(self, actor: User | None = None) -> None:
        self._stop_event.clear()
        await self.settings.resume(actor)
        await self.notifications.notify(
            "▶️ <b>Задачи возобновлены</b>\n"
            "Проверка сессии и продаж начнётся со следующего цикла.",
            critical=True,
        )

    # ---------------------------------------------------------------- helpers

    async def _handle_session_expired(self, exc: Exception) -> None:
        await self.cookies.mark_checked(False, "Сессия Playerok истекла")
        await self._stop_for(
            "Сессия Playerok истекла", self.notifications.session_expired
        )
        await self.audit.log("session_expired", truncate(str(exc), 500))

    async def _stop_for(
        self, reason: str, notifier: Callable[[], Awaitable[None]]
    ) -> None:
        self._stop_event.set()
        await self.settings.emergency_stop(reason)
        await self.audit.log("automation_stopped", reason)
        try:
            await notifier()
        except Exception:  # noqa: BLE001 - notification must never crash us
            logger.exception("Failed to send critical notification")

    async def _disable_boost_for_limit(
        self, amount: Decimal | None
    ) -> None:
        await self.settings.set_feature("auto_boost_enabled", False)
        await self.notifications.limit_reached(amount)
        await self.audit.log(
            "boost_limit", f"Дневной лимит буста превышен: {amount}"
        )

    async def _warn_about_uncertain_operations(self) -> None:
        async with self.db.session() as session:
            uncertain_relist = list(
                await session.scalars(
                    select(Sale).where(
                        Sale.status.in_(("relisting", "relist_uncertain"))
                    )
                )
            )
            uncertain_boost = list(
                await session.scalars(
                    select(Sale).where(Sale.boost_state == "boosting")
                )
            )
        if not uncertain_relist and not uncertain_boost:
            return
        await self.notifications.notify(
            "⚠️ <b>Найдены незавершённые операции</b>\n"
            "Проверьте аккаунт Playerok вручную. Бот не повторяет эти "
            "действия автоматически, чтобы не создать дубль или повторный "
            "платёж.\n"
            f"Перевыставление: {len(uncertain_relist)}\n"
            f"Буст: {len(uncertain_boost)}",
            critical=True,
        )

    async def _record_sale(self, info: SaleInfo) -> Sale | None:
        async with self.db.session() as session:
            existing = await session.scalar(
                select(Sale).where(Sale.external_id == info.external_id)
            )
            if existing is not None:
                return None
            row = self._sale_row(info, status="detected")
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                return None
            return row

    @staticmethod
    async def _existing_ids(session: Any, ids: list[str]) -> set[str]:
        result: set[str] = set()
        unique = list(dict.fromkeys(ids))
        for start in range(0, len(unique), 400):
            chunk = unique[start : start + 400]
            rows = await session.scalars(
                select(Sale.external_id).where(Sale.external_id.in_(chunk))
            )
            result.update(str(item) for item in rows.all())
        return result

    @staticmethod
    def _sale_row(info: SaleInfo, *, status: str) -> Sale:
        return Sale(
            external_id=info.external_id,
            title=truncate(info.title, 500) if info.title else None,
            price=info.price,
            deal_url=info.deal_url or None,
            lot_url=info.lot_url,
            item_slug=info.item_slug,
            status=status,
        )

    @staticmethod
    def _sale_info_from_row(row: Sale) -> SaleInfo:
        return SaleInfo(
            external_id=row.external_id,
            title=row.title,
            price=Decimal(row.price) if row.price is not None else None,
            deal_url=row.deal_url or "",
            lot_url=row.lot_url,
            item_slug=row.item_slug,
            status=row.status,
            created_at=row.detected_at,
        )

    async def _set_sale_fields(self, sale_id: int, **fields: Any) -> None:
        if not fields:
            return
        async with self.db.session() as session:
            await session.execute(
                update(Sale).where(Sale.id == sale_id).values(**fields)
            )

    async def _set_sale_by_external(
        self, external_id: str, **fields: Any
    ) -> None:
        if not fields:
            return
        async with self.db.session() as session:
            await session.execute(
                update(Sale)
                .where(Sale.external_id == external_id)
                .values(**fields)
            )

    async def _get_sale_by_external(self, external_id: str) -> Sale | None:
        async with self.db.session() as session:
            return await session.scalar(
                select(Sale).where(Sale.external_id == external_id)
            )

    async def _mark_pending(
        self,
        pending_id: int,
        status: str,
        reason: str | None,
        *,
        attempts: int | None = None,
        increment: bool = False,
    ) -> None:
        async with self.db.session() as session:
            row = await session.get(PendingBoost, pending_id)
            if row is None:
                return
            row.status = status
            row.reason = truncate(reason, 500) if reason else None
            if attempts is not None:
                row.attempts = attempts
            elif increment:
                row.attempts = (row.attempts or 0) + 1
            row.updated_at = utcnow()


__all__ = [
    "AutomationService",
    "CRITICAL_ERRORS",
    "PENDING_BOOST_MAX_ATTEMPTS",
    "PENDING_BOOST_RETRY_SECONDS",
    "RELIST_MAX_ATTEMPTS",
]
