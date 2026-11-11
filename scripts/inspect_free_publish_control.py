"""Find the "Оставлять в продаже" control on a draft listing."""

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

DUMP_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const nodes = [...document.querySelectorAll('*')].filter((el) => {
    if (el.children.length > 0) return false;
    const text = (el.textContent || '').trim();
    return text === 'Оставлять в продаже' || text === 'Выставить';
  });
  return nodes.map((el) => ({
    tag: el.tagName,
    text: (el.textContent || '').trim(),
    visible: visible(el),
    role: el.getAttribute('role') || '',
    cls: (el.className || '').toString().slice(0, 60),
    parentTag: el.parentElement ? el.parentElement.tagName : '',
    parentCls: el.parentElement
      ? (el.parentElement.className || '').toString().slice(0, 60)
      : '',
    parentHtml: el.parentElement
      ? el.parentElement.outerHTML.slice(0, 400)
      : '',
  }));
}
"""


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

    async with client.page_session(cookies) as page:
        await client.check_session(page)
        await client._goto(page, f"https://playerok.com/products/{slug}")
        await client._dismiss_cookie_banner(page)
        await page.wait_for_timeout(3000)
        print("=== page controls ===")
        for item in await page.evaluate(DUMP_JS):
            print(json.dumps(item, ensure_ascii=False)[:600])

        publish = page.get_by_text("Выставить", exact=True)
        if await publish.count():
            await publish.first.click()
            for _ in range(20):
                await page.wait_for_timeout(1500)
                modals = await page.evaluate(
                    "() => document.querySelectorAll('.MuiModal-root').length"
                )
                if modals:
                    break
            await page.wait_for_timeout(3000)
            print("=== modal controls ===")
            for item in await page.evaluate(DUMP_JS):
                print(json.dumps(item, ensure_ascii=False)[:600])
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
