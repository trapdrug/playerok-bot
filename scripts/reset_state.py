"""Reset the bot state after the layout failure and prepare the next cycle."""

from __future__ import annotations

import asyncio

from app.config import AppConfig
from app.db import Database
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.settings import SettingsService


async def main() -> None:
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    auth = AuthService(db, config, audit)
    admin = await auth.ensure_main_admin()
    settings = SettingsService(db, config, audit)

    await settings.resume(actor=admin)
    await settings.set_feature("auto_relist_enabled", True, actor=admin)
    await settings.set_feature("auto_boost_enabled", True, actor=admin)

    current = await settings.get()
    print("stopped:", current.stopped)
    print("auto_relist:", current.auto_relist_enabled)
    print("auto_boost:", current.auto_boost_enabled)
    print("dry_run:", current.dry_run)
    print("daily limit:", current.daily_boost_limit)
    print("single boost limit:", current.max_boost_amount)

    # The failed sale never created a listing (the button was not found),
    # so it is safe to let the new logic process it again.
    from sqlalchemy import select

    from app.models import Sale

    async with db.session() as session:
        rows = list(
            await session.scalars(
                select(Sale).where(
                    Sale.status.in_(("relist_uncertain", "relisting", "detected"))
                )
            )
        )
        for row in rows:
            print("resetting sale:", row.external_id, row.status, row.title)
            row.status = "detected"
            row.relist_attempts = 0
            row.error = None

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
