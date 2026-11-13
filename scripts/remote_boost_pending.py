"""Boost the listings that were published but have no premium yet."""

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
from app.services.settings import SettingsService  # noqa: E402

SKIP_MARKERS = ("доплата подарка", "доплата подарок")
USER_ID = "1f01e88d-ae8a-62b0-e266-9389c8f72aec"


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
    max_amount = current.max_boost_amount or 0

    async with client.page_session(cookies) as page:
        slugs = sys.argv[1:]
        if not slugs:
            data = await client._graphql_persisted(
                page,
                "items",
                {
                    "pagination": {"first": 16},
                    "filter": {
                        "userId": USER_ID,
                        "status": ["APPROVED", "PENDING_APPROVAL"],
                        "withOfficial": False,
                    },
                    "showForbiddenImage": True,
                },
                str(client.selectors.get("graphql.items_sha256", "")),
            )
            items = data.get("items") or {}
            slugs = [
                ((edge or {}).get("node") or {}).get("slug")
                for edge in (items.get("edges") or [])
            ]
            print("товаров в продаже/на модерации:", items.get("totalCount"))

        for slug in slugs:
            if not slug:
                continue
            item = await client.fetch_item(page, slug)
            title = item.name or ""
            if item.priority and item.priority.upper() == "PREMIUM":
                print(f"- {slug[:26]} уже с премиумом")
                continue
            reason = skip_reason(title)
            if reason:
                print(f"- {slug[:26]} пропущен по триггеру «{reason}»")
                continue
            if item.status and item.status.upper() not in ("APPROVED", "COMPLETED"):
                print(f"- {slug[:26]} ещё обрабатывается ({item.status})")
                continue
            bought = await client.boost_listing(
                page,
                item,
                max_amount=max_amount,
                item_full_price=item.raw_price or item.price,
            )
            print(
                f"- {slug[:26]} буст: {bought.status} {bought.amount or ''} "
                f"{bought.reason or ''}"
            )
            after = await client.fetch_item(page, slug)
            print(f"    стало: {after.status} / {after.priority}")

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
