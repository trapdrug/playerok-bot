from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.context import AppContext


class PrivateOnlyMiddleware(BaseMiddleware):
    """The bot handles account commands only in private chats."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            if event.message is None or event.message.chat.type != ChatType.PRIVATE:
                try:
                    await event.answer(
                        "Бот работает только в личных сообщениях.",
                        show_alert=True,
                    )
                except Exception:  # noqa: BLE001
                    pass
                return None
        elif isinstance(event, Message):
            if event.chat.type != ChatType.PRIVATE:
                return None
        return await handler(event, data)


class UserContextMiddleware(BaseMiddleware):
    """Inject the service container and the bound bot account."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["ctx"] = self.ctx

        user = None
        tg_user = getattr(event, "from_user", None)
        if tg_user is not None:
            user = await self.ctx.auth.get_by_telegram_id(tg_user.id)
            if user is not None and not user.is_active:
                user = None
        data["user"] = user
        return await handler(event, data)


__all__ = ["PrivateOnlyMiddleware", "UserContextMiddleware"]
