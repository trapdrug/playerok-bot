"""Check the listing created by the bot."""

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
        item = await client.fetch_item(page, slug)
        print("name:", item.name)
        print("price:", item.price, "raw_price:", item.raw_price)
        print("priority:", item.priority)
        print("status:", item.status)
        print("attributes:", item.attributes)
        print("description len:", len(item.description))
        print("data fields:", len(item.data_fields))
        print("images:", len(item.attachment_urls))
        print("lot: https://playerok.com/products/" + slug)

        session = await client.check_session(page)
        count = await client.fetch_items_count(page, session.user_id or "")
        print("items in sale:", count)

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
