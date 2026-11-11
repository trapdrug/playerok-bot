"""Capture how Playerok publishes a draft listing for free (no premium)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import AppConfig
from app.db import Database
from app.playerok.client import PlayerokClient
from app.playerok.selectors import Selectors
from app.security.crypto import CookieCipher
from app.services.audit import AuditService
from app.services.cookies import CookieService

SOURCE = "2264e9898db6-jelly-bunny---lyubaya-model-bystraya-vydacha-lyuboy-fon"


async def main() -> None:
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    cookies_service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await cookies_service.get_cookies()
    log: list[dict] = []

    async with client.page_session(cookies) as page:
        await client.check_session(page)
        if len(sys.argv) > 1:
            lot_url = "https://playerok.com/products/" + sys.argv[1]
            created_slug = sys.argv[1]
            print("using existing draft:", created_slug)
        else:
            item = await client.fetch_item(page, SOURCE)
            created = await client.create_listing(page, item, dry_run=False)
            lot_url = created.lot_url or ""
            created_slug = created.slug or ""
            print("draft created:", created_slug, lot_url)
        if not lot_url:
            return

        async def route_handler(route, request) -> None:  # noqa: ANN001
            url = request.url
            if request.method in ("POST", "PUT"):
                if "feature-flags" not in url.lower():
                    log.append(
                        {
                            "method": request.method,
                            "op": "",
                            "url": url[:300],
                            "post": (request.post_data or "")[:1200],
                        }
                    )
                    print("REQ:", request.method, url[:150])
            await route.continue_()

        await page.route("**/*", route_handler)
        await client._goto(page, lot_url)
        await client._dismiss_cookie_banner(page)
        await page.wait_for_timeout(4000)

        buttons = await page.evaluate(
            """
            () => [...document.querySelectorAll('button, [role="button"]')]
                .map((el) => (el.innerText || '').trim())
                .filter(Boolean)
            """
        )
        print("buttons:", buttons)

        publish = page.get_by_text("Выставить", exact=True)
        print("publish button:", await publish.count())
        await publish.first.click()
        for _ in range(20):
            await page.wait_for_timeout(1500)
            modals = await page.evaluate(
                "() => document.querySelectorAll('.MuiModal-root').length"
            )
            if modals:
                print("modal appeared after", "1.5s ticks")
                break

        modal = await page.evaluate(
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
              return {
                radios: [...container.querySelectorAll('input[type="radio"]')].map((el) => ({
                  name: el.getAttribute('name') || '',
                  value: el.getAttribute('value') || '',
                  checked: el.checked,
                  label: el.closest('label')
                    ? (el.closest('label').innerText || '').replace(/\\s+/g, ' ').slice(0, 80)
                    : '',
                })),
                buttons: [...container.querySelectorAll('button, [role="button"]')]
                  .filter(visible)
                  .map((el) => (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 60)),
                text: (container.innerText || '').replace(/\\s+/g, ' ').slice(0, 700),
              };
            }
            """
        )
        print("=== promo modal ===")
        print(json.dumps(modal, ensure_ascii=False, indent=1)[:2000])

        free = page.locator(".MuiModal-root").get_by_text(
            "Оставлять в продаже", exact=True
        )
        print("free button in modal:", await free.count())
        try:
            await free.first.wait_for(state="visible", timeout=25000)
            print("free button visible")
            modal_html = await page.evaluate(
                "() => { const m = document.querySelector('.MuiModal-root');"
                " return m ? m.outerHTML.slice(-4000) : ''; }"
            )
            print("MODAL HTML:", modal_html[:4000])
            await free.first.click()
            await page.wait_for_timeout(9000)
            body = await page.evaluate("() => document.body.innerText")
            print("BODY AFTER CLICK:", " ".join(body.split())[:400])
        except Exception as exc:  # noqa: BLE001
            print("free button not clickable:", str(exc)[:200])

        print("=== requests ===")
        for entry in log:
            print(entry["method"], entry["op"], entry["url"][:200])
            if entry["post"]:
                print("   ", entry["post"][:500])

        Path("data/free_publish_capture.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
