from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import PendingBoost
from app.services.audit import AuditService
from app.services.settings import SettingsService


@pytest.mark.asyncio
async def test_settings_lifecycle(db, config):
    service = SettingsService(db, config, AuditService(db))

    settings = await service.get()
    assert settings.dry_run is True
    assert settings.auto_relist_enabled is False
    assert settings.auto_boost_enabled is False

    updated, value = await service.toggle("auto_relist_enabled")
    assert value is True
    assert updated.auto_relist_enabled is True

    updated, disabled = await service.set_daily_limit(Decimal("500"))
    assert disabled is False
    assert Decimal(updated.daily_boost_limit) == Decimal("500.00")

    assert await service.can_spend(Decimal("100")) is True
    await service.record_boost_spend(Decimal("100"))
    assert await service.get_today_spend() == Decimal("100.00")
    assert await service.remaining_boost_budget() == Decimal("400.00")
    assert await service.can_spend(Decimal("500")) is False

    await service.set_feature("auto_boost_enabled", True)
    stopped = await service.emergency_stop("test")
    assert stopped.stopped is True
    assert stopped.auto_relist_enabled is False
    assert stopped.auto_boost_enabled is False
    assert stopped.stop_reason == "test"

    resumed = await service.resume()
    assert resumed.stopped is False
    assert resumed.stop_reason is None


@pytest.mark.asyncio
async def test_disable_boost_cancels_pending(db, config):
    service = SettingsService(db, config, AuditService(db))

    async with db.session() as session:
        session.add(
            PendingBoost(
                sale_external_id="s1",
                lot_url="https://playerok.com/products/x",
                status="pending",
            )
        )

    await service.set_feature("auto_boost_enabled", True)
    await service.set_feature("auto_boost_enabled", False)

    async with db.session() as session:
        row = await session.scalar(
            select(PendingBoost).where(
                PendingBoost.sale_external_id == "s1"
            )
        )
    assert row is not None
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_zero_limit_disables_boost(db, config):
    service = SettingsService(db, config, AuditService(db))
    await service.set_feature("auto_boost_enabled", True)

    _, disabled = await service.set_daily_limit(Decimal("0"))
    assert disabled is True

    settings = await service.get()
    assert settings.auto_boost_enabled is False
    assert await service.can_spend(Decimal("1")) is False
