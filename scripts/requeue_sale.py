"""Put a sale back into the queue so the bot processes it again."""

from __future__ import annotations

import asyncio
import sys

from app.config import AppConfig
from app.db import Database
from sqlalchemy import select

from app.models import Sale


async def main() -> None:
    external_id = sys.argv[1]
    config = AppConfig()
    db = Database(config.database_url)
    await db.init()
    async with db.session() as session:
        row = await session.scalar(
            select(Sale).where(Sale.external_id == external_id)
        )
        if row is None:
            print("not found")
        else:
            row.status = "detected"
            row.relisted_at = None
            row.relist_attempts = 0
            row.error = None
            row.lot_url = None
            print("reset:", row.external_id, row.title)
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
