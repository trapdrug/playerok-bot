"""Repair: publish and boost the listings the bot left unfinished.

Runs inside the Railway container and uses the same API calls as the bot.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys

sys.path.insert(0, "/app")

from app.config import AppConfig  # noqa: E402
from app.db import Database  # noqa: E402
from app.playerok.client import PlayerokClient  # noqa: E402
from app.playerok.selectors import Selectors  # noqa: E402
from app.security.crypto import CookieCipher  # noqa: E402
from app.services.audit import AuditService  # noqa: E402
from app.services.cookies import CookieService  # noqa: E402
from app.services.settings import SettingsService  # noqa: E402

# Titles that must never be boosted.
SKIP_MARKERS = ("доплата подарка", "доплата подарок")


def skip_reason(title: str) -> str | None:
    lowered = (title or "").casefold()
    for marker in SKIP_MARKERS:
        if marker in lowered:
            return marker
    return None


async def main() -> None:
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    service = CookieService(db, config, cipher, audit)
    settings = SettingsService(db, config, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await service.get_cookies()
    current = await settings.get()
    max_amount = float(current.max_boost_amount or 0)

    connection = sqlite3.connect("/data/playerok.db")
    rows = list(
        connection.execute(
            "select external_id, title, price, lot_url, item_raw_price, status, "
            "boost_state from sales where lot_url is not null "
            "and status not in ('boosted', 'baseline') order by id desc limit 20"
        )
    )

    async with client.page_session(cookies) as page:
        for external_id, title, price, lot_url, raw_price, status, boost_state in rows:
            slug = lot_url.rstrip("/").split("/")[-1]
            try:
                item = await client.fetch_item(page, slug)
            except Exception as exc:  # noqa: BLE001
                print(f"{external_id}: не найдено ({exc})")
                continue
            print(
                f"{external_id}: {item.status} / {item.priority} / {item.price} "
                f"| {item.name[:40]}"
            )
            if item.status and item.status.upper() not in ("APPROVED", "COMPLETED"):
                # The listing was never published: publish it (free).
                outcome = await client.publish_listing(page, lot_url)
                print(f"   публикация: {outcome.status} {outcome.reason or ''}")
                item = await client.fetch_item(page, slug)
                print(f"   стало: {item.status} / {item.priority}")
                if item.status and item.status.upper() not in (
                    "APPROVED",
                    "COMPLETED",
                ):
                    continue
            reason = skip_reason(item.name or title)
            if reason:
                print(f"   буст пропущен по триггеру «{reason}»")
                continue
            if item.priority and item.priority.upper() == "PREMIUM":
                print("   уже с премиумом")
                continue
            from decimal import Decimal

            bought = await client.boost_listing(
                page,
                item,
                max_amount=Decimal(str(max_amount)),
                item_full_price=item.raw_price or item.price,
            )
            print(f"   буст: {bought.status} {bought.amount or ''} {bought.reason or ''}")
            after = await client.fetch_item(page, slug)
            print(f"   стало: {after.status} / {after.priority}")

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
