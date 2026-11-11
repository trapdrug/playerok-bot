"""Remove listings from sale (test cleanup)."""

from __future__ import annotations

import asyncio
import sys

from app.config import AppConfig
from app.db import Database
from app.playerok.client import PlayerokClient
from app.playerok.selectors import Selectors
from app.security.crypto import CookieCipher
from app.services.audit import AuditService
from app.services.cookies import CookieService


async def main() -> None:
    slugs = sys.argv[1:]
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    cookies_service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await cookies_service.get_cookies()

    async with client.page_session(cookies) as page:
        await client.check_session(page)
        for slug in slugs:
            await client._goto(page, f"https://playerok.com/products/{slug}")
            await client._dismiss_cookie_banner(page)
            await page.wait_for_timeout(2500)

            clicked = await page.evaluate(
                """
                () => {
                  const nodes = [...document.querySelectorAll('*')].filter(
                    (el) => el.children.length === 0 &&
                      (el.textContent || '').trim() === 'Снять с продажи'
                  );
                  if (!nodes.length) return false;
                  const button = nodes[0].closest('button') || nodes[0];
                  button.click();
                  return true;
                }
                """
            )
            if not clicked:
                print(slug, "-> no button")
                continue
            modal = {"ok": False}
            for _ in range(10):
                await page.wait_for_timeout(2000)
                modal = await page.evaluate(
                    """
                    () => {
                      const visible = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 &&
                          style.visibility !== 'hidden' &&
                          style.display !== 'none';
                      };
                      const candidates = [
                        ...document.querySelectorAll(
                          '[role="dialog"], .MuiModal-root, [role="alertdialog"]'
                        ),
                      ].filter(visible);
                      const container = candidates.find((el) =>
                        (el.innerText || '').includes('Снять товар с продажи')
                      );
                      if (!container) return {ok: false};
                      const nodes = [...container.querySelectorAll('button')]
                        .filter(visible);
                      const buttons = nodes.map(
                        (el) => (el.innerText || '').trim()
                      );
                      const index = buttons.findIndex(
                        (text) => text.trim() === 'Снять с продажи'
                      );
                      if (index >= 0) nodes[index].click();
                      return {ok: true, buttons, clicked: index >= 0};
                    }
                    """
                )
                if modal.get("ok"):
                    break
            await page.wait_for_timeout(5000)
            item = await client.fetch_item(page, slug)
            print(slug[:20], "->", item.status, item.priority, modal)
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
