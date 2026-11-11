"""Find the real "relist" control on the seller's products page (read-only)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
PAGE_URL = "https://playerok.com/profile/Kolakussia/products"
OUT = Path("data/relist_capture.json")

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
      text: (el.innerText || el.textContent || '').trim().slice(0, 60),
      aria: el.getAttribute('aria-label') || '',
      href: el.getAttribute('href') || '',
    }))
    .filter((b) => b.text || b.aria || b.href);
  const links = [...document.querySelectorAll('a[href*="/products/"]')]
    .filter(visible)
    .map((el) => el.getAttribute('href'))
    .slice(0, 20);
  const tabs = [...document.querySelectorAll('button, [role="tab"], a')]
    .filter(visible)
    .map((el) => (el.innerText || '').trim())
    .filter((t) => t && t.length < 30);
  return {buttons, links, tabs, body: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 900)};
}
"""


async def main() -> None:
    captured: list[dict] = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        def on_request(request) -> None:  # noqa: ANN001
            if "operationName=items" in request.url:
                captured.append({"url": request.url})

        page.on("request", on_request)

        await page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(7000)

        print("=== TABS / CONTROLS (Активные) ===")
        data = await page.evaluate(DUMP_JS)
        print("tabs:", data["tabs"][:40])
        print("buttons:", json.dumps(data["buttons"], ensure_ascii=False)[:1500])
        print("body:", data["body"][:400])

        # Switch to the "Завершённые" tab.
        for label in ("Завершённые", "Завершенные"):
            locator = page.get_by_text(label, exact=True)
            if await locator.count():
                print(f"\n=== clicking tab {label} ===")
                await locator.first.click()
                await page.wait_for_timeout(7000)
                break

        data = await page.evaluate(DUMP_JS)
        print("\n=== AFTER TAB SWITCH ===")
        print("body:", data["body"][:800])
        print("buttons:", json.dumps(data["buttons"], ensure_ascii=False)[:2500])
        print("product links:", data["links"])

        OUT.write_text(
            json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        await context.close()

    from urllib.parse import parse_qs, urlparse

    print("\n=== captured items requests ===")
    for item in captured:
        query = parse_qs(urlparse(item["url"]).query)
        print("vars:", query.get("variables"))


if __name__ == "__main__":
    asyncio.run(main())
