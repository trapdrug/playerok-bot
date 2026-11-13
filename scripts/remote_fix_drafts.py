"""Find the seller's draft listings on Playerok and publish them."""

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
        data = await client._graphql_persisted(
            page,
            "items",
            {
                "pagination": {"first": 16},
                "filter": {
                    "userId": USER_ID,
                    "status": ["DRAFT"],
                    "withOfficial": False,
                },
                "showForbiddenImage": True,
            },
            str(client.selectors.get("graphql.items_sha256", "")),
        )
        items = data.get("items") or {}
        edges = items.get("edges") or []
        print("черновиков найдено:", items.get("totalCount"))

        for edge in edges:
            node = (edge or {}).get("node") or {}
            slug = node.get("slug")
            title = node.get("name") or ""
            print(f"- {slug} | {title[:50]} | {node.get('price')}")
            if not slug:
                continue
            lot_url = f"https://playerok.com/products/{slug}"
            outcome = await client.publish_listing(page, lot_url)
            print(f"   публикация: {outcome.status} {outcome.reason or ''}")
            item = await client.fetch_item(page, slug)
            print(f"   стало: {item.status} / {item.priority}")
            if item.status and item.status.upper() not in ("APPROVED", "COMPLETED"):
                continue
            reason = skip_reason(item.name or title)
            if reason:
                print(f"   буст пропущен по триггеру «{reason}»")
                continue
            if item.priority and item.priority.upper() == "PREMIUM":
                print("   уже с премиумом")
                continue
            bought = await client.boost_listing(
                page,
                item,
                max_amount=max_amount,
                item_full_price=item.raw_price or item.price,
            )
            print(
                f"   буст: {bought.status} {bought.amount or ''} "
                f"{bought.reason or ''}"
            )
            after = await client.fetch_item(page, slug)
            print(f"   стало: {after.status} / {after.priority}")

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
