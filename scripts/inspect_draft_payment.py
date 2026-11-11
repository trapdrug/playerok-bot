"""Inspect the payment window of the draft publishing flow (payment blocked)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/draft_payment_capture.json")
SLUG = "884c63247db5-jelly-bunny---lyubaya-model-bystraya-vydacha-lyuboy-fon"

BLOCK_MARKERS = (
    "createpayment",
    "createtransaction",
    "payfor",
    "paymenttransaction",
    "paytransaction",
)

DUMP_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const modals = [...document.querySelectorAll('.MuiModal-root, [role="dialog"]')]
    .filter(visible);
  const container = modals.length ? modals[modals.length - 1] : document.body;
  const switches = [...container.querySelectorAll('input[type="checkbox"], input[role="switch"]')]
    .map((el) => ({
      name: el.getAttribute('name') || '',
      value: el.getAttribute('value') || '',
      checked: el.checked,
      label: (el.closest('label') || {}).innerText
        ? (el.closest('label').innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 90)
        : '',
    }));
  const radios = [...container.querySelectorAll('input[type="radio"]')].map((el) => ({
    name: el.getAttribute('name') || '',
    value: el.getAttribute('value') || '',
    checked: el.checked,
  }));
  const buttons = [...container.querySelectorAll('button, [role="button"]')]
    .filter(visible)
    .map((el) => ({
      text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 70),
      disabled: el.disabled === true,
    }));
  return {
    modalCount: modals.length,
    switches,
    radios,
    buttons,
    text: (container.innerText || '').replace(/\\s+/g, ' ').slice(0, 1200),
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
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def route_handler(route, request) -> None:  # noqa: ANN001
            url = request.url
            if "graphql" in url.lower():
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
                if name and any(m in name.lower() for m in BLOCK_MARKERS):
                    log.append({"op": name, "post": (request.post_data or "")[:1200]})
                    print("BLOCKED:", name)
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
        try:
            ok = page.get_by_role("button", name="ОК", exact=True)
            if await ok.count():
                await ok.first.click()
                await page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass

        await page.get_by_role("button", name="Выставить", exact=True).first.click()
        await page.wait_for_timeout(6000)
        promo = await page.evaluate(DUMP_JS)
        print("=== promo modal ===")
        print("switches:", json.dumps(promo["switches"], ensure_ascii=False))
        print("radios:", json.dumps(promo["radios"], ensure_ascii=False))
        print("buttons:", json.dumps(promo["buttons"], ensure_ascii=False))
        print("text:", promo["text"][:600])

        confirm = page.get_by_role("button", name="Выставить Премиум", exact=False)
        print("confirm count:", await confirm.count())
        if await confirm.count():
            await confirm.first.click()
            await page.wait_for_timeout(8000)

        payment = await page.evaluate(DUMP_JS)
        print("=== payment modal ===")
        print("switches:", json.dumps(payment["switches"], ensure_ascii=False))
        print("radios:", json.dumps(payment["radios"], ensure_ascii=False))
        print("buttons:", json.dumps(payment["buttons"], ensure_ascii=False))
        print("text:", payment["text"][:900])

        OUT.write_text(
            json.dumps({"promo": promo, "payment": payment}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
