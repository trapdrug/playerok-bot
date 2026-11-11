from __future__ import annotations

import html
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Database
from app.models import AuditLog, User
from app.utils.parsing import truncate


logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def log(
        self,
        action: str,
        details: str | None = None,
        *,
        user: User | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        safe_details = truncate(html.unescape(details or ""), 2000)
        if session is not None:
            session.add(
                AuditLog(
                    user_id=user.id if user else None,
                    username=user.username if user else None,
                    action=action[:80],
                    details=safe_details or None,
                )
            )
            return
        async with self.db.session() as own_session:
            own_session.add(
                AuditLog(
                    user_id=user.id if user else None,
                    username=user.username if user else None,
                    action=action[:80],
                    details=safe_details or None,
                )
            )
        logger.info("audit action=%s user=%s", action, user.username if user else "system")

    async def recent(self, limit: int = 30) -> list[AuditLog]:
        async with self.db.session() as session:
            result = await session.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
            )
            return list(result.all())


__all__ = ["AuditService"]
