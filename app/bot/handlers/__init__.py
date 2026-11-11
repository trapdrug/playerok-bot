from __future__ import annotations

from aiogram import Router

from app.bot.handlers import accounts, auth, common, cookies, logs, settings


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(auth.router)
    root.include_router(accounts.router)
    root.include_router(cookies.router)
    root.include_router(settings.router)
    root.include_router(logs.router)
    root.include_router(common.router)
    return root


__all__ = ["setup_routers"]
