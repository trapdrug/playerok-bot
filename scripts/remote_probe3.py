"""Watch the Playerok browser check without reloading the page."""

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
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await service.get_cookies()

    async with client.page_session(cookies or []) as page:
        response = await page.goto(
            "https://playerok.com/profile",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        print("первый запрос: HTTP", response.status if response else None, flush=True)
        for step in range(1, 8):
            await page.wait_for_timeout(15000)
            try:
                text = await page.evaluate(
                    "() => (document.body && document.body.innerText || '')"
                )
            except Exception as exc:  # noqa: BLE001
                text = f"ошибка {exc}"
            head = " ".join(text.split())[:110]
            print(f"через {step * 15}с: {page.url} | {head}", flush=True)
            if "Проверка браузера" not in text:
                print("ПРОВЕРКА ПРОЙДЕНА", flush=True)
                break

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
