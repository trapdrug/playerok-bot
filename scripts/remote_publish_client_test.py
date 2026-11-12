"""Run the bot's own publish_listing() on a draft (inside the container)."""

from __future__ import annotations

import asyncio
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
    slug = sys.argv[1]
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await service.get_cookies()

    async with client.page_session(cookies) as page:
        lot_url = f"https://playerok.com/products/{slug}"
        item = await client.fetch_item(page, slug)
        print("до:", item.status, item.priority, item.id, item.price)
        result = await client.publish_listing(page, lot_url)
        print("publish_listing ->", result)
        after = await client.fetch_item(page, slug)
        print("после:", after.status, after.priority)
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
