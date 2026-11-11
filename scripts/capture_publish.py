"""Capture what Playerok sends when a draft listing is published."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/publish_capture.json")
SLUG = "884c63247db5-jelly-bunny---lyubaya-model-bystraya-vydacha-lyuboy-fon"

DUMP_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const buttons = [...document.querySelectorAll('button, [role="button"]')]
    .filter(visible)
    .map((el) => (el.innerText || el.textContent || '').trim().slice(0, 60))
    .filter(Boolean);
  const modals = [...document.querySelectorAll('.MuiModal-root, [role="dialog"]')]
    .filter(visible)
    .map((el) => (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 800));
  return {buttons, modals, body: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 700)};
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
            url = request.url
            method = request.method
            if method in ("POST", "PUT") and (
                "graphql" in url.lower() or "rest-api" in url.lower()
            ):
                name = ""
                if "operationName=" in url:
                    values = (
                        parse_qs(urlparse(url).query).get("operationName") or []
                    )
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
                log.append(
                    {
                        "method": method,
                        "op": name,
                        "url": url[:400],
                        "post": (request.post_data or "")[:1500],
                    }
                )
            await route.continue_()

        await page.route("**/*", route_handler)
        await page.goto(
            f"https://playerok.com/products/{SLUG}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(7000)
        try:
            ok = page.get_by_role("button", name="ОК", exact=True)
            if await ok.count():
                await ok.first.click()
                await page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass

        before = await page.evaluate(DUMP_JS)
        print("before:", before["buttons"], before["body"][:200])

        publish = page.get_by_role("button", name="Выставить", exact=True)
        print("publish button:", await publish.count())
        if await publish.count():
            await publish.first.click()
            await page.wait_for_timeout(6000)
            after = await page.evaluate(DUMP_JS)
            print("after buttons:", after["buttons"])
            print("after modals:", after["modals"])
            print("after body:", after["body"][:600])

            # Confirm the publication if a modal asks for it.
            for label in ("Выставить", "Опубликовать", "Подтвердить", "Да"):
                confirm = page.get_by_role("button", name=label, exact=True)
                if await confirm.count():
                    print("confirming with", label)
                    await confirm.first.click()
                    await page.wait_for_timeout(8000)
                    break
            final = await page.evaluate(DUMP_JS)
            print("final buttons:", final["buttons"])
            print("final body:", final["body"][:400])

        print("=== requests ===")
        for item in log:
            print(item["method"], item["op"], item["url"][:220])
            if item["post"]:
                print("   ", item["post"][:400])

        OUT.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
