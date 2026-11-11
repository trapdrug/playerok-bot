"""Live check of the new listing flow: copy a sold lot and buy premium.

The script uses the bot's own client code, so it verifies exactly what the
automation does. It publishes one new listing (a copy of a sold lot) and
buys premium for it with the profile balance.
"""

from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal

from app.config import AppConfig
from app.db import Database
from app.playerok.client import PlayerokClient
from app.playerok.selectors import Selectors
from app.security.crypto import CookieCipher
from app.services.audit import AuditService
from app.services.cookies import CookieService
from app.services.settings import SettingsService

# Sold lot: "Снят с продажи" -> the bot must publish a new identical listing.
SOURCE_SLUG = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "2264e9898db6-jelly-bunny---lyubaya-model-bystraya-vydacha-lyuboy-fon"
)


async def _same_image(page, source, copy) -> bool:
    """Compare the picture bytes of the original and the copy."""
    import hashlib

    def digest(url: str) -> str | None:
        try:
            response = page.context.request.get(url)
            if not response.ok:
                return None
            return hashlib.sha256(response.body()).hexdigest()
        except Exception:  # noqa: BLE001
            return None

    source_url = (source.attachment_urls or [None])[0]
    copy_url = (copy.attachment_urls or [None])[0]
    if not source_url or not copy_url:
        return False
    return digest(source_url) == digest(copy_url)


async def main() -> None:
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    settings = SettingsService(db, config, audit)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    cookies_service = CookieService(db, config, cipher, audit)
    selectors = Selectors(config.selectors_path)
    client = PlayerokClient(config, selectors)

    cookies = await cookies_service.get_cookies()
    if not cookies:
        print("NO COOKIES")
        return

    async with client.page_session(cookies) as page:
        session = await client.check_session(page)
        print("session:", session.username, session.user_id)

        count_before = await client.fetch_items_count(page, session.user_id or "")
        print("items in sale before:", count_before)

        item = await client.fetch_item(page, SOURCE_SLUG)
        print("--- source item ---")
        print("name:", item.name)
        print("price:", item.price)
        print("description len:", len(item.description))
        print("attributes:", item.attributes)
        print("data_fields:", json.dumps(item.data_fields, ensure_ascii=False)[:200])
        print("images:", item.attachment_urls)
        print("category:", item.game_category_id, "obtaining:", item.obtaining_type_id)

        result = await client.create_listing(page, item, dry_run=False)
        print("--- created ---")
        print("slug:", result.slug)
        print("lot_url:", result.lot_url)
        print("item_id:", result.item_id)
        print("price:", result.price)
        if not result.slug:
            print("CREATION FAILED")
            return

        copy_item = await client.fetch_item(page, result.slug)
        print("--- copy check ---")
        print("name equal:", copy_item.name == item.name)
        print("price equal:", copy_item.price == item.price)
        print("description equal:", copy_item.description == item.description)
        print("attributes equal:", copy_item.attributes == item.attributes)
        print("data_fields equal:", copy_item.data_fields == item.data_fields)
        print(
            "image bytes equal:",
            await _same_image(page, item, copy_item),
        )
        print("copy priority before boost:", copy_item.priority)
        print("copy status:", copy_item.status)

        outcome = await client.apply_boost(
            page,
            result.lot_url or "",
            dry_run=False,
            remaining_budget=Decimal("500"),
        )
        print("--- boost ---")
        print("status:", outcome.status)
        print("amount:", outcome.amount)
        print("source:", outcome.source)
        print("reason:", outcome.reason)

        boosted = await client.fetch_item(page, result.slug)
        print("copy priority after boost:", boosted.priority)

        count_after = await client.fetch_items_count(page, session.user_id or "")
        print("items in sale after:", count_after)

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
