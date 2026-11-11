"""Dump the full GraphQL `item` payload of a sold lot (read-only)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OUT = Path("data/item_payload.json")

OPERATION = "item"
SHA256 = "48d2fe797afc4195cdbc1404b743711998e6f2a4b3b38db9a63bfb4eb8a9536d"
SLUG = "23c6cef827c2-easter-egg---lyubaya-model-bystraya-vydacha-lyuboy-fon"

FETCH_JS = """
async ({variables, sha256Hash}) => {
  const params = new URLSearchParams({
    operationName: 'item',
    variables: JSON.stringify(variables),
    extensions: JSON.stringify({persistedQuery: {version: 1, sha256Hash}}),
  });
  const response = await fetch('/graphql?' + params.toString(), {
    method: 'GET',
    credentials: 'include',
    headers: {
      'x-apollo-operation-name': 'item',
      'apollo-require-preflight': 'true',
      'apollographql-client-name': 'web',
    },
  });
  return await response.json();
}
"""


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
        await page.goto(
            f"https://playerok.com/products/{SLUG}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(5000)

        result = await page.evaluate(
            FETCH_JS,
            {
                "variables": {
                    "slug": SLUG,
                    "hasSupportAccess": False,
                    "showForbiddenImage": True,
                },
                "sha256Hash": SHA256,
            },
        )
        OUT.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        item = (result.get("data") or {}).get("item") or {}
        print("item keys:", list(item.keys()))
        for key in (
            "id",
            "slug",
            "name",
            "price",
            "rawPrice",
            "description",
            "attributes",
            "status",
            "priority",
            "sellerType",
            "comment",
            "dataFields",
            "attachments",
            "obtainingType",
            "category",
            "game",
            "dataMask",
        ):
            value = item.get(key)
            print(f"--- {key}:", json.dumps(value, ensure_ascii=False)[:1200])
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
