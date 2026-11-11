"""Check that the single-boost limit stops the payment."""

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


async def main() -> None:
    slug = sys.argv[1]
    limit = Decimal(sys.argv[2]) if len(sys.argv) > 2 else Decimal("10")
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
        print("before:", before.priority, before.status, before.price)

        outcome = await client.apply_boost(
            page,
            f"https://playerok.com/products/{slug}",
            dry_run=False,
            remaining_budget=Decimal("500"),
            max_amount=limit,
            item_full_price=before.raw_price,
        )
        print("status:", outcome.status)
        print("amount:", outcome.amount)
        print("reason:", outcome.reason)

        after = await client.fetch_item(page, slug)
        print("after:", after.priority, after.status, after.price)

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
