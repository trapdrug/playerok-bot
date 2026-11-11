"""Inspect the real Playerok relist / premium controls (read-only).

The script only opens pages and reads the DOM. It never confirms a purchase.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")

DEAL_URLS = [
    "https://playerok.com/deal/1f1d3799-fe34-6010-8eed-46c9870da70e",
    "https://playerok.com/deal/1f1ddda2-024a-6ec0-d390-15fbe5a268de",
]
LOT_URLS = [
    "https://playerok.com/products/6127aa5d2514-mad-pumpkin---lyubaya-model-bystraya-vydacha-lyuboy-fon",
    "https://playerok.com/products/0c85e02c7bae-snoop-dogg---lyubaya-model-bystraya-vydacha-lyuboy-fon",
    "https://playerok.com/products/23c6cef827c2-easter-egg---lyubaya-model-bystraya-vydacha-lyuboy-fon",
]

DUMP_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const buttons = [...document.querySelectorAll('button, a[role="button"], [role="button"]')]
    .filter(visible)
    .map((el) => ({
      tag: el.tagName,
      text: (el.innerText || el.textContent || '').trim().slice(0, 80),
      aria: el.getAttribute('aria-label') || '',
      href: el.getAttribute('href') || '',
      cls: (el.className || '').toString().slice(0, 80),
    }))
    .filter((b) => b.text || b.aria || b.href);
  const body = document.body.innerText.replace(/\\s+/g, ' ').slice(0, 1200);
  return {buttons, body};
}
"""


async def dump(page, url: str, label: str) -> None:
    print("=" * 70)
    print(label, url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:  # noqa: BLE001
        print("goto failed:", exc)
        return
    await page.wait_for_timeout(6000)
    data = await page.evaluate(DUMP_JS)
    print("TITLE:", await page.title())
    print("BODY:", data["body"][:600])
    print("CONTROLS:")
    for item in data["buttons"]:
        print(
            "  -",
            item["tag"],
            repr(item["text"]),
            "aria=", repr(item["aria"]),
            "href=", item["href"][:70],
        )


async def main() -> None:
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        for url in DEAL_URLS:
            await dump(page, url, "DEAL")
        for url in LOT_URLS:
            await dump(page, url, "LOT")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
