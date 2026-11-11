from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.utils.parsing import utcnow


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")

    @property
    def is_main_admin(self) -> bool:
        return self.role == "main_admin"

    @property
    def is_worker(self) -> bool:
        return self.role == "worker"

    @property
    def can_manage_automation(self) -> bool:
        return self.role in {"main_admin", "user"}


class PlayerokSession(Base):
    __tablename__ = "playerok_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    cookies_encrypted: Mapped[str] = mapped_column(Text)
    cookies_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cookies_updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_alive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_relist_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_boost_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_boost_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    max_boost_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("925")
    )
    stopped: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sales_initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    items_in_sale: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    deal_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    lot_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    item_slug: Mapped[str | None] = mapped_column(String(300), nullable=True)
    item_raw_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    status: Mapped[str] = mapped_column(String(40), default="detected", index=True)
    boost_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    relist_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    relisted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    boosted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatState(Base):
    """Last seen message per Playerok chat, used to deduplicate notifications."""

    __tablename__ = "chat_state"

    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    peer_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class PendingBoost(Base):
    __tablename__ = "pending_boosts"
    __table_args__ = (UniqueConstraint("sale_external_id", name="uq_pending_boost_sale"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_external_id: Mapped[str] = mapped_column(String(128), index=True)
    lot_url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    item_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DailyBoostSpend(Base):
    __tablename__ = "daily_boost_spend"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped[User | None] = relationship(back_populates="logs")


__all__ = [
    "AuditLog",
    "Base",
    "BotSettings",
    "ChatState",
    "DailyBoostSpend",
    "PendingBoost",
    "PlayerokSession",
    "Sale",
    "User",
]
