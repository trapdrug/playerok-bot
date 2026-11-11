"""Test the free publish of a fresh draft listing (mutation path)."""

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

SOURCE = "23c6cef827c2-easter-egg---lyubaya-model-bystraya-vydacha-lyuboy-fon"


async def main() -> None:
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
        item = await client.fetch_item(page, SOURCE)
        created = await client.create_listing(page, item, dry_run=False)
        print("draft:", created.slug, created.lot_url)
        if not created.lot_url:
            return

        draft = await client.fetch_item(page, created.slug)
        print("draft status:", draft.status, draft.priority)

        published = await client.publish_listing(page, created.lot_url)
        print("published:", published)

        after = await client.fetch_item(page, created.slug)
        print("after:", after.status, after.priority, after.price)
        print("lot:", created.lot_url)

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
