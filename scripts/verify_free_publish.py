"""Publish a draft listing without premium (free) and verify the result."""

from __future__ import annotations

import asyncio
import sys

from app.config import AppConfig
from app.db import Database
from app.playerok.client import PlayerokClient
from app.playerok.selectors import Selectors
from app.security.crypto import CookieCipher
from app.services.audit import AuditService
from app.services.cookies import CookieService


async def main() -> None:
    slug = sys.argv[1]
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    cookies_service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await cookies_service.get_cookies()

    async with client.page_session(cookies) as page:
        await client.check_session(page)
        before = await client.fetch_item(page, slug)
        print("before:", before.status, before.priority, before.price)

        published = await client.publish_listing(
            page, f"https://playerok.com/products/{slug}"
        )
        print("published:", published)

        after = await client.fetch_item(page, slug)
        print("after:", after.status, after.priority, after.price)
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
