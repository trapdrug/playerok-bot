"""Boost an existing listing with the profile balance and verify the result."""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

from app.config import AppConfig
from app.db import Database
from app.playerok.client import PlayerokClient
from app.playerok.selectors import Selectors
from app.security.crypto import CookieCipher
from app.services.audit import AuditService
from app.services.cookies import CookieService
from app.services.settings import SettingsService


async def main() -> None:
    slug = sys.argv[1]
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    settings = SettingsService(db, config, audit)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    cookies_service = CookieService(db, config, cipher, audit)
    selectors = Selectors(config.selectors_path)
    client = PlayerokClient(config, selectors)

    cookies = await cookies_service.get_cookies()
    async with client.page_session(cookies) as page:
        session = await client.check_session(page)
        print("session:", session.username)

        before = await client.fetch_item(page, slug)
        print("before:", before.priority, before.price, before.status)

        outcome = await client.apply_boost(
            page,
            f"https://playerok.com/products/{slug}",
            dry_run=False,
            remaining_budget=Decimal("500"),
        )
        print("boost status:", outcome.status)
        print("boost amount:", outcome.amount)
        print("boost source:", outcome.source)
        print("boost reason:", outcome.reason)

        after = await client.fetch_item(page, slug)
        print("after:", after.priority, after.price, after.status)

        count = await client.fetch_items_count(page, session.user_id or "")
        print("items in sale:", count)

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
