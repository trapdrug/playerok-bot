"""Explore the Playerok "create listing" form (read-only, mutations blocked)."""

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
  const buttons = [...document.querySelectorAll('button, [role="button"]')]
    .filter(visible)
    .map((el) => ({
      text: (el.innerText || el.textContent || '').trim().slice(0, 60),
      testid: el.getAttribute('data-testid') || '',
      cls: (el.className || '').toString().slice(0, 50),
    }))
    .filter((b) => b.text);
  const labels = [...document.querySelectorAll('label, [class*="label"], span')]
    .filter(visible)
    .map((el) => (el.innerText || '').trim())
    .filter((t) => t && t.length < 40)
    .slice(0, 40);
  return {
    fields,
    buttons,
    labels,
    body: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 1500),
  };
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
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def route_handler(route, request) -> None:  # noqa: ANN001
            url = request.url
            if "graphql" in url.lower():
                from urllib.parse import parse_qs, urlparse

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
                        "method": request.method,
                        "op": name,
                        "url": url[:220],
                        "post": (request.post_data or "")[:1500],
                    }
                )
                # Block mutations only (Apollo sends them as POST with a query).
                if request.method == "POST" and "mutation" in (
                    request.post_data or ""
                )[:400].lower():
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", route_handler)
        await page.goto(
            "https://playerok.com/profile/Kolakussia/products",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(4000)
        try:
            ok = page.get_by_role("button", name="ОК", exact=True)
            if await ok.count():
                await ok.first.click()
                await page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass

        print("=== header buttons ===")
        header = await page.evaluate(DUMP_JS)
        print(json.dumps(header["buttons"][:20], ensure_ascii=False))

        # Click "Продать" in the header.
        sell = page.get_by_text("Продать", exact=True)
        print("sell entries:", await sell.count())
        if await sell.count():
            await sell.first.click()
            await page.wait_for_timeout(12000)
            print("URL after click:", page.url)

        seller = await page.evaluate(DUMP_JS)
        print("=== seller page buttons ===")
        print(json.dumps(seller["buttons"], ensure_ascii=False)[:1500])
        print("=== seller body ===")
        print(seller["body"][:900])

        for label in ("Создать товар", "Новый товар", "Добавить товар", "Разместить"):
            button = page.get_by_text(label, exact=False)
            if await button.count():
                print(f"clicking {label!r}")
                await button.first.click()
                await page.wait_for_timeout(9000)
                print("URL:", page.url)
                break

        form = await page.evaluate(DUMP_JS)
        print("=== fields ===")
        print(json.dumps(form["fields"], ensure_ascii=False, indent=1)[:3000])
        print("=== buttons ===")
        print(json.dumps(form["buttons"], ensure_ascii=False)[:2000])
        print("=== labels ===")
        print(json.dumps(form["labels"], ensure_ascii=False)[:1200])
        print("=== body ===")
        print(form["body"][:1200])

        print("=== graphql log ===")
        for item in log:
            print(item["method"], item["op"], item["post"][:200])

        OUT.write_text(
            json.dumps(
                {"form": form, "log": log}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
