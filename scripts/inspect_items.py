"""Capture the Playerok "my items" GraphQL operation through the saved profile.

Read-only: the script only opens pages and stores the captured requests.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
URLS = [
    "https://playerok.com/profile/Kolakussia/products",
    "https://playerok.com/profile/Kolakussia",
    "https://playerok.com/products",
]
OUT = Path("data/items_capture.json")


async def main() -> None:
    captures: list[dict] = []
    responses: list[dict] = []

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
            url = request.url
            if "graphql" not in url.lower():
                return
            payload = None
            raw = request.post_data
            if raw:
                try:
                    payload = json.loads(raw)
                except Exception:  # noqa: BLE001
                    payload = {"raw": raw[:3000]}
            captures.append(
                {"url": url, "method": request.method, "payload": payload}
            )

        async def on_response(response) -> None:  # noqa: BLE001
            url = response.url
            if "graphql" not in url.lower():
                return
            try:
                body = await response.text()
            except Exception:  # noqa: BLE001
                body = ""
            responses.append(
                {"url": url, "status": response.status, "body": body[:30000]}
            )

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        for url in URLS:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:  # noqa: BLE001
                print("goto failed", url, exc)
                continue
            await page.wait_for_timeout(7000)
            try:
                title = await page.title()
                body = await page.locator("body").inner_text(timeout=5000)
                print(
                    f"URL: {page.url}\nTITLE: {title}\n"
                    f"BODY: {' '.join(body.split())[:600]}\n"
                )
            except Exception:  # noqa: BLE001
                pass

        await context.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {"requests": captures, "responses": responses},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("captured requests:", len(captures), "responses:", len(responses))
    for item in captures:
        payload = item.get("payload") or {}
        print("-", item["url"][:160], "op=", payload.get("operationName"))


if __name__ == "__main__":
    asyncio.run(main())
