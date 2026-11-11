"""Count Playerok items that are currently on sale (read-only)."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OPERATION = "items"
SHA256 = "3f20c731f8f769a094ee3fa32e09f8e12250357e9a4f0ebb4e6988e7a0bb9260"
USER_ID = "1f01e88d-ae8a-62b0-e266-9389c8f72aec"

GET_JS = """
async ({operationName, variables, sha256Hash}) => {
  const params = new URLSearchParams({
    operationName,
    variables: JSON.stringify(variables),
    extensions: JSON.stringify({persistedQuery: {version: 1, sha256Hash}}),
  });
  const response = await fetch('/graphql?' + params.toString(), {
    method: 'GET',
    credentials: 'include',
    headers: {
      'x-apollo-operation-name': operationName,
      'apollo-require-preflight': 'true',
      'apollographql-client-name': 'web',
      'x-gql-op': operationName,
      'x-gql-path': '/profile/[username]/products',
      'x-timezone-offset': '-180',
    },
  });
  return await response.json();
}
"""

PERSISTED_JS = """
async ({operationName, variables, sha256Hash}) => {
  const response = await fetch('/graphql', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'content-type': 'application/json',
      'apollo-require-preflight': 'true',
      'x-apollo-operation-name': operationName,
    },
    body: JSON.stringify({
      operationName,
      variables,
      extensions: {persistedQuery: {version: 1, sha256Hash}},
    }),
  });
  return await response.json();
}
"""

VARIANTS = {
    "all_active": {
        "pagination": {"first": 50},
        "filter": {
            "userId": USER_ID,
            "status": ["APPROVED", "PENDING_MODERATION", "PENDING_APPROVAL"],
            "withOfficial": False,
        },
        "showForbiddenImage": True,
    },
    "approved_only": {
        "pagination": {"first": 50},
        "filter": {"userId": USER_ID, "status": ["APPROVED"]},
    },
    "no_status_filter": {
        "pagination": {"first": 50},
        "filter": {"userId": USER_ID},
    },
}


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

        for name, variables in VARIANTS.items():
            for transport, js in (("get", GET_JS), ("post", PERSISTED_JS)):
                result = await page.evaluate(
                    js,
                    {
                        "operationName": OPERATION,
                        "variables": variables,
                        "sha256Hash": SHA256,
                    },
                )
                items = (result.get("data") or {}).get("items") or {}
                edges = items.get("edges") or []
                nodes = [edge.get("node") or {} for edge in edges]
                print(
                    f"[{name}/{transport}] totalCount={items.get('totalCount')} "
                    f"loaded={len(nodes)} "
                    f"statuses={dict(Counter(n.get('status') for n in nodes))} "
                    f"priority={dict(Counter(n.get('priority') for n in nodes))}"
                )
                if not nodes:
                    print(
                        "   raw:",
                        json.dumps(result, ensure_ascii=False)[:300],
                    )
                if name == "all_active" and nodes and transport == "get":
                    for node in nodes:
                        print(
                            " -",
                            node.get("status"),
                            node.get("priority"),
                            node.get("price"),
                            (node.get("name") or "")[:50],
                            f"https://playerok.com/products/{node.get('slug')}",
                        )

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
