"""Verify the relist and premium flows on Playerok without side effects.

All POST /graphql requests (mutations) are aborted, so nothing is published
and nothing is paid. The script only reads the UI and the mutation payloads
the site tries to send.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/flow_capture.json")

SOLD_LOT = (
    "https://playerok.com/products/0c85e02c7bae-snoop-dogg---"
    "lyubaya-model-bystraya-vydacha-lyuboy-fon"
)
ACTIVE_LOT = (
    "https://playerok.com/products/8a2aa1dbe4a2-doplata-podarka"
)

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
    .map((el) => ({
      text: (el.innerText || el.textContent || '').trim().slice(0, 60),
      aria: el.getAttribute('aria-label') || '',
    }))
    .filter((b) => b.text || b.aria);
  const inputs = [...document.querySelectorAll('input, textarea')]
    .filter(visible)
    .map((el) => ({
      tag: el.tagName,
      name: el.getAttribute('name') || '',
      type: el.getAttribute('type') || '',
      placeholder: el.getAttribute('placeholder') || '',
      value: (el.value || '').slice(0, 300),
    }));
  const radios = [...document.querySelectorAll('input[type="radio"]')].map((el) => {
    const label = el.closest('label');
    return {
      name: el.getAttribute('name') || '',
      value: el.getAttribute('value') || '',
      checked: el.checked,
      label: label ? (label.innerText || '').trim().slice(0, 80) : '',
    };
  });
  const dialogs = [...document.querySelectorAll('[role="dialog"], .MuiDialog-root, [class*="modal"], [role="menu"], [class*="popover"], [class*="tooltip"]')]
    .filter(visible)
    .map((el) => (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 2500));
  const interesting = [...document.querySelectorAll('*')]
    .filter((el) => {
      if (!visible(el)) return false;
      if (el.children.length > 0) return false;
      const t = (el.textContent || '').trim();
      return t.length > 0 && t.length < 120 &&
        /₽|баланс|премиум|поднять|оплат|выставить|продвинуть|топ/i.test(t);
    })
    .map((el) => ({
      tag: el.tagName,
      text: (el.textContent || '').trim().slice(0, 120),
      cls: (el.className || '').toString().slice(0, 60),
    }))
    .slice(0, 60);
  const images = [...document.querySelectorAll('img')].filter(visible).length;
  return {
    buttons,
    inputs,
    radios,
    dialogs,
    images,
    interesting,
    body: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 4000),
  };
}
"""


async def run_flow(context, page, url: str, button_text: str, label: str) -> dict:
    captured: list[dict] = []

BLOCK_MARKERS = (
    "pay",
    "boost",
    "priority",
    "premium",
    "payment",
    "publish",
    "createitem",
    "activate",
)


def is_blocked(operation_name: str, raw: str) -> bool:
    name = (operation_name or "").lower()
    if any(marker in name for marker in BLOCK_MARKERS):
        return True
    return False


async def run_flow(context, page, url: str, button_text: str, label: str) -> dict:
    captured: list[dict] = []

    async def route_handler(route, request) -> None:  # noqa: ANN001
        if "graphql" in request.url.lower():
            raw = request.post_data or ""
            operation_name = ""
            if "operationName=" in request.url:
                from urllib.parse import parse_qs, urlparse

                query = parse_qs(urlparse(request.url).query)
                values = query.get("operationName") or []
                operation_name = values[0] if values else ""
            else:
                try:
                    payload = json.loads(raw)
                    operation_name = str(payload.get("operationName") or "")
                except Exception:  # noqa: BLE001
                    operation_name = ""
            if is_blocked(operation_name, raw):
                captured.append(
                    {
                        "url": request.url[:200],
                        "operation": operation_name,
                        "post_data": raw[:4000],
                    }
                )
                await route.abort()
                return
        await route.continue_()

    await page.route("**/*", route_handler)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # Dismiss the cookie banner if it shows up.
        try:
            ok = page.get_by_role("button", name="ОК", exact=True)
            if await ok.count():
                await ok.first.click()
                await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(2000)

        before = await page.evaluate(DUMP_JS)
        print("=" * 70)
        print(f"[{label}] before click")
        print("buttons:", json.dumps(before["buttons"], ensure_ascii=False)[:800])

        target = page.get_by_role("button", name=button_text, exact=False)
        count = await target.count()
        print(f"[{label}] button {button_text!r} found: {count}")
        if count:
            await target.first.click()
            await page.wait_for_timeout(6000)

        after = await page.evaluate(DUMP_JS)
        print(f"[{label}] after click")
        print("dialogs:", json.dumps(after["dialogs"], ensure_ascii=False)[:2500])
        print("interesting:", json.dumps(after["interesting"], ensure_ascii=False)[:2500])
        print("buttons:", json.dumps(after["buttons"], ensure_ascii=False)[:1200])
        print("inputs:", json.dumps(after["inputs"], ensure_ascii=False)[:2000])
        print("radios:", json.dumps(after["radios"], ensure_ascii=False)[:1200])
        print("body:", after["body"][:2500])
        print("mutations attempted:", len(captured))
        for item in captured:
            print("  ->", item.get("operation"), item["post_data"][:800])

        # Try to open the premium option inside the promotion menu.
        for option_text in ("Премиум", "Premium"):
            option = page.get_by_text(option_text, exact=True)
            if await option.count():
                print(f"[{label}] clicking option {option_text!r}")
                await option.first.click()
                await page.wait_for_timeout(5000)
                modal = await page.evaluate(DUMP_JS)
                print(f"[{label}] after option click")
                print("dialogs:", json.dumps(modal["dialogs"], ensure_ascii=False)[:2500])
                print("interesting:", json.dumps(modal["interesting"], ensure_ascii=False)[:2500])
                print("buttons:", json.dumps(modal["buttons"], ensure_ascii=False)[:1200])
                print("radios:", json.dumps(modal["radios"], ensure_ascii=False)[:1200])
                print("body:", modal["body"][:2500])
                after = modal
                break

        return {"before": before, "after": after, "mutations": captured}
    finally:
        await page.unroute("**/*", route_handler)


async def main() -> None:
    result: dict = {}
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        result["relist"] = await run_flow(
            context, page, SOLD_LOT, "Выставить повторно", "RELIST"
        )
        result["boost"] = await run_flow(
            context, page, ACTIVE_LOT, "Повысить статус", "BOOST"
        )
        await context.close()

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
