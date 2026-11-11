"""List all Playerok items of the seller with status/priority (read-only)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OPERATION = "items"
SHA256 = "3f20c731f8f769a094ee3fa32e09f8e12250357e9a4f0ebb4e6988e7a0bb9260"
USER_ID = "1f01e88d-ae8a-62b0-e266-9389c8f72aec"

FETCH_JS = """
async ({variables, sha256Hash}) => {
  const params = new URLSearchParams({
    operationName: 'items',
    variables: JSON.stringify(variables),
    extensions: JSON.stringify({persistedQuery: {version: 1, sha256Hash}}),
  });
  const response = await fetch('/graphql?' + params.toString(), {
    method: 'GET',
    credentials: 'include',
    headers: {
      'x-apollo-operation-name': 'items',
      'apollo-require-preflight': 'true',
      'apollographql-client-name': 'web',
      'x-gql-op': 'items',
      'x-gql-path': '/profile/[username]/products',
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
            "https://playerok.com/profile/Kolakussia/products",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(4000)

        after = None
        total = None
        rows = []
        for _ in range(5):
            variables = {
                "pagination": {"first": 16, **({"after": after} if after else {})},
                "filter": {
                    "userId": USER_ID,
                    "status": [
                        "APPROVED",
                        "PENDING_MODERATION",
                        "PENDING_APPROVAL",
                    ],
                    "withOfficial": False,
                },
                "showForbiddenImage": True,
            }
            result = await page.evaluate(
                FETCH_JS, {"variables": variables, "sha256Hash": SHA256}
            )
            items = (result.get("data") or {}).get("items") or {}
            if "errors" in result:
                print("ERROR:", json.dumps(result["errors"], ensure_ascii=False)[:300])
                break
            total = items.get("totalCount")
            edges = items.get("edges") or []
            for edge in edges:
                node = edge.get("node") or {}
                rows.append(node)
            page_info = items.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")

        print("totalCount:", total, "loaded:", len(rows))
        from collections import Counter

        print("statuses:", Counter(r.get("status") for r in rows))
        print("priority:", Counter(r.get("priority") for r in rows))
        for node in rows:
            print(
                f"  {node.get('status'):10} {str(node.get('priority')):10} "
                f"{node.get('price'):>6} {node.get('name')[:45]:47} "
                f"https://playerok.com/products/{node.get('slug')}"
            )
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
