"""Remove a listing from sale via the promotion modal switch."""

from __future__ import annotations

import asyncio
import json
import sys

from app.config import AppConfig
from app.db import Database
from app.playerok.client import PlayerokClient
from app.playerok.selectors import Selectors
from app.security.crypto import CookieCipher
from app.services.audit import AuditService
from app.services.cookies import CookieService


async def main() -> None:
    slug = sys.argv[1]
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    cookies_service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await cookies_service.get_cookies()
    seen: list[str] = []

    async with client.page_session(cookies) as page:
        def on_request(request) -> None:  # noqa: ANN001
            if request.method == "POST" and "graphql" in request.url.lower():
                body = request.post_data or ""
                name = ""
                try:
                    name = str(json.loads(body).get("operationName") or "")
                except Exception:  # noqa: BLE001
                    name = ""
                seen.append(f"POST {name}: {body[:260]}")

        page.on("request", on_request)
        await client.check_session(page)
        await client._goto(page, f"https://playerok.com/products/{slug}")
        await client._dismiss_cookie_banner(page)
        await page.wait_for_timeout(2500)

        entry = await client._find_visible(
            page, "boost.entry_buttons", timeout_ms=15000
        )
        print("entry:", entry is not None)
        await client._safe_click(entry)
        modal = await client._wait_for_modal(page)
        print("modal:", modal is not None)

        state = await page.evaluate(
            """
            () => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' && style.display !== 'none';
              };
              const modals = [...document.querySelectorAll('.MuiModal-root')]
                .filter(visible);
              const container = modals.length ? modals[modals.length - 1] : document.body;
              const switches = [...container.querySelectorAll('button[role="switch"]')]
                .filter(visible);
              const info = switches.map((el) => el.getAttribute('aria-checked'));
              if (switches.length) switches[0].click();
              return {info, clicked: switches.length > 0};
            }
            """
        )
        print("switch:", state)
        await page.wait_for_timeout(6000)
        print("requests:")
        for line in seen[-6:]:
            print("  ", line)
        item = await client.fetch_item(page, slug)
        print("after:", item.status, item.priority)
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
