"""Open the seller page and capture what it renders."""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")


async def main() -> None:
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(
            "https://playerok.com/seller", wait_until="domcontentloaded", timeout=60000
        )
        await page.wait_for_timeout(15000)
        print("URL:", page.url)
        print("TITLE:", await page.title())
        body = await page.evaluate("() => document.body.innerText")
        print("BODY LEN:", len(body))
        print("BODY:", " ".join(body.split())[:1500])
        buttons = await page.evaluate(
            """
            () => [...document.querySelectorAll('button, a')].map((el) => ({
              tag: el.tagName,
              text: (el.innerText || '').trim().slice(0, 50),
              href: el.getAttribute('href') || '',
            })).filter((b) => b.text).slice(0, 40)
            """
        )
        for item in buttons:
            print(" -", item)
        await page.screenshot(path="data/seller_page.png", full_page=False)
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
