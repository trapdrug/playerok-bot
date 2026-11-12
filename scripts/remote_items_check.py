"""Check the seller's listings inside the Railway container."""

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

USER_ID = "1f01e88d-ae8a-62b0-e266-9389c8f72aec"


async def main() -> None:
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await service.get_cookies()

    async with client.page_session(cookies) as page:
        session = await client.check_session(page)
        count = await client.fetch_items_count(page, session.user_id or "")
        print("всего товаров в продаже:", count)
        for slug in (
            "68b7f7a23bd6-doplata-podarka",
            "8a2aa1dbe4a2-doplata-podarka",
            "dba8b641dcec-doplata-podarok",
        ):
            try:
                item = await client.fetch_item(page, slug)
                print(
                    f"  {slug[:16]} -> статус {item.status}, "
                    f"приоритет {item.priority}, цена {item.price}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {slug[:16]} -> {exc}")
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
