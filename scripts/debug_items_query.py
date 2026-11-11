"""Reproduce the exact Playerok items query to find why it returns FORBIDDEN."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


PROFILE_DIR = Path("data/export-profile")
OPERATION = "items"
SHA256 = "3f20c731f8f769a094ee3fa32e09f8e12250357e9a4f0ebb4e6988e7a0bb9260"

EXACT_VARIABLES = {
    "pagination": {"first": 16},
    "filter": {
        "userId": "1f01e88d-ae8a-62b0-e266-9389c8f72aec",
        "status": ["APPROVED", "PENDING_MODERATION", "PENDING_APPROVAL"],
        "withOfficial": False,
    },
    "showForbiddenImage": True,
}

FETCH_JS = """
async ({url, method, headers, body}) => {
  const options = {method, credentials: 'include', headers};
  if (body) options.body = body;
  const response = await fetch(url, options);
  const text = await response.text();
  return {status: response.status, text: text.slice(0, 200000)};
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
        await page.wait_for_timeout(6000)

        params = {
            "operationName": OPERATION,
            "variables": json.dumps(EXACT_VARIABLES, separators=(",", ":")),
            "extensions": json.dumps(
                {"persistedQuery": {"version": 1, "sha256Hash": SHA256}},
                separators=(",", ":"),
            ),
        }
        from urllib.parse import urlencode

        url = "/graphql?" + urlencode(params)

        site_headers = {
            "apollo-require-preflight": "true",
            "apollographql-client-name": "web",
            "x-gql-op": OPERATION,
            "x-gql-path": "/profile/[username]/products",
            "x-timezone-offset": "-180",
        }

        cases = [
            ("get exact + site headers", "GET", url, site_headers, None),
            (
                "get exact minimal headers",
                "GET",
                url,
                {"apollo-require-preflight": "true"},
                None,
            ),
            (
                "post exact + site headers",
                "POST",
                "/graphql",
                {**site_headers, "content-type": "application/json"},
                json.dumps(
                    {
                        "operationName": OPERATION,
                        "variables": EXACT_VARIABLES,
                        "extensions": {
                            "persistedQuery": {"version": 1, "sha256Hash": SHA256}
                        },
                    }
                ),
            ),
        ]

        for name, method, target, headers, body in cases:
            result = await page.evaluate(
                FETCH_JS,
                {"url": target, "method": method, "headers": headers, "body": body},
            )
            print(f"--- {name}: HTTP {result['status']}")
            print(result["text"][:200])
            print()

        # Which variable changes are accepted?
        variants = {
            "first=32": {**EXACT_VARIABLES, "pagination": {"first": 32}},
            "first=16 status=APPROVED": {
                **EXACT_VARIABLES,
                "filter": {
                    **EXACT_VARIABLES["filter"],
                    "status": ["APPROVED"],
                },
            },
            "first=16 no status filter": {
                "pagination": {"first": 16},
                "filter": {
                    "userId": EXACT_VARIABLES["filter"]["userId"],
                    "withOfficial": False,
                },
                "showForbiddenImage": True,
            },
            "first=16 status=MODERATED": {
                **EXACT_VARIABLES,
                "filter": {
                    **EXACT_VARIABLES["filter"],
                    "status": ["MODERATED"],
                },
            },
        }
        for name, variables in variants.items():
            variant_params = {
                "operationName": OPERATION,
                "variables": json.dumps(variables, separators=(",", ":")),
                "extensions": json.dumps(
                    {"persistedQuery": {"version": 1, "sha256Hash": SHA256}},
                    separators=(",", ":"),
                ),
            }
            from urllib.parse import urlencode

            variant_url = "/graphql?" + urlencode(variant_params)
            result = await page.evaluate(
                FETCH_JS,
                {
                    "url": variant_url,
                    "method": "GET",
                    "headers": site_headers,
                    "body": None,
                },
            )
            text = result["text"]
            total = None
            try:
                payload = json.loads(text)
                total = ((payload.get("data") or {}).get("items") or {}).get(
                    "totalCount"
                )
            except Exception:  # noqa: BLE001
                pass
            print(
                f"--- variant {name}: HTTP {result['status']} totalCount={total} "
                f"{text[:200]}"
            )

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
