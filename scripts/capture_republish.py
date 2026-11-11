"""Capture what Playerok sends when the seller re-publishes a sold lot.

The request is intercepted and aborted, so nothing is published.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/republish_capture.json")

# Sold lot (currently "Снят с продажи").
LOT = (
    "https://playerok.com/products/23c6cef827c2-easter-egg---"
    "lyubaya-model-bystraya-vydacha-lyuboy-fon"
)

READ_ONLY = {
    "viewer",
    "viewerLocalBalance",
    "items",
    "profileLegacyUser",
    "itemPriorityStatuses",
    "item",
    "itemById",
    "deal",
    "deals",
    "userChats",
    "validateItemData",
}


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
            if "graphql" not in url.lower():
                if request.method in ("POST", "PUT"):
                    log.append(
                        {
                            "kind": "upload",
                            "method": request.method,
                            "url": url[:200],
                            "post": (request.post_data or "")[:800],
                        }
                    )
                await route.continue_()
                return

            name = ""
            if "operationName=" in url:
                values = parse_qs(urlparse(url).query).get("operationName") or []
                name = values[0] if values else ""
            else:
                try:
                    name = str(
                        (
                            json.loads(request.post_data or "{}")
                        ).get("operationName")
                        or ""
                    )
                except Exception:  # noqa: BLE001
                    name = ""

            entry = {
                "kind": "graphql",
                "method": request.method,
                "op": name,
                "url": url[:1500],
                "post": (request.post_data or "")[:4000],
            }
            log.append(entry)

            if name and name not in READ_ONLY:
                print(f"BLOCKED operation: {name}")
                await route.abort()
                return
            await route.continue_()

        await page.route("**/*", route_handler)
        await page.goto(LOT, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(7000)
        try:
            ok = page.get_by_role("button", name="ОК", exact=True)
            if await ok.count():
                await ok.first.click()
                await page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass

        body = await page.evaluate("() => document.body.innerText")
        print("page has 'Снят с продажи':", "Снят с продажи" in body)

        button = page.get_by_role("button", name="Выставить повторно")
        print("button count:", await button.count())
        if await button.count():
            await button.first.click()
            await page.wait_for_timeout(12000)

        print("=== log ===")
        for item in log:
            if item["kind"] == "graphql":
                print(
                    item["method"],
                    item["op"],
                    item["url"][:400],
                )
                if item["post"]:
                    print("   POST:", item["post"][:800])
            else:
                print(item["kind"], item["method"], item["url"][:150])

        OUT.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
