from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update

from app.config import AppConfig
from app.db import Database
from app.models import BotSettings, DailyBoostSpend, PendingBoost, User
from app.services.audit import AuditService
from app.utils.parsing import local_date, money, utcnow


FEATURE_LABELS = {
    "auto_relist_enabled": "Автовыставление",
    "auto_boost_enabled": "Автовыставление с бустом",
    "dry_run": "Тестовый режим (dry-run)",
}


class SettingsService:
    def __init__(self, db: Database, config: AppConfig, audit: AuditService) -> None:
        self.db = db
        self.config = config
        self.audit = audit

    async def get(self) -> BotSettings:
        async with self.db.session() as session:
            row = await session.get(BotSettings, 1)
            if row is None:
                row = BotSettings(
                    id=1,
                    dry_run=self.config.dry_run,
                    auto_relist_enabled=False,
                    auto_boost_enabled=False,
                    daily_boost_limit=Decimal("0"),
                )
                session.add(row)
                await session.flush()
            return row

    async def set_feature(
        self, feature: str, value: bool, actor: User | None = None
    ) -> BotSettings:
        if feature not in FEATURE_LABELS:
            raise ValueError("Неизвестная настройка")
        async with self.db.session() as session:
            row = await session.get(BotSettings, 1)
            if row is None:
                row = BotSettings(id=1)
                session.add(row)
                await session.flush()
            old = getattr(row, feature)
            setattr(row, feature, bool(value))
            if feature == "auto_boost_enabled" and not value:
                await session.execute(
                    update(PendingBoost)
                    .where(PendingBoost.status == "pending")
                    .values(status="cancelled", reason="Автобуст выключен")
                )
            await self.audit.log(
                "setting_changed",
                f"{FEATURE_LABELS[feature]}: {'✅' if old else '❌'} → {'✅' if value else '❌'}",
                user=actor,
                session=session,
            )
            return row

    async def toggle(
        self, feature: str, actor: User | None = None
    ) -> tuple[BotSettings, bool]:
        current = await self.get()
        new_value = not bool(getattr(current, feature))
        updated = await self.set_feature(feature, new_value, actor)
        return updated, new_value

    async def emergency_stop(
        self, reason: str, actor: User | None = None
    ) -> BotSettings:
        async with self.db.session() as session:
            row = await session.get(BotSettings, 1)
            if row is None:
                row = BotSettings(id=1)
                session.add(row)
                await session.flush()
            row.stopped = True
            row.stop_reason = reason[:300]
            row.stopped_at = utcnow()
            row.auto_relist_enabled = False
            row.auto_boost_enabled = False
            await session.execute(
                update(PendingBoost)
                .where(PendingBoost.status == "pending")
                .values(status="cancelled", reason=f"Остановка: {reason[:200]}")
            )
            await self.audit.log(
                "stop_all", f"Все задачи остановлены: {reason}", user=actor, session=session
            )
            return row

    async def resume(self, actor: User | None = None) -> BotSettings:
        async with self.db.session() as session:
            row = await session.get(BotSettings, 1)
            if row is None:
                row = BotSettings(id=1)
                session.add(row)
                await session.flush()
            row.stopped = False
            row.stop_reason = None
            row.stopped_at = None
            await self.audit.log(
                "resume", "Задачи возобновлены", user=actor, session=session
            )
            return row

    async def set_daily_limit(
        self, amount: Decimal, actor: User | None = None
    ) -> tuple[BotSettings, bool]:
        if amount < 0:
            raise ValueError("Лимит не может быть отрицательным")
        amount = amount.quantize(Decimal("0.01"))
        async with self.db.session() as session:
            row = await session.get(BotSettings, 1)
            if row is None:
                row = BotSettings(id=1)
                session.add(row)
                await session.flush()
            row.daily_boost_limit = amount
            disabled = False
            spend = await self._get_spend_for_day(session, local_date(self.config.timezone))
            if amount == 0 or spend > amount:
                if row.auto_boost_enabled:
                    row.auto_boost_enabled = False
                    disabled = True
                await session.execute(
                    update(PendingBoost)
                    .where(PendingBoost.status == "pending")
                    .values(status="cancelled", reason="Дневной лимит буста исчерпан")
                )
            await self.audit.log(
                "limit_changed",
                f"Дневной лимит буста: {money(amount)}",
                user=actor,
                session=session,
            )
            return row, disabled

    async def set_max_boost_amount(
        self, amount: Decimal, actor: User | None = None
    ) -> BotSettings:
        """Limit the price of a single boost."""
        if amount < 0:
            raise ValueError("Лимит буста не может быть отрицательным")
        amount = amount.quantize(Decimal("0.01"))
        async with self.db.session() as session:
            row = await session.get(BotSettings, 1)
            if row is None:
                row = BotSettings(id=1, max_boost_amount=amount)
                session.add(row)
            else:
                row.max_boost_amount = amount
            await self.audit.log(
                "boost_limit_changed",
                f"Лимит за один буст: {money(amount)}",
                user=actor,
                session=session,
            )
            return row

    async def get_today_spend(self) -> Decimal:
        async with self.db.session() as session:
            return await self._get_spend_for_day(session, local_date(self.config.timezone))

    async def remaining_boost_budget(self) -> Decimal:
        settings = await self.get()
        spent = await self.get_today_spend()
        return max(Decimal("0"), Decimal(settings.daily_boost_limit) - spent)

    async def can_spend(self, amount: Decimal) -> bool:
        if amount <= 0:
            return False
        settings = await self.get()
        limit = Decimal(settings.daily_boost_limit)
        if limit <= 0:
            return False
        spent = await self.get_today_spend()
        return spent + amount <= limit

    async def record_boost_spend(self, amount: Decimal) -> Decimal:
        if amount <= 0:
            return await self.get_today_spend()
        day = local_date(self.config.timezone)
        async with self.db.session() as session:
            row = await session.scalar(
                select(DailyBoostSpend).where(DailyBoostSpend.day == day)
            )
            if row is None:
                row = DailyBoostSpend(day=day, amount=Decimal("0"))
                session.add(row)
                await session.flush()
            row.amount = Decimal(row.amount) + amount.quantize(Decimal("0.01"))
            await session.flush()
            return Decimal(row.amount)

    async def set_sales_initialized(self, value: bool) -> None:
        async with self.db.session() as session:
            row = await session.get(BotSettings, 1)
            if row is None:
                row = BotSettings(id=1, sales_initialized=value)
                session.add(row)
            else:
                row.sales_initialized = value

    async def set_items_in_sale(self, count: int | None) -> None:
        """Store how many of the seller's listings are currently on sale."""
        async with self.db.session() as session:
            row = await session.get(BotSettings, 1)
            if row is None:
                row = BotSettings(id=1, items_in_sale=count)
                session.add(row)
            else:
                row.items_in_sale = count
            row.items_checked_at = utcnow()

    async def _get_spend_for_day(self, session, day) -> Decimal:  # noqa: ANN001
        row = await session.scalar(
            select(DailyBoostSpend).where(DailyBoostSpend.day == day)
        )
        return Decimal(row.amount) if row else Decimal("0")


__all__ = ["FEATURE_LABELS", "SettingsService"]
