from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.config import AppConfig
from app.db import Database


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        bot_token="123456789:AAExampleTokenReplaceMeWithYourOwnToken",
        admin_login="gladmin",
        admin_initial_password="1234",
        fernet_key=Fernet.generate_key().decode(),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        selectors_file=tmp_path / "selectors.yaml",
        dry_run=True,
        poll_interval_seconds=60,
    )


@pytest_asyncio.fixture
async def db(config: AppConfig):
    database = Database(config.database_url)
    await database.init()
    try:
        yield database
    finally:
        await database.dispose()
