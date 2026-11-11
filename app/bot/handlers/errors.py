from __future__ import annotations

import logging
from typing import Awaitable, Callable

from aiogram.types import ErrorEvent

from app.context import AppContext


logger = logging.getLogger(__name__)


def build_error_handler(
    ctx: AppContext,
) -> Callable[[ErrorEvent], Awaitable[None]]:
    async def handler(event: ErrorEvent) -> None:
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
