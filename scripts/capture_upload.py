"""Capture the image upload + createItem multipart payload (mutation blocked)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/upload_capture.json")
IMAGE = Path("data/sample_item_image.jpg")

DUMP_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const imgs = [...document.querySelectorAll('img')]
    .filter(visible)
    .map((el) => el.src.slice(0, 160));
  const previews = [...document.querySelectorAll('[class*="upload"], [class*="preview"]')]
    .filter(visible)
    .map((el) => (el.innerText || '').trim().slice(0, 40));
  return {imgs, previews, body: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 600)};
}
"""

WIZARD = [
    ("Telegram", 7000),
    ("Подарки (NFT)", 3500),
]


async def wizard_to_form(page) -> None:
    await page.goto(
        "https://playerok.com/sell", wait_until="domcontentloaded", timeout=60000
    )
    await page.wait_for_timeout(9000)
    try:
        ok = page.get_by_role("button", name="ОК", exact=True)
        if await ok.count():
            await ok.first.click()
            await page.wait_for_timeout(1200)
    except Exception:  # noqa: BLE001
        pass
    for text, wait in WIZARD:
        loc = page.get_by_text(text, exact=True)
        if await loc.count():
            await loc.first.click()
            await page.wait_for_timeout(wait)
    nxt = page.get_by_role("button", name="Далее")
    if not await nxt.first.is_disabled():
        await nxt.first.click()
        await page.wait_for_timeout(11000)
    delivery = page.get_by_text("Подарок", exact=True)
    if await delivery.count():
        await delivery.first.click()
        await page.wait_for_timeout(3000)
    nxt = page.get_by_role("button", name="Далее")
    if not await nxt.first.is_disabled():
        await nxt.first.click()
        await page.wait_for_timeout(14000)


async def main() -> None:
    log: list[dict] = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def route_handler(route, request) -> None:  # noqa: ANN001
            url = request.url
            body = request.post_data or ""
            interesting = (
                "graphql" in url.lower()
                or "upload" in url.lower()
                or "api.playerok.com" in url.lower()
                or "rest-api" in url.lower()
            )
            if interesting and request.method in ("POST", "PUT"):
                entry = {
                    "method": request.method,
                    "url": url[:300],
                    "ctype": request.headers.get("content-type", ""),
                    "post": body[:4000],
                }
                log.append(entry)
                if "graphql" in url.lower() and "createItem" in body:
                    print("BLOCKED createItem")
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", route_handler)
        await wizard_to_form(page)

        # Fill the form.
        await page.fill(
            "input[name='title']",
            "⭐️ SNOOP DOGG - ЛЮБАЯ МОДЕЛЬ | ⚡️ БЫСТРАЯ ВЫДАЧА | 🎨 ЛЮБОЙ ФОН",
        )
        await page.fill(
            "textarea[name='description']",
            "Коллекция Snoop Dogg\n\nℹ️ Быстрая выдача по @username!",
        )
        comment = page.locator("textarea[name^='dataFields.']")
        if await comment.count():
            await comment.first.fill("Показатель: 842")
        price = page.locator("#item-price")
        if await price.count():
            await price.first.fill("850")
        characteristic = page.get_by_text("Snoop Dogg", exact=True)
        if await characteristic.count():
            await characteristic.first.click()
            await page.wait_for_timeout(3000)
        await page.wait_for_timeout(1000)

        # Upload through the visible "Загрузить" button.
        async with page.expect_file_chooser(timeout=15000) as info:
            await page.get_by_text("Загрузить", exact=True).first.click()
        chooser = await info.value
        await chooser.set_files(str(IMAGE))
        await page.wait_for_timeout(12000)

        state = await page.evaluate(DUMP_JS)
        print("previews:", json.dumps(state["previews"], ensure_ascii=False)[:600])
        print("images:", json.dumps(state["imgs"], ensure_ascii=False)[:900])
        print("body:", state["body"][:400])

        save = page.get_by_role("button", name="Сохранить")
        print("save disabled:", await save.first.is_disabled())
        await save.first.click(force=True)
        await page.wait_for_timeout(12000)

        print("=== requests ===")
        for item in log:
            print(item["method"], item["url"][:200], "ctype=", item["ctype"][:40])
            if item["post"]:
                print("   ", item["post"][:700].replace("\r\n", " | "))

        OUT.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
