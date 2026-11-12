"""Buy the premium for a listing that is already published (API, from the container)."""

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
        item = await client.fetch_item(page, slug)
        balance_before = await client.fetch_balance(page)
        print("до:", item.status, item.priority, item.price, "| баланс", balance_before)

        options = await client._priority_options(page, item)
        premium_id = None
        price = None
        for entry in options:
            if str(entry.get("type")).upper() == "PREMIUM":
                premium_id = entry.get("id")
                price = entry.get("price")
        print("варианты премиума:", premium_id, "цена", price)

        if not premium_id:
            print("премиум недоступен")
            return
        outcome = await client.increase_priority(page, item.id, premium_id)
        print("результат:", outcome.status, "| причина:", outcome.reason)

        after = await client.fetch_item(page, slug)
        balance_after = await client.fetch_balance(page)
        print("после:", after.status, after.priority, "| баланс", balance_after)
        if balance_before is not None and balance_after is not None:
            print("списано с баланса:", balance_before - balance_after)
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
