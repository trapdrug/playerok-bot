"""Capture the exact headers Playerok sends for the items GraphQL query."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
URL = "https://playerok.com/profile/Kolakussia/products"
OUT = Path("data/items_headers.json")


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

        async def on_request(request) -> None:  # noqa: ANN001
            url = request.url
            if "operationName=items" not in url:
                return
            headers = await request.all_headers()
            print("METHOD:", request.method)
            print("URL:", url[:400])
            print("HEADERS:", json.dumps(headers, ensure_ascii=False, indent=2))
            OUT.write_text(
                json.dumps(
                    {"method": request.method, "url": url, "headers": headers},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        page.on("request", on_request)
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(9000)
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
