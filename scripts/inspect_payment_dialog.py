"""Inspect the Playerok premium payment dialog (payment is blocked)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/payment_dialog_capture.json")
LOT = "https://playerok.com/products/8a2aa1dbe4a2-doplata-podarka"

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
  const radios = [...container.querySelectorAll('input[type="radio"]')].map((el) => {
    const label = el.closest('label');
    return {
      name: el.getAttribute('name') || '',
      value: el.getAttribute('value') || '',
      checked: el.checked,
      label: label ? (label.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120) : '',
    };
  });
  const buttons = [...container.querySelectorAll('button, [role="button"]')]
    .filter(visible)
    .map((el) => ({
      text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
      disabled: el.disabled === true,
      cls: (el.className || '').toString().slice(0, 50),
    }));
  const texts = [...container.querySelectorAll('span, p, div')]
    .filter((el) => visible(el) && el.children.length === 0)
    .map((el) => (el.textContent || '').trim())
    .filter((t) => t && /₽|баланс|карт|сбп|оплат|премиум/i.test(t))
    .slice(0, 40);
  return {
    modalCount: modals.length,
    radioCount: radios.length,
    radios,
    buttons,
    texts,
    text: (container.innerText || '').replace(/\\s+/g, ' ').slice(0, 2000),
    html: container.outerHTML.slice(0, 9000),
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
                    log.append({"op": name, "url": url[:300], "post": (request.post_data or "")[:1500]})
                    print("BLOCKED:", name)
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

        await page.get_by_role("button", name="Повысить статус").first.click()
        await page.wait_for_timeout(6000)
        step1 = await page.evaluate(DUMP_JS)
        print("=== after 'Повысить статус' ===")
        print("radios:", json.dumps(step1["radios"], ensure_ascii=False)[:1200])
        print("buttons:", json.dumps(step1["buttons"], ensure_ascii=False)[:800])
        print("text:", step1["text"][:900])

        # Click the footer action "Повысить до Премиум за 99 ₽".
        footer = page.get_by_text("Повысить до Премиум", exact=False)
        print("footer matches:", await footer.count())
        if await footer.count():
            await footer.first.click()
            await page.wait_for_timeout(8000)

        step2 = await page.evaluate(DUMP_JS)
        print("=== after 'Повысить до Премиум' ===")
        print("modalCount:", step2["modalCount"])
        print("radios:", json.dumps(step2["radios"], ensure_ascii=False)[:1500])
        print("buttons:", json.dumps(step2["buttons"], ensure_ascii=False)[:1200])
        print("texts:", json.dumps(step2["texts"], ensure_ascii=False)[:1500])
        print("text:", step2["text"][:2000])

        OUT.write_text(
            json.dumps({"step1": step1, "step2": step2, "log": log}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
