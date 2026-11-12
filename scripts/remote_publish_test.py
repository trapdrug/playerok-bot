"""Try the publishItem mutation directly (inside the container)."""

from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, "/app")

from app.config import AppConfig  # noqa: E402
from app.db import Database  # noqa: E402
from app.playerok.client import PUBLISH_ITEM_QUERY, PlayerokClient  # noqa: E402
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
        print("item:", item.id, item.status, item.priority, item.price)
        free_id = await client._free_priority_id(page, item)
        print("free id:", free_id)
        if not free_id:
            print("нет бесплатного статуса")
            return
        result = await client._run_graphql_request(
            page,
            "POST",
            client._absolute("/graphql"),
            headers={
                "apollo-require-preflight": "true",
                "x-apollo-operation-name": "publishItem",
            },
            json_body={
                "query": PUBLISH_ITEM_QUERY,
                "operationName": "publishItem",
                "variables": {
                    "input": {
                        "transactionProviderId": "LOCAL",
                        "priorityStatuses": [free_id],
                        "itemId": item.id,
                        "keepInSale": False,
                    }
                },
            },
        )
        print("HTTP:", result.get("status"))
        print("ответ:", json.dumps(result.get("json"), ensure_ascii=False)[:1200])
        after = await client.fetch_item(page, slug)
        print("после:", after.status, after.priority)
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
