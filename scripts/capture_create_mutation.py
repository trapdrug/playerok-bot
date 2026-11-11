"""Capture the createItem mutation payload without publishing anything.

The final mutation is blocked, so no listing is created on the account.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/create_mutation.json")
IMAGE = Path("data/sample_item_image.jpg")

# Original SNOOP DOGG listing image (taken from the GraphQL items response).
IMAGE_URL = (
    "https://i.playerok.com/3PyP88aCcb0_hzkcmexMmKJLJZvheGEGdz45L5s910M/"
    "wm:0.8:soea:5:2:0.2/rs:fill:0:300:1/g:no/quality:99/"
    "czM6Ly9wbGF5ZXJvay8vaW1hZ2VzLzFmMWQ2YjRiLWMwNTMtNjAzMC0yMjY3LWU3MmUzODNjZmZmZC5wbmc.jpg"
)

DUMP_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const fileInputs = [...document.querySelectorAll('input[type="file"]')].map((el) => ({
    accept: el.getAttribute('accept') || '',
    multiple: el.multiple,
    id: el.id || '',
    name: el.getAttribute('name') || '',
  }));
  const allInputs = [...document.querySelectorAll('input, textarea')].map((el) => ({
    tag: el.tagName,
    type: el.getAttribute('type') || '',
    name: el.getAttribute('name') || '',
    id: el.id || '',
    placeholder: el.getAttribute('placeholder') || '',
    visible: visible(el),
    value: (el.value || '').slice(0, 120),
  }));
  const buttons = [...document.querySelectorAll('button, [role="button"], a, div')]
    .filter(visible)
    .map((el) => ({
      tag: el.tagName,
      text: (el.innerText || el.textContent || '').trim().slice(0, 40),
      disabled: el.disabled === true,
    }))
    .filter((b) => b.text && b.text.length < 30)
    .slice(0, 80);
  return {fileInputs, allInputs, buttons};
}
"""


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

    telegram = page.get_by_text("Telegram", exact=True)
    if await telegram.count():
        await telegram.first.click()
        await page.wait_for_timeout(7000)
    gift = page.get_by_text("Подарки (NFT)", exact=False)
    if await gift.count():
        await gift.first.click()
        await page.wait_for_timeout(3500)
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
    captured: list[dict] = []

    if not IMAGE.exists():
        import urllib.request

        urllib.request.urlretrieve(IMAGE_URL, IMAGE)
        print("image downloaded:", IMAGE.stat().st_size, "bytes")

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
            if "graphql" in request.url.lower() and request.method == "POST":
                raw = request.post_data or ""
                captured.append({"url": request.url[:160], "post": raw[:6000]})
                if "mutation" in raw[:400].lower():
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", route_handler)
        await wizard_to_form(page)
        print("URL:", page.url)

        info = await page.evaluate(DUMP_JS)
        print("file inputs:", json.dumps(info["fileInputs"], ensure_ascii=False))
        print(
            "inputs:",
            json.dumps(
                [i for i in info["allInputs"] if i["type"] != "search"],
                ensure_ascii=False,
            )[:2000],
        )

        # Fill the form with the original listing data.
        title = "⭐️ SNOOP DOGG - ЛЮБАЯ МОДЕЛЬ | ⚡️ БЫСТРАЯ ВЫДАЧА | 🎨 ЛЮБОЙ ФОН"
        description = (
            "Коллекция Snoop Dogg\n\nℹ️ Быстрая выдача по @username!\n"
            "ℹ️ Цена указана за самую дешёвую модель."
        )
        await page.fill("input[name='title']", title)
        await page.fill("textarea[name='description']", description)
        comment = page.locator("textarea[name^='dataFields.']")
        if await comment.count():
            await comment.first.fill("Показатель: 842")
        price = page.locator("#item-price")
        if await price.count():
            await price.first.fill("850")
        await page.wait_for_timeout(1500)

        # Pick the characteristic (collection) "Snoop Dogg".
        characteristic = page.get_by_text("Snoop Dogg", exact=True)
        print("characteristic matches:", await characteristic.count())
        if await characteristic.count():
            await characteristic.first.click()
            await page.wait_for_timeout(6000)
            after = await page.evaluate(DUMP_JS)
            print("buttons after characteristic:", json.dumps(after["buttons"], ensure_ascii=False)[:1500])
            print("body:", (await page.evaluate("() => document.body.innerText")).replace("\n", " ")[:800])

        # Upload the original image.
        file_input = page.locator("input[type='file']")
        print("file inputs:", await file_input.count())
        if await file_input.count():
            await file_input.first.set_input_files(str(IMAGE))
            await page.wait_for_timeout(6000)

        save = page.get_by_role("button", name="Сохранить")
        print("save disabled:", await save.first.is_disabled())
        await save.first.click(force=True)
        await page.wait_for_timeout(10000)

        print("=== captured POSTs ===")
        for item in captured:
            print(item["url"][:120])
            print(item["post"][:1500])
            print("---")

        OUT.write_text(
            json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
