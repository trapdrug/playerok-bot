from __future__ import annotations

import pytest

from app.services.audit import AuditService
from app.services.auth import AuthError, AuthService
from app.services.notifications import NotificationService
from app.services.settings import SettingsService


def _services(db, config):
    audit = AuditService(db)
    auth = AuthService(db, config, audit)
    settings = SettingsService(db, config, audit)
    notifications = NotificationService(bot=None, db=db, settings_service=settings)
    return auth, settings, notifications


@pytest.mark.asyncio
async def test_worker_account_has_notifications_only(db, config):
    auth, _, _ = _services(db, config)
    admin = await auth.ensure_main_admin()

    worker = await auth.create_account(
        admin, "worker1", "password123", role="worker"
    )
    assert worker.role == "worker"
    assert worker.is_worker is True
    assert worker.can_manage_automation is False
    assert worker.notifications_enabled is True

    ordinary = await auth.create_account(admin, "seller1", "password123")
    assert ordinary.role == "user"
    assert ordinary.can_manage_automation is True

    with pytest.raises(AuthError) as exc:
        await auth.create_account(admin, "bad", "password123", role="root")
    assert exc.value.code == "validation"

    # Workers cannot create accounts.
    with pytest.raises(AuthError) as exc:
        await auth.create_account(worker, "another", "password123")
    assert exc.value.code == "forbidden"


@pytest.mark.asyncio
async def test_notifications_are_personal(db, config):
    auth, _, notifications = _services(db, config)
    admin = await auth.ensure_main_admin()
    admin_login = await auth.authenticate(
        "gladmin", config.admin_initial_password.get_secret_value(), 111
    )

    worker = await auth.create_account(
        admin, "worker1", "password123", role="worker"
    )
    worker_login = await auth.authenticate("worker1", "password123", 222)

    assert set(await notifications._recipients()) == {111, 222}

    # Switching notifications off for the worker affects only the worker.
    updated = await auth.set_notifications(worker_login.user, False)
    assert updated.notifications_enabled is False
    assert set(await notifications._recipients()) == {111}
    assert set(await notifications._recipients(critical=True)) == {111}

    # Switching notifications off for the main admin keeps the admin reachable
    # for critical events (expired session, captcha, 429).
    await auth.set_notifications(admin_login.user, False)
    assert await notifications._recipients() == []
    assert set(await notifications._recipients(critical=True)) == {111}

    # The worker stays out of critical notifications while disabled.
    await auth.set_notifications(worker_login.user, True)
    await auth.set_notifications(admin_login.user, True)
    assert set(await notifications._recipients()) == {111, 222}
    assert set(await notifications._recipients(critical=True)) == {111, 222}
