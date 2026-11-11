from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.helpers import require_automation
from app.bot.texts import build_logs_text
from app.context import AppContext
from app.models import User


router = Router(name="logs")


@router.message(Command("logs"))
async def cmd_logs(
    message: Message, ctx: AppContext, user: User | None
) -> None:
    if not await require_automation(message, user):
        return
    logs = await ctx.audit.recent(20)
    await message.answer(build_logs_text(logs, ctx.config.timezone))


@router.message(Command("stop"))
async def cmd_stop(
    message: Message, ctx: AppContext, user: User | None
) -> None:
    if not await require_automation(message, user):
        return
    await ctx.automation.request_stop("Команда /stop", actor=user)
    await message.answer(
        "🛑 Все задачи остановлены.\n"
        "Cookies и настройки сохранены. /resume — возобновить."
    )


@router.message(Command("resume"))
async def cmd_resume(
    message: Message, ctx: AppContext, user: User | None
) -> None:
    if not await require_automation(message, user):
        return
    await ctx.automation.resume(actor=user)
    await message.answer(
        "▶️ Задачи возобновлены. Проверка сессии и продаж начнётся "
        "со следующего цикла."
    )


__all__ = ["router"]
