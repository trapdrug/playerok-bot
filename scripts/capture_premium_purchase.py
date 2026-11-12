"""Capture the premium purchase request of Playerok (without paying)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
SLUG = "bea83dad1590-surge-board---lyubaya-model-bystraya-vydacha-lyuboy-fon"
OUT = Path("data/premium_purchase_capture.json")


async def main() -> None:
    captured: list[dict] = []

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
            body = request.post_data or ""
            if request.method == "POST" and "mutation" in body[:400].lower():
                name = ""
                try:
                    name = str(json.loads(body).get("operationName") or "")
                except Exception:  # noqa: BLE001
                    name = ""
                if name != "validateItemData":
                    captured.append(
                        {
                            "url": request.url[:200],
                            "body": body[:3000],
                        }
                    )
                    print("BLOCKED (not paid):", name)
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", route_handler)
        await page.goto(
            f"https://playerok.com/products/{SLUG}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(7000)

        for label in ("Повысить статус", "Поднять в топ", "Выставить"):
            button = page.get_by_text(label, exact=True)
            if await button.count():
                print("нажато:", label)
                await button.first.click()
                await page.wait_for_timeout(6000)
                break

        for label in ("Повысить до Премиум", "Выставить Премиум"):
            button = page.get_by_text(label, exact=False)
            if await button.count():
                print("нажато:", label)
                await button.first.click()
                await page.wait_for_timeout(6000)
                break

        body = await page.evaluate("() => document.body.innerText")
        print("окно оплаты:", " ".join(body.split())[:400])

        for label in ("Перейти к оплате", "Оплатить"):
            button = page.get_by_role("button", name=label, exact=True)
            if await button.count():
                print("нажато:", label)
                await button.first.click()
                await page.wait_for_timeout(7000)
                break

        await context.close()

    OUT.write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("captured:", len(captured))
    for item in captured:
        print(item["body"][:600])
        print("---")


if __name__ == "__main__":
    asyncio.run(main())
