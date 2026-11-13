"""Compare the real premium price (site) with the API price (no payment)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
SLUGS = [
    "bbd4817d8a91-krutoy-podarok-swag-bag",
    "68ba7443fa4f-snoop-dogg---lyubaya-model-bystraya-vydacha-lyuboy-fon",
]


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
        for slug in SLUGS:
            await page.goto(
                f"https://playerok.com/products/{slug}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await page.wait_for_timeout(6000)
            for label in ("Повысить статус", "Поднять в топ"):
                button = page.get_by_text(label, exact=True)
                if await button.count():
                    await button.first.click()
                    await page.wait_for_timeout(6000)
                    break
            text = await page.evaluate("() => document.body.innerText")
            prices = [
                part
                for part in text.split("\n")
                if "₽" in part or "Премиум" in part
            ]
            print(f"--- {slug}")
            for part in prices[:14]:
                print("   ", part.strip()[:110])
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1500)
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
