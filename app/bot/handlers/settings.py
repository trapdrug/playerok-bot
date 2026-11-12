from __future__ import annotations

import html
from decimal import Decimal

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.helpers import (
    require_admin_cb,
    require_automation,
    require_automation_cb,
    require_user,
    require_user_cb,
    safe_answer,
)
from app.bot.keyboards import dry_run_confirm, settings_menu
from app.bot.texts import build_settings_text
from app.context import AppContext
from app.models import BotSettings, User
from app.services.auth import AuthError
from app.services.settings import FEATURE_LABELS
from app.utils.parsing import money, parse_money


router = Router(name="settings")


async def edit_settings(
    callback: CallbackQuery,
    settings: BotSettings,
    user: User,
    *,
    confirm_live: bool = False,
) -> None:
    if confirm_live:
        text = (
            "🔴 <b>Включить LIVE-режим?</b>\n\n"
            "Бот начнёт реально оплачивать буст с основного баланса. "
            "Убедитесь, что cookies активны и дневной лимит задан через /limit."
        )
        markup = dry_run_confirm()
    else:
        text = build_settings_text(settings, user)
        markup = settings_menu(settings, user)

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


@router.message(Command("settings"))
async def cmd_settings(
    message: Message, ctx: AppContext, user: User | None
) -> None:
    if not await require_user(message, user):
        return
    settings = await ctx.settings.get()
    await message.answer(
        build_settings_text(settings, user),
        reply_markup=settings_menu(settings, user),
    )


@router.callback_query(F.data == "menu:settings")
async def cb_settings(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_user_cb(callback, user):
        return
    await safe_answer(callback)
    settings = await ctx.settings.get()
    await edit_settings(callback, settings, user)


@router.callback_query(F.data.startswith("toggle:"))
async def cb_toggle(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_user_cb(callback, user):
        return

    feature = (callback.data or "").split(":", 1)[1]

    if feature == "notifications_enabled":
        # Personal switch: affects only this account, never the whole bot.
        try:
            updated_user = await ctx.auth.set_notifications(
                user, not user.notifications_enabled
            )
        except AuthError as exc:
            await safe_answer(callback, html.escape(exc.message), show_alert=True)
            return
        await safe_answer(callback, 
            "🔔 Включены" if updated_user.notifications_enabled else "🔕 Выключены"
        )
        settings = await ctx.settings.get()
        await edit_settings(callback, settings, updated_user)
        return

    if user is not None and not user.can_manage_automation:
        await safe_answer(callback, 
            "Работнику доступны только личные уведомления.", show_alert=True
        )
        return

    if feature not in FEATURE_LABELS:
        await safe_answer(callback, "Неизвестная настройка", show_alert=True)
        return

    current = await ctx.settings.get()
    new_value = not bool(getattr(current, feature))

    if feature == "dry_run" and not new_value:
        if not user.is_main_admin:
            await safe_answer(callback, 
                "Только главный администратор может включить LIVE-режим.",
                show_alert=True,
            )
            return
        await safe_answer(callback)
        await edit_settings(callback, current, user, confirm_live=True)
        return

    updated, value = await ctx.settings.toggle(feature, actor=user)
    await safe_answer(callback, "Включено" if value else "Выключено")
    await edit_settings(callback, updated, user)

    if (
        feature == "auto_boost_enabled"
        and value
        and Decimal(updated.daily_boost_limit) <= 0
    ):
        await callback.bot.send_message(
            callback.from_user.id,
            "⚠️ Дневной лимит буста равен 0. Установите /limit сумма, "
            "иначе буст не будет оплачен.",
        )


@router.callback_query(F.data == "stop_all")
async def cb_stop_all(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_automation_cb(callback, user):
        return
    await ctx.automation.request_stop("Команда из меню: Стоп всё", actor=user)
    await safe_answer(callback, "Все задачи остановлены", show_alert=True)
    settings = await ctx.settings.get()
    await edit_settings(callback, settings, user)


@router.message(Command("limit"))
async def cmd_limit(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User | None,
) -> None:
    if not await require_automation(message, user):
        return

    if not command.args:
        settings = await ctx.settings.get()
        spend = await ctx.settings.get_today_spend()
        await message.answer(
            f"Текущий дневной лимит буста: {money(settings.daily_boost_limit)}\n"
            f"Потрачено сегодня: {money(spend)}\n\n"
            "Использование: <code>/limit 500</code>"
        )
        return

    amount = parse_money(command.args)
    if amount is None or amount < 0:
        await message.answer(
            "❌ Не удалось разобрать сумму. Пример: <code>/limit 500</code>"
        )
        return

    settings, disabled = await ctx.settings.set_daily_limit(amount, actor=user)
    spend = await ctx.settings.get_today_spend()
    remaining = max(
        Decimal("0"), Decimal(settings.daily_boost_limit) - spend
    )
    text = (
        f"✅ Дневной лимит буста: {money(settings.daily_boost_limit)}\n"
        f"Потрачено сегодня: {money(spend)}\n"
        f"Остаток: {money(remaining)}"
    )
    if disabled:
        text += (
            "\n\n⛔ Автовыставление с бустом отключено: лимит исчерпан."
        )
    await message.answer(text)


@router.message(Command("boost_limit"))
async def cmd_boost_limit(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User | None,
) -> None:
    if not await require_automation(message, user):
        return

    settings = await ctx.settings.get()
    if not command.args:
        await message.answer(
            "Текущий лимит за один буст: "
            f"{money(settings.max_boost_amount)}\n"
            "Бот не будет покупать премиум дороже этой суммы за один товар.\n\n"
            "Использование: <code>/boost_limit 925</code>\n"
            "<code>/boost_limit 0</code> — убрать лимит (не рекомендуется)."
        )
        return

    amount = parse_money(command.args)
    if amount is None or amount < 0:
        await message.answer(
            "❌ Не удалось разобрать сумму. Пример: <code>/boost_limit 925</code>"
        )
        return

    settings = await ctx.settings.set_max_boost_amount(amount, actor=user)
    await message.answer(
        f"✅ Лимит за один буст: {money(settings.max_boost_amount)}\n"
        "Лимит применяется к цене буста. Если цену буста определить не "
        "удалось, бот берёт цену товара без скидки."
    )


@router.message(Command("dryrun"))
async def cmd_dryrun(
    message: Message,
    command: CommandObject,
    ctx: AppContext,
    user: User | None,
) -> None:
    if not await require_automation(message, user):
        return

    arg = (command.args or "").strip().lower()
    settings = await ctx.settings.get()

    if not arg:
        state = "✅ включён" if settings.dry_run else "❌ выключен (LIVE)"
        await message.answer(
            f"Тестовый режим: {state}\n"
            "Использование: <code>/dryrun on</code> или <code>/dryrun off</code>"
        )
        return

    if arg in ("on", "вкл", "включить", "1", "true"):
        await ctx.settings.set_feature("dry_run", True, actor=user)
        await message.answer(
            "🧪 Тестовый режим включён. Бот ничего не оплачивает."
        )
        return

    if arg in ("off", "выкл", "выключить", "0", "false"):
        if not user.is_main_admin:
            await message.answer(
                "❌ Только главный администратор может включить LIVE-режим."
            )
            return
        if settings.dry_run:
            await message.answer(
                "🔴 <b>Включить LIVE-режим?</b>\n\n"
                "Бот начнёт реально оплачивать буст. Убедитесь, что cookies "
                "активны и дневной лимит задан через /limit.",
                reply_markup=dry_run_confirm(),
            )
        else:
            await message.answer("LIVE-режим уже включён.")
        return

    await message.answer(
        "Использование: <code>/dryrun on</code> или <code>/dryrun off</code>"
    )


@router.callback_query(F.data == "dryrun:confirm_off")
async def cb_dryrun_confirm_off(
    callback: CallbackQuery, ctx: AppContext, user: User | None
) -> None:
    if not await require_admin_cb(callback, user):
        return

    await ctx.settings.set_feature("dry_run", False, actor=user)
    await ctx.notifications.notify(
        "🔴 <b>LIVE-режим включён</b>\n"
        "Бот будет реально оплачивать буст с основного баланса. "
        "Проверьте /limit и /status.",
        critical=True,
    )
    await safe_answer(callback, "LIVE-режим включён", show_alert=True)
    settings = await ctx.settings.get()
    await edit_settings(callback, settings, user)


__all__ = ["router"]
