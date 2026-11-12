"""Check the listings created by the bot and the premium state."""

from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, "/app")

from app.config import AppConfig  # noqa: E402
from app.db import Database  # noqa: E402
from app.playerok.client import PlayerokClient  # noqa: E402
from app.playerok.selectors import Selectors  # noqa: E402
from app.security.crypto import CookieCipher  # noqa: E402
from app.services.audit import AuditService  # noqa: E402
from app.services.cookies import CookieService  # noqa: E402


async def main() -> None:
    slugs = sys.argv[1:]
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = Cipher = CookieCipher(config.fernet_key.get_secret_value())
    service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await service.get_cookies()

    async with client.page_session(cookies) as page:
        balance = await client.fetch_balance(page)
        print("баланс:", balance)
        session = await client.check_session(page)
        print("товаров в продаже:", await client.fetch_items_count(page, session.user_id or ""))
        for slug in slugs:
            item = await client.fetch_item(page, slug)
            print(f"{slug}:")
            print(f"   название: {item.name}")
            print(f"   статус: {item.status} | приоритет: {item.priority}")
            print(f"   цена: {item.price} | картинок: {len(item.attachment_urls)}")
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
