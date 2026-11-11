from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.helpers import LOGIN_HINT, require_user, safe_delete
from app.bot.keyboards import main_menu
from app.bot.states import ChangePassword
from app.context import AppContext
from app.models import User
from app.security.passwords import verify_password
from app.services.auth import AuthError


router = Router(name="auth")

LOGIN_USAGE = "Использование: <code>/login логин пароль</code>"


@router.message(Command("login"))
async def cmd_login(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User | None,
    state: FSMContext,
) -> None:
    # Delete the password-containing command as early as possible.
    await safe_delete(message)

    if message.from_user is None:
        return
    if not command.args:
        await message.answer(LOGIN_USAGE)
        return

    parts = command.args.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(LOGIN_USAGE)
        return
    login, password = parts

    try:
        result = await ctx.auth.authenticate(
            login, password, message.from_user.id
        )
    except AuthError as exc:
        await message.answer(f"❌ {html.escape(exc.message)}")
        return

    if result.must_change_password:
        await state.set_state(ChangePassword.waiting_new)
        await state.update_data(forced=True, user_id=result.user.id)
        await message.answer(
            "✅ Вход выполнен.\n"
            "🔐 Смените пароль: отправьте новый пароль (минимум 8 символов).\n"
            "Сообщение с паролем будет удалено. /cancel — отмена."
        )
        return

    await state.clear()
    await message.answer(
        f"✅ Вход выполнен: <b>{html.escape(result.user.username)}</b>",
        reply_markup=main_menu(result.user),
    )


@router.message(Command("logout"))
async def cmd_logout(
    message: Message,
    ctx: AppContext,
    user: User | None,
    state: FSMContext,
) -> None:
    if not await require_user(message, user):
        return
    await ctx.auth.logout(user)
    await state.clear()
    await message.answer("👋 Telegram отвязан от аккаунта.")


@router.message(Command("password"))
async def cmd_password(
    message: Message,
    ctx: AppContext,
    user: User | None,
    state: FSMContext,
) -> None:
    if not await require_user(message, user):
        return

    if user.must_change_password:
        await state.set_state(ChangePassword.waiting_new)
        await state.update_data(forced=True, user_id=user.id)
        await message.answer(
            "🔐 Отправьте новый пароль (минимум 8 символов).\n"
            "Сообщение будет удалено. /cancel — отмена."
        )
        return

    await state.set_state(ChangePassword.waiting_old)
    await message.answer(
        "🔐 Отправьте текущий пароль. Сообщение будет удалено. "
        "/cancel — отмена."
    )


@router.message(ChangePassword.waiting_old, F.text, ~F.text.startswith("/"))
async def process_old_password(
    message: Message,
    ctx: AppContext,
    user: User | None,
    state: FSMContext,
) -> None:
    password = message.text or ""
    await safe_delete(message)

    if user is None:
        await state.clear()
        await message.answer(LOGIN_HINT)
        return
    if not verify_password(password, user.password_hash):
        await message.answer(
            "❌ Текущий пароль неверен. Попробуйте ещё раз или /cancel."
        )
        return

    await state.set_state(ChangePassword.waiting_new)
    await state.update_data(forced=False, user_id=user.id)
    await message.answer(
        "🔐 Отправьте новый пароль (минимум 8 символов). "
        "Сообщение будет удалено."
    )


@router.message(ChangePassword.waiting_new, F.text, ~F.text.startswith("/"))
async def process_new_password(
    message: Message,
    ctx: AppContext,
    user: User | None,
    state: FSMContext,
) -> None:
    password = message.text or ""
    await safe_delete(message)

    if user is None:
        await state.clear()
        await message.answer(LOGIN_HINT)
        return

    data = await state.get_data()
    expected_id = data.get("user_id")
    if expected_id is not None and int(expected_id) != user.id:
        await state.clear()
        await message.answer(
            "❌ Смена пароля была начата для другого аккаунта. "
            "Войдите заново командой /login."
        )
        return

    try:
        await ctx.auth.change_password(user, "", password, forced=True)
    except AuthError as exc:
        await message.answer(
            f"❌ {html.escape(exc.message)}\n"
            "Попробуйте другой пароль или /cancel."
        )
        return

    await state.clear()
    await message.answer(
        "✅ Пароль изменён.",
        reply_markup=main_menu(user),
    )


__all__ = ["router"]
