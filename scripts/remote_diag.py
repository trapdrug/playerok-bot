"""Diagnose the Playerok connection from inside the Railway container."""

from __future__ import annotations

import asyncio
import sys
import time

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
    print("cookies в базе:", len(cookies or []), flush=True)

    started = time.time()
    async with client.page_session(cookies) as page:
        print(f"контекст браузера: {time.time() - started:.1f}s", flush=True)

        step = time.time()
        response = await page.goto(
            "https://playerok.com/profile",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        print(
            f"goto: status={response.status if response else None} "
            f"{time.time() - step:.1f}s url={page.url}",
            flush=True,
        )

        step = time.time()
        length = await asyncio.wait_for(
            page.evaluate("() => (document.body.innerText || '').length"),
            timeout=60,
        )
        print(f"body: {length} симв. за {time.time() - step:.1f}s", flush=True)

        step = time.time()
        chats = await asyncio.wait_for(
            client.fetch_chats(page, USER_ID), timeout=90
        )
        print(f"чатов: {len(chats)} за {time.time() - step:.1f}s", flush=True)

        step = time.time()
        session = await asyncio.wait_for(client.check_session(page), timeout=90)
        print(
            f"сессия: {session.username} id={session.user_id} "
            f"за {time.time() - step:.1f}s",
            flush=True,
        )
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
