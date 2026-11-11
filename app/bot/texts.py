from __future__ import annotations

import html
from datetime import datetime
from decimal import Decimal

from app.models import AuditLog, BotSettings, User
from app.services.cookies import CookieStatus
from app.utils.parsing import format_dt, money, truncate


def _mark(value: bool) -> str:
    return "✅" if value else "❌"


WELCOME_TEXT = (
    "👋 <b>Playerok Bot</b>\n\n"
    "Бот следит за продажами Playerok, перевыставляет проданные лоты, "
    "оплачивает буст с основного баланса и присылает уведомления о новых "
    "личных сообщениях Playerok.\n\n"
    "🔐 Вход: <code>/login логин пароль</code>\n"
    "Сообщение с паролем будет удалено сразу после входа.\n\n"
    "При первом входе бот попросит сменить пароль (минимум 8 символов).\n"
    "Главный администратор создаётся при первом запуске из данных <code>.env</code>."
)


def build_help_text(user: User | None) -> str:
    is_admin = bool(user and user.is_main_admin)
    is_worker = bool(user and user.is_worker)

    lines = [
        "<b>ℹ️ Команды</b>",
        "",
        "/start — главное меню",
        "/login логин пароль — вход (только личные сообщения)",
        "/logout — отвязать Telegram от аккаунта",
        "/password — сменить пароль",
    ]
    if is_worker:
        lines += [
            "/settings — мои уведомления (вкл/выкл)",
            "/cancel — отменить ввод пароля",
            "",
            "<b>Роль: работник</b>",
            "Доступны только личные уведомления: новые продажи и сообщения "
            "в чате Playerok.",
        ]
        return "\n".join(lines)

    lines += [
        "/settings — настройки с кнопками",
        "/status — состояние бота и сессии",
        "/limit сумма — дневной лимит буста, например <code>/limit 500</code>",
        "/boost_limit сумма — лимит цены одного буста, например <code>/boost_limit 925</code>",
        "/dryrun on|off — тестовый режим",
        "/logs — последние действия",
        "/stop — остановить все задачи",
        "/resume — возобновить задачи",
        "/cancel — отменить ввод пароля",
    ]
    if is_admin:
        lines += [
            "",
            "<b>Только главный администратор</b>",
            "/cookies — статус cookies и инструкция",
            "Отправьте JSON-файл с cookies — бот сохранит его в зашифрованном виде",
            "/accounts — список аккаунтов",
            "/create_account логин пароль [user|worker] — создать аккаунт",
            "/create_worker логин пароль — создать работника (только уведомления)",
            "/delete_account логин — удалить аккаунт",
            "/disable_account логин — отключить аккаунт",
            "/enable_account логин — включить аккаунт",
        ]
    return "\n".join(lines)


def build_status_text(
    settings: BotSettings,
    cookie_status: CookieStatus,
    spend: Decimal,
    remaining: Decimal,
    next_run: datetime | None,
    *,
    timezone: str,
    is_admin: bool,
    notifications_enabled: bool,
) -> str:
    alive = {
        True: "активна",
        False: "недействительна",
        None: "не проверена",
    }.get(cookie_status.is_alive, "не проверена")

    lines = [
        "<b>📊 Статус</b>",
        f"Задачи: {'⛔ остановлены' if settings.stopped else '▶️ работают'}",
    ]
    if settings.stopped:
        lines.append(f"Причина: {html.escape(settings.stop_reason or '—')}")
        lines.append(f"Остановлено: {format_dt(settings.stopped_at, timezone)}")

    lines += [
        "",
        f"🔔 Мои уведомления: {_mark(notifications_enabled)}",
        f"🔄 Автовыставление: {_mark(settings.auto_relist_enabled)}",
        f"🚀 Автовыставление с бустом: {_mark(settings.auto_boost_enabled)}",
        f"🧪 Тестовый режим: {_mark(settings.dry_run)}",
        "",
        f"Дневной лимит буста: {money(settings.daily_boost_limit)}",
        f"Лимит за один буст: {money(settings.max_boost_amount)}",
        f"Потрачено сегодня: {money(spend)}",
        f"Остаток: {money(remaining)}",
        "",
        "<b>Playerok</b>",
        f"Cookies: {'загружены' if cookie_status.exists else 'не загружены'}",
    ]
    if settings.items_in_sale is not None:
        lines.append(f"Товаров в продаже: {settings.items_in_sale}")
        if settings.items_checked_at:
            lines.append(
                f"Проверено: {format_dt(settings.items_checked_at, timezone)}"
            )
    if cookie_status.exists:
        lines.append(f"Обновлены: {format_dt(cookie_status.updated_at, timezone)}")
        lines.append(f"Сессия: {alive}")
        if cookie_status.last_checked_at:
            lines.append(
                f"Проверена: {format_dt(cookie_status.last_checked_at, timezone)}"
            )
        if is_admin and cookie_status.last_error:
            lines.append(
                "Последняя ошибка: "
                f"<code>{html.escape(truncate(cookie_status.last_error, 300))}</code>"
            )
    if next_run is not None:
        lines += ["", f"Следующий цикл: {format_dt(next_run, timezone)}"]
    return "\n".join(lines)


def build_logs_text(logs: list[AuditLog], timezone: str) -> str:
    if not logs:
        return "📜 Пока нет записей."
    lines = ["<b>📜 Последние действия</b>"]
    for item in logs[:15]:
        who = f" <i>{html.escape(item.username)}</i>" if item.username else ""
        details = (
            f" — {html.escape(truncate(item.details, 180))}"
            if item.details
            else ""
        )
        lines.append(
            f"<code>{format_dt(item.created_at, timezone)}</code> "
            f"<b>{html.escape(item.action)}</b>{who}{details}"
        )
    return "\n".join(lines)


def build_accounts_text(users: list[User]) -> str:
    lines = ["<b>👥 Аккаунты</b>"]
    for user in users:
        if user.is_main_admin:
            role = "гл. админ"
        elif user.is_worker:
            role = "👷 работник (только уведомления)"
        else:
            role = "пользователь"
        status = "✅" if user.is_active else "⛔"
        bound = "🔗 привязан" if user.telegram_id else "— не привязан"
        notif = "🔔" if user.notifications_enabled else "🔕"
        lines.append(
            f"{status} <b>{html.escape(user.username)}</b> — {role}, {bound}, "
            f"уведомления {notif}"
        )
    return "\n".join(lines)


def build_settings_text(settings: BotSettings, user: User) -> str:
    if user.is_worker:
        return (
            "<b>🔔 Мои уведомления</b>\n"
            f"Уведомления: {_mark(user.notifications_enabled)}\n\n"
            "Вам приходят уведомления о новых продажах и новых личных сообщениях "
            "в чате Playerok.\n"
            "Настройка действует только на ваш аккаунт — на бота в целом "
            "она не влияет."
        )
    return (
        "<b>⚙️ Настройки</b>\n"
        f"🔔 Мои уведомления: {_mark(user.notifications_enabled)}\n"
        f"🔄 Автовыставление: {_mark(settings.auto_relist_enabled)}\n"
        f"🚀 Автовыставление с бустом: {_mark(settings.auto_boost_enabled)}\n"
        f"🧪 Тестовый режим: {_mark(settings.dry_run)}\n\n"
        "Переключатель уведомлений действует только на ваш аккаунт.\n"
        "Нажмите кнопку, чтобы переключить настройку."
    )


__all__ = [
    "WELCOME_TEXT",
    "build_accounts_text",
    "build_help_text",
    "build_logs_text",
    "build_settings_text",
    "build_status_text",
]
