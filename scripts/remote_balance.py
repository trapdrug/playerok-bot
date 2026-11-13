"""Show the full balance of the profile (value, available, pending)."""

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
        data = await client._graphql_persisted(
            page,
            "viewerLocalBalance",
            {},
            str(client.selectors.get("graphql.balance_sha256", "")),
        )
        print(json.dumps(data, ensure_ascii=False, indent=1)[:900])
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
