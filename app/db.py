from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base


def _ensure_sqlite_columns(sync_connection) -> None:  # noqa: ANN001
    """Add columns that were introduced after the first SQLite schema."""
    columns = {
        row[1]
        for row in sync_connection.exec_driver_sql("PRAGMA table_info(users)")
    }
    if "notifications_enabled" not in columns:
        sync_connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN notifications_enabled "
            "BOOLEAN NOT NULL DEFAULT 1"
        )
    settings_columns = {
        row[1]
        for row in sync_connection.exec_driver_sql("PRAGMA table_info(bot_settings)")
    }
    if "items_in_sale" not in settings_columns:
        sync_connection.exec_driver_sql(
            "ALTER TABLE bot_settings ADD COLUMN items_in_sale INTEGER"
        )
    if "items_checked_at" not in settings_columns:
        sync_connection.exec_driver_sql(
            "ALTER TABLE bot_settings ADD COLUMN items_checked_at DATETIME"
        )
    if "max_boost_amount" not in settings_columns:
        sync_connection.exec_driver_sql(
            "ALTER TABLE bot_settings ADD COLUMN max_boost_amount "
            "NUMERIC(12, 2) DEFAULT 925"
        )
    sales_columns = {
        row[1] for row in sync_connection.exec_driver_sql("PRAGMA table_info(sales)")
    }
    if "item_raw_price" not in sales_columns:
        sync_connection.exec_driver_sql(
            "ALTER TABLE sales ADD COLUMN item_raw_price NUMERIC(12, 2)"
        )
    pending_columns = {
        row[1]
        for row in sync_connection.exec_driver_sql("PRAGMA table_info(pending_boosts)")
    }
    if "item_price" not in pending_columns:
        sync_connection.exec_driver_sql(
            "ALTER TABLE pending_boosts ADD COLUMN item_price NUMERIC(12, 2)"
        )


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )
        self._configure_sqlite()

    def _configure_sqlite(self) -> None:
        if not self.engine.url.get_backend_name().startswith("sqlite"):
            return

        @event.listens_for(self.engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

    async def init(self) -> None:
        if self.engine.url.get_backend_name().startswith("sqlite"):
            database = self.engine.url.database
            if database and database != ":memory:":
                Path(database).parent.mkdir(parents=True, exist_ok=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            if self.engine.url.get_backend_name().startswith("sqlite"):
                await connection.run_sync(_ensure_sqlite_columns)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


__all__ = ["Database"]
