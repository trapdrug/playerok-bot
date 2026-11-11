from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.bot.handlers import setup_routers
from app.bot.handlers.errors import build_error_handler
from app.bot.middlewares import PrivateOnlyMiddleware, UserContextMiddleware
from app.config import AppConfig
from app.context import AppContext
from app.db import Database
from app.logging_setup import setup_logging
from app.playerok.client import PlayerokClient
from app.playerok.selectors import Selectors, SelectorsError
from app.security.crypto import CookieCipher
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.automation import AutomationService
from app.services.cookies import CookieService
from app.services.notifications import NotificationService
from app.services.scheduler import SchedulerService
from app.services.settings import SettingsService


logger = logging.getLogger(__name__)


async def build_context(config: AppConfig) -> tuple[AppContext, Bot]:
    config.data_dir.mkdir(parents=True, exist_ok=True)

    db = Database(config.database_url)
    await db.init()

    audit = AuditService(db)
    auth = AuthService(db, config, audit)
    await auth.ensure_main_admin()

    settings = SettingsService(db, config, audit)
    await settings.get()

    try:
        cipher = CookieCipher(config.fernet_key.get_secret_value())
    except ValueError as exc:
        raise RuntimeError(
            "FERNET_KEY недействителен. Сгенерируйте новый ключ командой "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from exc

    cookies = CookieService(db, config, cipher, audit)

    try:
        selectors = Selectors(config.selectors_path)
    except SelectorsError as exc:
        raise RuntimeError(
            f"Не удалось загрузить селекторы: {exc}. "
            "Проверьте SELECTORS_FILE и файл selectors.yaml."
        ) from exc

    client = PlayerokClient(config, selectors)

    bot = Bot(
        token=config.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifications = NotificationService(bot, db, settings)
    automation = AutomationService(
        config, db, settings, cookies, notifications, audit, client
    )
    scheduler = SchedulerService(automation, config)

    ctx = AppContext(
        config=config,
        db=db,
        audit=audit,
        auth=auth,
        cookies=cookies,
        settings=settings,
        notifications=notifications,
        client=client,
        automation=automation,
        scheduler=scheduler,
    )

    await automation.initialize()
    return ctx, bot


async def _set_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="login", description="Вход: логин пароль"),
        BotCommand(command="logout", description="Выйти из аккаунта"),
        BotCommand(command="password", description="Сменить пароль"),
        BotCommand(command="settings", description="Настройки и мои уведомления"),
        BotCommand(command="status", description="Статус бота и сессии"),
        BotCommand(command="limit", description="Дневной лимит буста"),
        BotCommand(command="boost_limit", description="Лимит цены одного буста"),
        BotCommand(command="dryrun", description="Тестовый режим"),
        BotCommand(command="logs", description="Последние действия"),
        BotCommand(command="stop", description="Остановить все задачи"),
        BotCommand(command="resume", description="Возобновить задачи"),
        BotCommand(command="cancel", description="Отменить ввод"),
        BotCommand(command="cookies", description="Cookies (гл. админ)"),
        BotCommand(command="accounts", description="Аккаунты (гл. админ)"),
        BotCommand(
            command="create_account",
            description="Создать аккаунт: логин пароль [user|worker] (гл. админ)",
        ),
        BotCommand(
            command="create_worker",
            description="Создать работника: логин пароль (гл. админ)",
        ),
        BotCommand(
            command="delete_account", description="Удалить аккаунт (гл. админ)"
        ),
        BotCommand(
            command="disable_account",
            description="Отключить аккаунт (гл. админ)",
        ),
        BotCommand(
            command="enable_account",
            description="Включить аккаунт (гл. админ)",
        ),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to set bot commands", exc_info=True)


async def main() -> None:
    try:
        config = AppConfig()
    except Exception as exc:  # noqa: BLE001
        print(f"Ошибка конфигурации: {exc}")
        raise SystemExit(1) from exc

    setup_logging(
        level=config.log_level,
        log_file=config.data_dir / "bot.log",
        secrets=[
            config.bot_token.get_secret_value(),
            config.fernet_key.get_secret_value(),
            config.admin_initial_password.get_secret_value(),
        ],
    )

    logger.info("Starting Playerok bot")
    ctx, bot = await build_context(config)

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(PrivateOnlyMiddleware())
    dp.message.outer_middleware(UserContextMiddleware(ctx))
    dp.callback_query.outer_middleware(PrivateOnlyMiddleware())
    dp.callback_query.outer_middleware(UserContextMiddleware(ctx))
    dp.include_router(setup_routers())
    dp.errors.register(build_error_handler(ctx))

    await _set_commands(bot)
    ctx.scheduler.start()

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(
            bot, allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        ctx.scheduler.shutdown()
        for task in list(ctx.background_tasks):
            task.cancel()
        if ctx.background_tasks:
            await asyncio.gather(
                *ctx.background_tasks, return_exceptions=True
            )
        await ctx.client.close()
        await ctx.db.dispose()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
