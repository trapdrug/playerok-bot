"""Check whether the Playerok browser challenge resolves on its own."""

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
        for attempt in range(1, 7):
            try:
                response = await page.goto(
                    "https://playerok.com/profile",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                status = response.status if response else None
            except Exception as exc:  # noqa: BLE001
                status = f"ошибка {exc}"
            await page.wait_for_timeout(5000)
            try:
                text = await page.evaluate(
                    "() => (document.body && document.body.innerText || '')"
                )
            except Exception:  # noqa: BLE001
                text = ""
            head = " ".join(text.split())[:120]
            print(
                f"попытка {attempt}: HTTP {status} | url={page.url} | {head}",
                flush=True,
            )
            if status == 200 and "Проверка браузера" not in text:
                print("ПРОВЕРКА ПРОЙДЕНА", flush=True)
                break
            await page.wait_for_timeout(10000)

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
