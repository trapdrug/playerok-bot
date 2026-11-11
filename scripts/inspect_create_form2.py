"""Open the Playerok listing creation form and dump its fields."""

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
      value: (el.value || el.innerText || '').slice(0, 200),
    }));
  const buttons = [...document.querySelectorAll('button, [role="button"], a')]
    .filter(visible)
    .map((el) => ({
      text: (el.innerText || el.textContent || '').trim().slice(0, 60),
      href: el.getAttribute('href') || '',
      cls: (el.className || '').toString().slice(0, 40),
    }))
    .filter((b) => b.text);
  return {fields, buttons, body: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 1500)};
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
                log.append({"op": request.url[:160], "post": raw[:2000]})
                if "mutation" in raw[:400].lower():
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", route_handler)
        await page.goto(
            "https://playerok.com/seller", wait_until="domcontentloaded", timeout=60000
        )
        await page.wait_for_timeout(9000)
        try:
            ok = page.get_by_role("button", name="ОК", exact=True)
            if await ok.count():
                await ok.first.click()
                await page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass

        start = page.get_by_role("button", name="Начать продажу")
        print("start button:", await start.count())
        await start.first.click()
        await page.wait_for_timeout(9000)
        print("URL:", page.url)

        # Choose the Telegram app (the category of the seller's lots).
        telegram = page.get_by_text("Telegram", exact=True)
        print("telegram entries:", await telegram.count())
        if await telegram.count():
            await telegram.first.click()
            await page.wait_for_timeout(8000)
            print("URL after category:", page.url)

        for label in ("Подарки (NFT)", "Подарки"):
            gift = page.get_by_text(label, exact=True)
            if await gift.count():
                print(f"clicking {label!r}")
                await gift.first.click()
                await page.wait_for_timeout(4000)
                break

        nxt = page.get_by_role("button", name="Далее")
        print("next button:", await nxt.count())
        if await nxt.count():
            await nxt.first.click()
            await page.wait_for_timeout(14000)
            print("URL after next:", page.url)

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
