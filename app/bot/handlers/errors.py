from __future__ import annotations

import logging
from typing import Awaitable, Callable

from aiogram.types import ErrorEvent

from app.context import AppContext


logger = logging.getLogger(__name__)

# Telegram errors that are not the bot's fault and should not spam the chat.
BENIGN_ERRORS = (
    "query is too old",
    "message is not modified",
    "message to edit not found",
    "message can't be deleted",
)


def build_error_handler(
    ctx: AppContext,
) -> Callable[[ErrorEvent], Awaitable[None]]:
    async def handler(event: ErrorEvent) -> None:
        text = str(event.exception)
        if any(marker in text.lower() for marker in BENIGN_ERRORS):
            logger.warning("Benign Telegram error ignored: %s", text)
            return
        logger.error(
            "Unhandled update error",
            exc_info=event.exception,
        )
        try:
            await ctx.notifications.generic_error(
                f"Ошибка обработки команды: {event.exception}"
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to report handler error")

    return handler


__all__ = ["build_error_handler"]
