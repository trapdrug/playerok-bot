"""Check whether Playerok blocks the Railway IP even without cookies."""

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


async def probe(page, url: str, label: str) -> None:
    try:
        response = await page.goto(
            url, wait_until="domcontentloaded", timeout=60000
        )
        body = await page.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 400)"
        )
        print(
            f"[{label}] {url} -> HTTP {response.status if response else None}",
            flush=True,
        )
        print(f"    текст: {body!r}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] {url} -> ошибка {exc}", flush=True)


async def main() -> None:
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))

    print("=== без cookies ===", flush=True)
    async with client.page_session([]) as page:
        await probe(page, "https://playerok.com/", "без cookies")
        await probe(page, "https://playerok.com/profile", "без cookies")
        await probe(page, "https://playerok.com/products/", "без cookies")

    print("=== с cookies ===", flush=True)
    cookies = await service.get_cookies()
    async with client.page_session(cookies or []) as page:
        await probe(page, "https://playerok.com/", "с cookies")
        await probe(page, "https://playerok.com/profile", "с cookies")

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
