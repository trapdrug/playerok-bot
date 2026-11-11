from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.helpers import require_admin, require_admin_cb, safe_delete
from app.bot.keyboards import accounts_menu, delete_confirm
from app.bot.texts import build_accounts_text
from app.context import AppContext
from app.models import User
from app.services.auth import AuthError


router = Router(name="accounts")

USAGE_CREATE = (
    "Использование: <code>/create_account логин пароль</code>\n"
    "Роль работника: <code>/create_account логин пароль worker</code>\n"
    "или <code>/create_worker логин пароль</code>"
)

ROLE_ALIASES = {
    "user": "user",
    "пользователь": "user",
    "worker": "worker",
    "работник": "worker",
}


def _parse_id(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def show_accounts(
    callback: CallbackQuery, ctx: AppContext, user: User
) -> None:
    users = await ctx.auth.list_users()
    text = build_accounts_text(users)
    markup = accounts_menu(users)
    if callback.message is None:
        await callback.bot.send_message(
            callback.from_user.id, text, reply_markup=markup
        )
        return
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        try:
            await callback.message.answer(text, reply_markup=markup)
        except Exception:  # noqa: BLE001
            pass


@router.message(Command("accounts"))
async def cmd_accounts(
    message: Message, ctx: AppContext, user: User | None
) -> None:
    if not await require_admin(message, user):
        return
    users = await ctx.auth.list_users()
    await message.answer(
        build_accounts_text(users), reply_markup=accounts_menu(users)
    )


@router.message(Command("create_account", "create_worker"))
async def cmd_create_account(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User | None,
) -> None:
    if not await require_admin(message, user):
        await safe_delete(message)
        return

    await safe_delete(message)
    if not command.args:
        await message.answer(USAGE_CREATE)
        return

    parts = command.args.split()
    if len(parts) < 2:
        await message.answer(USAGE_CREATE)
        return

    username = parts[0]
    role = "worker" if (command.command or "").endswith("create_worker") else "user"
    password_parts = parts[1:]
    if len(password_parts) >= 2:
        alias = ROLE_ALIASES.get(password_parts[-1].strip().lower())
        if alias:
            role = alias
            password_parts = password_parts[:-1]
    password = " ".join(password_parts)

    try:
        created = await ctx.auth.create_account(
            user, username, password, role=role
        )
    except AuthError as exc:
        await message.answer(f"❌ {html.escape(exc.message)}")
        return

    if created.is_worker:
        note = (
            "Роль: работник — доступны только личные уведомления "
            "(вкл/выкл через /settings)."
        )
    else:
        note = "Роль: пользователь."
    await message.answer(
        f"✅ Аккаунт <b>{html.escape(created.username)}</b> создан.\n"
        f"{note}\n"
        "При первом входе бот попросит сменить пароль."
    )


@router.message(Command("delete_account"))
async def cmd_delete_account(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User | None,
) -> None:
    if not await require_admin(message, user):
        return
    if not command.args:
        await message.answer("Использование: <code>/delete_account логин</code>")
        return

    target = await ctx.auth.get_by_username(command.args.strip())
    if target is None:
        await message.answer("❌ Аккаунт не найден.")
        return
    if target.is_main_admin:
        await message.answer("❌ Главного администратора нельзя удалить.")
        return

    await message.answer(
        f"Удалить аккаунт <b>{html.escape(target.username)}</b>?",
        reply_markup=delete_confirm(target.id, target.username),
    )


@router.message(Command("disable_account"))
async def cmd_disable_account(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User | None,
) -> None:
    if not await require_admin(message, user):
        return
    await _set_account_active(
        message, command, ctx, user, active=False
    )


@router.message(Command("enable_account"))
async def cmd_enable_account(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User | None,
) -> None:
    if not await require_admin(message, user):
        return
    await _set_account_active(
        message, command, ctx, user, active=True
    )


async def _set_account_active(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User,
    *,
    active: bool,
) -> None:
    if not command.args:
        await message.answer(
            "Использование: <code>/disable_account логин</code> или "
            "<code>/enable_account логин</code>"
        )
        return
    target = await ctx.auth.get_by_username(command.args.strip())
    if target is None:
        await message.answer("❌ Аккаунт не найден.")
        return
    try:
        await ctx.auth.set_active(user, target.id, active)
    except AuthError as exc:
        await message.answer(f"❌ {html.escape(exc.message)}")
        return
    await message.answer(
        f"✅ Аккаунт <b>{html.escape(target.username)}</b> "
        f"{'включён' if active else 'отключён'}."
    )


@router.callback_query(F.data == "menu:accounts")
async def cb_accounts(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_admin_cb(callback, user):
        return
    await callback.answer()
    await show_accounts(callback, ctx, user)


@router.callback_query(F.data == "acct:create_worker")
async def cb_create_worker_help(
    callback: CallbackQuery, user: User | None
) -> None:
    if not await require_admin_cb(callback, user):
        return
    await callback.answer(
        "Создайте работника командой /create_worker логин пароль\n"
        "У него доступны только личные уведомления (вкл/выкл).",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("acct:disable:"))
async def cb_disable_account(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_admin_cb(callback, user):
        return
    target_id = _parse_id((callback.data or "").split(":")[-1])
    if target_id is None:
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    try:
        await ctx.auth.set_active(user, target_id, False)
    except AuthError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await callback.answer("Аккаунт отключён")
    await show_accounts(callback, ctx, user)


@router.callback_query(F.data.startswith("acct:enable:"))
async def cb_enable_account(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_admin_cb(callback, user):
        return
    target_id = _parse_id((callback.data or "").split(":")[-1])
    if target_id is None:
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    try:
        await ctx.auth.set_active(user, target_id, True)
    except AuthError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await callback.answer("Аккаунт включён")
    await show_accounts(callback, ctx, user)


@router.callback_query(F.data.startswith("acct:delete:"))
async def cb_delete_account(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_admin_cb(callback, user):
        return
    target_id = _parse_id((callback.data or "").split(":")[-1])
    if target_id is None:
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    target = await ctx.auth.get_by_id(target_id)
    if target is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    if target.is_main_admin:
        await callback.answer(
            "Главного администратора нельзя удалить", show_alert=True
        )
        return

    await callback.answer()
    text = f"Удалить аккаунт <b>{html.escape(target.username)}</b>?"
    markup = delete_confirm(target.id, target.username)
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("acct:confirm_delete:"))
async def cb_confirm_delete_account(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_admin_cb(callback, user):
        return
    target_id = _parse_id((callback.data or "").split(":")[-1])
    if target_id is None:
        await callback.answer("Некорректный аккаунт", show_alert=True)
        return
    try:
        await ctx.auth.delete_account(user, target_id)
    except AuthError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await callback.answer("Аккаунт удалён")
    await show_accounts(callback, ctx, user)


__all__ = ["router"]
