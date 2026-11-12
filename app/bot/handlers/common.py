from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.helpers import require_automation, require_automation_cb, require_user, require_user_cb, safe_answer
from app.bot.keyboards import back_menu, main_menu
from app.bot.texts import (
    WELCOME_TEXT,
    build_help_text,
    build_logs_text,
    build_status_text,
)
from app.context import AppContext
from app.models import User


router = Router(name="common")


async def edit_or_send(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,  # noqa: ANN001
) -> None:
    if callback.message is None:
        await callback.bot.send_message(
            callback.from_user.id, text, reply_markup=reply_markup
        )
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        try:
            await callback.message.answer(text, reply_markup=reply_markup)
        except Exception:  # noqa: BLE001
            pass


async def status_text(ctx: AppContext, user: User) -> str:
    settings = await ctx.settings.get()
    cookie_status = await ctx.cookies.status()
    spend = await ctx.settings.get_today_spend()
    remaining = await ctx.settings.remaining_boost_budget()
    return build_status_text(
        settings,
        cookie_status,
        spend,
        remaining,
        ctx.scheduler.next_run_time,
        timezone=ctx.config.timezone,
        is_admin=user.is_main_admin,
        notifications_enabled=user.notifications_enabled,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, user: User | None) -> None:
    if user is None:
        await message.answer(WELCOME_TEXT)
        return
    await message.answer(
        f"👋 С возвращением, <b>{html.escape(user.username)}</b>.\n"
        "Выберите действие:",
        reply_markup=main_menu(user),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, user: User | None) -> None:
    await message.answer(build_help_text(user))


@router.message(Command("status"))
async def cmd_status(
    message: Message, ctx: AppContext, user: User | None
) -> None:
    if not await require_automation(message, user):
        return
    await message.answer(await status_text(ctx, user))


@router.callback_query(F.data == "menu:status")
async def cb_status(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_automation_cb(callback, user):
        return
    await safe_answer(callback)
    await edit_or_send(callback, await status_text(ctx, user), back_menu())


@router.callback_query(F.data == "menu:logs")
async def cb_logs(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_automation_cb(callback, user):
        return
    await safe_answer(callback)
    logs = await ctx.audit.recent(20)
    await edit_or_send(
        callback, build_logs_text(logs, ctx.config.timezone), back_menu()
    )


@router.callback_query(F.data == "menu:back")
async def cb_back(
    callback: CallbackQuery, user: User | None
) -> None:
    if user is None:
        await safe_answer(callback, "Сначала войдите в аккаунт", show_alert=True)
        return
    await safe_answer(callback)
    await edit_or_send(
        callback,
        "Главное меню:",
        main_menu(user),
    )


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await safe_answer(callback)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ввод отменён.")


__all__ = ["edit_or_send", "router", "status_text"]
