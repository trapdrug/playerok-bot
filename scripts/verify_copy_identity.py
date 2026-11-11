"""Compare the created copy with the original sold listing."""

from __future__ import annotations

import asyncio
import hashlib
import sys

from app.config import AppConfig
from app.db import Database
from app.playerok.client import PlayerokClient
from app.playerok.selectors import Selectors
from app.security.crypto import CookieCipher
from app.services.audit import AuditService
from app.services.cookies import CookieService

SOURCE = "2264e9898db6-jelly-bunny---lyubaya-model-bystraya-vydacha-lyuboy-fon"
COPY = "884c63247db5-jelly-bunny---lyubaya-model-bystraya-vydacha-lyuboy-fon"


async def main() -> None:
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    cookies_service = CookieService(db, config, cipher, audit)
    client = PlayerokClient(config, Selectors(config.selectors_path))
    cookies = await cookies_service.get_cookies()

    async with client.page_session(cookies) as page:
        await client.check_session(page)
        source = await client.fetch_item(page, SOURCE)
        copy = await client.fetch_item(page, COPY)

        print("name equal:      ", source.name == copy.name)
        print("price equal:     ", source.price == copy.price, source.price, copy.price)
        print("description eq:  ", source.description == copy.description)
        print("attributes eq:   ", source.attributes == copy.attributes, copy.attributes)
        print("data fields eq:  ", source.data_fields == copy.data_fields)
        print("category eq:     ", source.game_category_id == copy.game_category_id)
        print("obtaining eq:    ", source.obtaining_type_id == copy.obtaining_type_id)
        print("copy priority:   ", copy.priority)
        print("copy status:     ", copy.status)

        async def digest(url: str) -> str | None:
            try:
                response = await page.context.request.get(url)
                if not response.ok:
                    return None
                return hashlib.sha256(await response.body()).hexdigest()
            except Exception as exc:  # noqa: BLE001
                print("download failed:", exc)
                return None

        src_url = (source.attachment_urls or [None])[0]
        copy_url = (copy.attachment_urls or [None])[0]
        print("source image:    ", src_url[-60:] if src_url else None)
        print("copy image:      ", copy_url[-60:] if copy_url else None)
        src_hash = await digest(src_url) if src_url else None
        copy_hash = await digest(copy_url) if copy_url else None
        print("source hash:", src_hash)
        print("copy hash:  ", copy_hash)
        print("image bytes eq:  ", bool(src_hash and copy_hash and src_hash == copy_hash))

        balance = await page.evaluate(
            """
            async () => {
              const params = new URLSearchParams({
                operationName: 'viewerLocalBalance',
                variables: '{}',
                extensions: JSON.stringify({
                  persistedQuery: {
                    version: 1,
                    sha256Hash: '836ba5f37fd9377acacdfe93b879566f87791f8119704ee6844382af69248d48'
                  }
                }),
              });
              const response = await fetch('/graphql?' + params.toString(), {
                credentials: 'include',
                headers: {'x-apollo-operation-name': 'viewerLocalBalance'},
              });
              return await response.json();
            }
            """
        )
        print("balance:", balance)

    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
