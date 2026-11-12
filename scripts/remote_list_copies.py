"""List the listings created by the bot and their premium state."""

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
    slugs = sys.argv[1:]
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await service.get_cookies()

    async with client.page_session(cookies) as page:
        for slug in slugs:
            try:
                item = await client.fetch_item(page, slug)
            except Exception as exc:  # noqa: BLE001
                print(slug, "-> ошибка", exc)
                continue
            print(
                f"{slug[:26]:28} {item.status:16} {item.priority:8} "
                f"{item.price} {item.name[:34]}"
            )
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
