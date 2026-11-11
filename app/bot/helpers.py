from __future__ import annotations

import logging

from aiogram.types import CallbackQuery, Message

from app.models import User


logger = logging.getLogger(__name__)


LOGIN_HINT = "🔐 Сначала войдите: <code>/login логин пароль</code>"


async def safe_delete(message: Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 - deletion is best-effort
        pass


async def safe_delete_callback(callback: CallbackQuery) -> None:
    await safe_delete(callback.message)


async def require_user(message: Message, user: User | None) -> bool:
    if user is None:
        await message.answer(LOGIN_HINT)
        return False
    return True


async def require_admin(message: Message, user: User | None) -> bool:
    if user is None:
        await message.answer(LOGIN_HINT)
        return False
    if not user.is_main_admin:
        await message.answer("❌ Недостаточно прав. Действие доступно только главному администратору.")
        return False
    return True


async def require_user_cb(callback: CallbackQuery, user: User | None) -> bool:
    if user is None:
        await callback.answer("Сначала войдите в аккаунт: /login логин пароль", show_alert=True)
        return False
    return True


async def require_admin_cb(callback: CallbackQuery, user: User | None) -> bool:
    if user is None:
        await callback.answer("Сначала войдите в аккаунт: /login логин пароль", show_alert=True)
        return False
    if not user.is_main_admin:
        await callback.answer("Действие доступно только главному администратору.", show_alert=True)
        return False
    return True


WORKER_DENIED = (
    "❌ Работнику доступны только личные уведомления "
    "(включение и выключение через /settings)."
)


async def require_automation(message: Message, user: User | None) -> bool:
    """Allow only accounts that manage the bot (admins and ordinary users)."""
    if not await require_user(message, user):
        return False
    if user is not None and not user.can_manage_automation:
        await message.answer(WORKER_DENIED)
        return False
    return True


async def require_automation_cb(
    callback: CallbackQuery, user: User | None
) -> bool:
    if not await require_user_cb(callback, user):
        return False
    if user is not None and not user.can_manage_automation:
        await callback.answer(WORKER_DENIED, show_alert=True)
        return False
    return True


__all__ = [
    "LOGIN_HINT",
    "WORKER_DENIED",
    "require_admin",
    "require_admin_cb",
    "require_automation",
    "require_automation_cb",
    "require_user",
    "require_user_cb",
    "safe_delete",
    "safe_delete_callback",
]
