"""Import Playerok cookies into the bot database (runs inside the container).

Usage: python /data/import_cookies.py /data/cookies.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import User  # noqa: E402
from app.security.crypto import CookieCipher  # noqa: E402
from app.services.audit import AuditService  # noqa: E402
from app.services.cookies import CookieService  # noqa: E402


async def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/cookies.json")
    payload = path.read_bytes()
    print("файл:", path, "байт:", len(payload))

    config = AppConfig()
    db = Database(config.database_url)
    await db.init()

    audit = AuditService(db)
    cipher = CookieCipher(config.fernet_key.get_secret_value())
    service = CookieService(db, config, cipher, audit)

    async with db.session() as session:
        admin = await session.scalar(
            select(User).where(User.role == "main_admin").limit(1)
        )
        if admin is None:
            admin = await session.scalar(select(User).limit(1))
    if admin is None:
        print("в базе нет ни одного аккаунта")
        return

    count = await service.save_from_bytes(payload, admin)
    print("cookies сохранено:", count)

    stored = await service.get_cookies()
    print("читается из базы:", len(stored or []), "шт.")
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
