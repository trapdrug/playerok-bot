"""Dump the promotion menu structure (services, price, pay button)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
LOT = "https://playerok.com/products/8a2aa1dbe4a2-doplata-podarka"

DUMP_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const candidates = [...document.querySelectorAll('div, section, aside, ul, form')]
    .filter((el) => visible(el) && (el.innerText || '').includes('Выберите сервис'))
    .map((el) => ({el, len: (el.innerText || '').length}))
    .sort((a, b) => a.len - b.len);
  const container = candidates.length ? candidates[0].el : null;
  const html = container ? container.outerHTML.slice(0, 14000) : '';
  const text = container ? (container.innerText || '').replace(/\\s+/g, ' ').slice(0, 2000) : '';
  const buttons = container
    ? [...container.querySelectorAll('button, [role="button"], [role="radio"], input, label')]
        .filter(visible)
        .map((el) => ({
          tag: el.tagName,
          text: (el.innerText || el.textContent || el.value || '').trim().slice(0, 80),
          role: el.getAttribute('role') || '',
          cls: (el.className || '').toString().slice(0, 70),
          checked: el.checked === true,
          type: el.getAttribute('type') || '',
        }))
    : [];
  const pageButtons = [...document.querySelectorAll('button, [role="button"], [role="radio"]')]
    .filter(visible)
    .map((el) => (el.innerText || el.textContent || '').trim().slice(0, 60))
    .filter(Boolean);
  return {html, text, buttons, pageButtons, found: Boolean(container)};
}
"""


async def main() -> None:
    blocked: list[dict] = []

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
            url = request.url.lower()
            if "graphql" in url:
                name = ""
                if "operationname=" in url:
                    from urllib.parse import parse_qs, urlparse

                    values = parse_qs(urlparse(request.url).query).get(
                        "operationName"
                    ) or []
                    name = values[0] if values else ""
                else:
                    try:
                        name = str(
                            (json.loads(request.post_data or "{}")).get(
                                "operationName"
                            )
                            or ""
                        )
                    except Exception:  # noqa: BLE001
                        name = ""
                if any(
                    marker in name.lower()
                    for marker in ("pay", "boost", "payment", "publish", "create")
                ):
                    blocked.append(
                        {
                            "op": name,
                            "data": (request.post_data or "")[:2000],
                            "url": request.url[:200],
                        }
                    )
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", route_handler)
        await page.goto(LOT, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        try:
            ok = page.get_by_role("button", name="ОК", exact=True)
            if await ok.count():
                await ok.first.click()
                await page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass

        await page.get_by_role("button", name="Повысить статус").first.click()
        await page.wait_for_timeout(5000)

        data = await page.evaluate(DUMP_JS)
        print("found marker:", data["found"])
        print("container text:", data["text"][:2000])
        print("container buttons:", json.dumps(data["buttons"], ensure_ascii=False)[:2000])
        print("page buttons:", json.dumps(data["pageButtons"], ensure_ascii=False)[:1500])
        print("HTML:", data["html"][:7000])
        print("blocked requests:", json.dumps(blocked, ensure_ascii=False)[:1500])

        Path("data/promo_menu.html").write_text(data["html"], encoding="utf-8")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
