"""Walk the Playerok listing wizard to the item form and dump its fields."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/create_form_capture.json")

DUMP_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const fields = [...document.querySelectorAll('input, textarea, select, [contenteditable="true"]')]
    .filter(visible)
    .map((el) => ({
      tag: el.tagName,
      type: el.getAttribute('type') || '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      placeholder: el.getAttribute('placeholder') || '',
      aria: el.getAttribute('aria-label') || '',
      testid: el.getAttribute('data-testid') || '',
      value: (el.value || el.innerText || '').slice(0, 300),
    }));
  const buttons = [...document.querySelectorAll('button, [role="button"], a')]
    .filter(visible)
    .map((el) => ({
      tag: el.tagName,
      text: (el.innerText || el.textContent || '').trim().slice(0, 60),
      href: el.getAttribute('href') || '',
      disabled: el.disabled === true,
      cls: (el.className || '').toString().slice(0, 40),
    }))
    .filter((b) => b.text);
  const giftNodes = [...document.querySelectorAll('*')]
    .filter((el) => visible(el) && el.children.length === 0 &&
      /Подарки|Звезды/.test(el.textContent || ''))
    .map((el) => ({
      tag: el.tagName,
      text: (el.textContent || '').trim().slice(0, 40),
      cls: (el.className || '').toString().slice(0, 50),
      parentCls: el.parentElement ? (el.parentElement.className || '').toString().slice(0, 50) : '',
    }));
  return {fields, buttons, giftNodes, body: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 1500)};
}
"""


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
            if "graphql" in request.url.lower() and request.method == "POST":
                raw = request.post_data or ""
                log.append({"url": request.url[:160], "post": raw[:3000]})
                if "mutation" in raw[:400].lower():
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", route_handler)
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

        data = await page.evaluate(DUMP_JS)
        print("step1 body:", data["body"][:300])

        telegram = page.get_by_text("Telegram", exact=True)
        if await telegram.count():
            await telegram.first.click()
            await page.wait_for_timeout(8000)

        data = await page.evaluate(DUMP_JS)
        print("step2 gift nodes:", json.dumps(data["giftNodes"], ensure_ascii=False)[:1200])

        # Click the "Подарки (NFT)" option by text (any tag).
        gift = page.get_by_text("Подарки (NFT)", exact=False)
        print("gift matches:", await gift.count())
        if await gift.count():
            await gift.first.click()
            await page.wait_for_timeout(4000)

        nxt = page.get_by_role("button", name="Далее")
        print("next disabled:", await nxt.first.is_disabled())
        if not await nxt.first.is_disabled():
            await nxt.first.click()
            await page.wait_for_timeout(12000)
        print("URL:", page.url)

        # Step "Способ передачи": choose "Подарок" (as in the original lot).
        delivery = page.get_by_text("Подарок", exact=True)
        print("delivery matches:", await delivery.count())
        if await delivery.count():
            await delivery.first.click()
            await page.wait_for_timeout(3000)
            nxt = page.get_by_role("button", name="Далее")
            print("next disabled after delivery:", await nxt.first.is_disabled())
            if not await nxt.first.is_disabled():
                await nxt.first.click()
                await page.wait_for_timeout(15000)
        print("URL:", page.url)

        form = await page.evaluate(DUMP_JS)
        print("=== fields ===")
        print(json.dumps(form["fields"], ensure_ascii=False, indent=1)[:4000])
        print("=== buttons ===")
        print(json.dumps(form["buttons"], ensure_ascii=False)[:2500])
        print("=== body ===")
        print(form["body"][:1500])
        await page.screenshot(path="data/create_form.png", full_page=False)

        OUT.write_text(
            json.dumps({"form": form, "log": log}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
