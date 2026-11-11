from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import or_, select

from app.db import Database
from app.models import Sale, User
from app.services.settings import SettingsService
from app.utils.parsing import format_dt, money, truncate


logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self, bot: Bot, db: Database, settings_service: SettingsService
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings_service = settings_service

    async def _recipients(self, *, critical: bool = False) -> list[int]:
        """Return recipients using each account's own notification preference.

        Critical events always reach active main admins. Other accounts receive
        them only when their personal notifications are enabled.
        """
        query = select(User.telegram_id).where(
            User.is_active.is_(True), User.telegram_id.is_not(None)
        )
        if critical:
            query = query.where(
                or_(
                    User.role == "main_admin",
                    User.notifications_enabled.is_(True),
                )
            )
        else:
            query = query.where(User.notifications_enabled.is_(True))
        async with self.db.session() as session:
            result = await session.scalars(query)
            return [int(item) for item in result.all() if item]

    async def notify(
        self, text: str, *, critical: bool = False
    ) -> int:
        recipients = await self._recipients(critical=critical)
        sent = 0
        for chat_id in recipients:
            try:
                await self.bot.send_message(
                    chat_id,
                    text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                sent += 1
            except TelegramForbiddenError:
                logger.warning("Notification blocked by user chat_id=%s", chat_id)
            except TelegramRetryAfter as exc:
                logger.warning(
                    "Telegram rate limit for chat_id=%s, retry_after=%s",
                    chat_id,
                    exc.retry_after,
                )
            except TelegramBadRequest as exc:
                logger.warning("Telegram rejected notification: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Notification failed: %s", exc)
        return sent

    async def sale_detected(self, sale: Sale) -> None:
        title = html.escape(sale.title or "Без названия")
        await self.notify(
            "💰 <b>Новая продажа</b>\n"
            f"Товар: {title}\n"
            f"Сумма: {money(sale.price)}\n"
            f"ID сделки: <code>{html.escape(sale.external_id)}</code>"
        )

    async def chat_message(
        self,
        sender: str,
        unread: int,
        created_at: datetime | None = None,
    ) -> int:
        text = (
            "💬 <b>Новое сообщение в чате Playerok</b>\n"
            f"От: <b>{html.escape(sender)}</b>\n"
            f"Непрочитанных в чате: {unread}"
        )
        if created_at is not None:
            text += (
                "\nВремя: "
                f"{format_dt(created_at, self.settings_service.config.timezone)}"
            )
        return await self.notify(text)

    async def relisted(
        self, sale: Sale, lot_url: str | None = None, *, dry_run: bool = False
    ) -> None:
        title = html.escape(sale.title or "Без названия")
        suffix = f"\n<a href=\"{html.escape(lot_url)}\">Открыть лот</a>" if lot_url else ""
        prefix = (
            "🧪 <b>DRY-RUN: было бы создано новое объявление</b>\n"
            if dry_run
            else "🆕 <b>Создано новое объявление</b>\n"
        )
        await self.notify(
            f"{prefix}Товар: {title}{suffix}\n"
            "Название, описание, картинка и цена скопированы из проданного лота."
        )

    async def boost_paid(
        self, sale: Sale, amount: Decimal | None, dry_run: bool = False
    ) -> None:
        title = html.escape(sale.title or "Без названия")
        prefix = "🧪 <b>DRY-RUN: буст был бы оплачен</b>" if dry_run else "🚀 <b>Буст оплачен</b>"
        await self.notify(
            f"{prefix}\nТовар: {title}\nСумма: {money(amount)}\nИсточник: основной баланс"
        )

    async def boost_skipped(self, sale: Sale, reason: str) -> None:
        title = html.escape(sale.title or "Без названия")
        await self.notify(
            "⏭ <b>Буст пропущен</b>\n"
            f"Товар: {title}\n"
            f"Причина: {html.escape(reason)}\n"
            "Объявление выставлено в продажу без премиума."
        )

    async def boost_deferred(
        self, sale: Sale, reason: str, amount: Decimal | None = None
    ) -> None:
        title = html.escape(sale.title or "Без названия")
        await self.notify(
            "⏸ <b>Буст отложен</b>\n"
            f"Товар: {title}\n"
            f"Причина: {html.escape(reason)}\n"
            f"Сумма: {money(amount)}"
        )

    async def limit_reached(self, amount: Decimal | None = None) -> None:
        await self.notify(
            "⛔ <b>Дневной лимит буста превышен</b>\n"
            f"Попытка списания: {money(amount)}\n"
            "Автовыставление с бустом отключено. Проверьте /limit и /logs."
        )

    async def session_expired(self) -> None:
        await self.notify(
            "🔴 <b>Сессия Playerok недействительна</b>\n"
            "Все задачи остановлены. Загрузите свежий JSON с cookies и выполните /resume.\n"
            "Cookies и пароли в этот чат не выводятся.",
            critical=True,
        )

    async def captcha(self) -> None:
        await self.notify(
            "🛑 <b>Обнаружена капча</b>\n"
            "Задачи остановлены. Бот не пытается обходить капчу и не повторяет действия вслепую.",
            critical=True,
        )

    async def rate_limited(self) -> None:
        await self.notify(
            "🛑 <b>Playerok вернул 429 / слишком много запросов</b>\n"
            "Задачи остановлены. Повторите позже и проверьте /logs.",
            critical=True,
        )

    async def layout_changed(self, details: str | None = None) -> None:
        suffix = f"\n<code>{html.escape(truncate(details or '', 400))}</code>" if details else ""
        await self.notify(
            "🛑 <b>Похоже, изменилась вёрстка Playerok</b>\n"
            "Задачи остановлены. Проверьте selectors.yaml и /logs."
            f"{suffix}",
            critical=True,
        )

    async def generic_error(self, details: str) -> None:
        await self.notify(
            "⚠️ <b>Ошибка автоматизации</b>\n"
            f"<code>{html.escape(truncate(details, 700))}</code>"
        )

    async def no_cookies(self) -> None:
        await self.notify(
            "ℹ️ <b>Cookies Playerok ещё не загружены</b>\n"
            "Главный администратор может отправить боту JSON-файл с cookies "
            "(сообщение будет удалено).",
            critical=True,
        )

    async def cookies_saved(self, count: int, username: str | None = None) -> None:
        who = f"\nАккаунт Playerok: <b>{html.escape(username)}</b>" if username else ""
        await self.notify(
            f"✅ Cookies сохранены и зашифрованы ({count} шт.).{who}",
            critical=True,
        )


__all__ = ["NotificationService"]
