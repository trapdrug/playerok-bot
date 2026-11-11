from __future__ import annotations

import pytest

from app.security.passwords import verify_password
from app.services.audit import AuditService
from app.services.auth import (
    LOCK_AFTER_ATTEMPTS,
    AuthError,
    AuthService,
)


@pytest.mark.asyncio
async def test_main_admin_and_accounts(db, config):
    service = AuthService(db, config, AuditService(db))

    admin = await service.ensure_main_admin()
    assert admin.is_main_admin
    assert admin.must_change_password

    again = await service.ensure_main_admin()
    assert again.id == admin.id

    created = await service.create_account(admin, "seller1", "password123")
    assert created.username == "seller1"
    assert created.role == "user"

    with pytest.raises(AuthError) as exc:
        await service.create_account(created, "seller2", "password123")
    assert exc.value.code == "forbidden"

    result = await service.authenticate("seller1", "password123", 555)
    assert result.user.telegram_id == 555
    assert result.must_change_password is True

    await service.change_password(
        result.user, "", "newpassword123", forced=True
    )
    fresh = await service.get_by_id(result.user.id)
    assert fresh is not None
    assert fresh.must_change_password is False
    assert not verify_password("password123", fresh.password_hash)
    assert verify_password("newpassword123", fresh.password_hash)


@pytest.mark.asyncio
async def test_lockout_after_failures(db, config):
    service = AuthService(db, config, AuditService(db))
    await service.ensure_main_admin()

    for _ in range(LOCK_AFTER_ATTEMPTS - 1):
        with pytest.raises(AuthError) as exc:
            await service.authenticate("gladmin", "wrong", 1)
        assert exc.value.code == "invalid_credentials"

    with pytest.raises(AuthError) as exc:
        await service.authenticate("gladmin", "wrong", 1)
    assert exc.value.code == "locked"

    with pytest.raises(AuthError) as exc:
        await service.authenticate(
            "gladmin",
            config.admin_initial_password.get_secret_value(),
            1,
        )
    assert exc.value.code == "locked"


@pytest.mark.asyncio
async def test_account_enable_disable_delete(db, config):
    service = AuthService(db, config, AuditService(db))
    admin = await service.ensure_main_admin()
    user = await service.create_account(admin, "seller1", "password123")

    disabled = await service.set_active(admin, user.id, False)
    assert disabled.is_active is False

    with pytest.raises(AuthError) as exc:
        await service.authenticate("seller1", "password123", 42)
    assert exc.value.code == "inactive"

    enabled = await service.set_active(admin, user.id, True)
    assert enabled.is_active is True

    with pytest.raises(AuthError) as exc:
        await service.set_active(admin, admin.id, False)
    assert exc.value.code == "forbidden"

    await service.delete_account(admin, user.id)
    assert await service.get_by_id(user.id) is None
