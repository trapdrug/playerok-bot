from __future__ import annotations

import asyncio
import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.helpers import require_admin, safe_delete
from app.context import AppContext
from app.models import User
from app.playerok.exceptions import SessionExpiredError
from app.services.cookies import CookieFormatError
from app.utils.parsing import format_dt


logger = logging.getLogger(__name__)
router = Router(name="cookies")


@router.message(Command("cookies"))
async def cmd_cookies(
    message: Message, ctx: AppContext, user: User | None
) -> None:
    if not await require_admin(message, user):
        return

    status = await ctx.cookies.status()
    lines = [
        "<b>🔐 Cookies Playerok</b>",
        f"Статус: {'загружены' if status.exists else 'не загружены'}",
    ]
    if status.exists:
        lines.append(
            f"Обновлены: {format_dt(status.updated_at, ctx.config.timezone)}"
        )
        alive = {
            True: "активна",
            False: "недействительна",
            None: "не проверена",
        }.get(status.is_alive, "не проверена")
        lines.append(f"Сессия: {alive}")
        if status.last_checked_at:
            lines.append(
                "Проверена: "
                f"{format_dt(status.last_checked_at, ctx.config.timezone)}"
            )
        if status.last_error:
            lines.append(
                "Последняя ошибка: "
                f"<code>{html.escape(status.last_error[:300])}</code>"
            )

    lines += [
        "",
        "Отправьте JSON-файл с cookies. Сообщение с файлом будет удалено, "
        "а cookies сохранятся в зашифрованном виде.",
        "Подходит JSON-массив cookies или объект с полем <code>cookies</code>.",
    ]
    await message.answer("\n".join(lines))


@router.message(F.document)
async def handle_cookie_document(
    message: Message, ctx: AppContext, user: User | None
) -> None:
    if user is None or not user.is_main_admin:
        await safe_delete(message)
        await message.answer("❌ Недостаточно прав.")
        return

    document = message.document
    if document is None:
        return

    filename = (document.file_name or "").lower()
    if not filename.endswith(".json"):
        await safe_delete(message)
        await message.answer("❌ Нужен JSON-файл с cookies.")
        return

    if (
        document.file_size
        and document.file_size > ctx.config.max_cookie_file_bytes
    ):
        await safe_delete(message)
        await message.answer("❌ Файл cookies слишком большой.")
        return

    try:
        buffer = await message.bot.download(document)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cookie file download failed: %s", exc)
        await safe_delete(message)
        await message.answer("❌ Не удалось скачать файл.")
        return

    if buffer is None:
        await safe_delete(message)
        await message.answer("❌ Не удалось прочитать файл.")
        return

    payload = buffer.read()
    # Delete the uploaded file before doing anything else.
    await safe_delete(message)

    try:
        count = await ctx.cookies.save_from_bytes(payload, user)
    except CookieFormatError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
        return

    await message.answer(
        f"✅ Cookies сохранены ({count} шт.) и зашифрованы. "
        "Проверяю сессию Playerok…"
    )
    task = asyncio.create_task(_verify_cookies(ctx, count))
    ctx.background_tasks.add(task)
    task.add_done_callback(ctx.background_tasks.discard)


async def _verify_cookies(ctx: AppContext, count: int) -> None:
    try:
        cookies = await ctx.cookies.get_cookies()
        if not cookies:
            await ctx.notifications.notify(
                "❌ Cookies не найдены после сохранения.", critical=True
            )
            return

        info = await ctx.client.verify_cookies(cookies)
        await ctx.cookies.mark_checked(True)
        await ctx.notifications.cookies_saved(count, info.username)
        await ctx.audit.log(
            "cookies_verified",
            f"Сессия Playerok активна: {info.username}",
        )
    except SessionExpiredError:
        await ctx.cookies.mark_checked(False, "Cookies недействительны")
        await ctx.notifications.notify(
            "❌ <b>Загруженные cookies недействительны</b>\n"
            "Playerok показывает форму входа. Загрузите свежий JSON с cookies.",
            critical=True,
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc)[:300]
        await ctx.cookies.mark_checked(False, message)
        await ctx.notifications.notify(
            "⚠️ <b>Не удалось проверить cookies</b>\n"
            f"<code>{html.escape(message)}</code>\n"
            "Cookies сохранены, но сессия не проверена.",
            critical=True,
        )


__all__ = ["router"]
