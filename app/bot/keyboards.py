from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import BotSettings, User


def _mark(value: bool) -> str:
    return "✅" if value else "❌"


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Назад", "menu:back")]])


def main_menu(user: User) -> InlineKeyboardMarkup:
    if user.is_worker:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [_btn("🔔 Мои уведомления", "menu:settings")],
            ]
        )
    rows: list[list[InlineKeyboardButton]] = [
        [_btn("⚙️ Настройки", "menu:settings"), _btn("📊 Статус", "menu:status")],
        [_btn("📜 Логи", "menu:logs"), _btn("🛑 Стоп всё", "stop_all")],
    ]
    if user.is_main_admin:
        rows.append(
            [_btn("🔐 Cookies", "menu:cookies"), _btn("👥 Аккаунты", "menu:accounts")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu(settings: BotSettings, user: User) -> InlineKeyboardMarkup:
    if user.is_worker:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _btn(
                        f"🔔 Мои уведомления: {_mark(user.notifications_enabled)}",
                        "toggle:notifications_enabled",
                    )
                ],
                [_btn("⬅️ Назад", "menu:back")],
            ]
        )

    dry_run_locked = (not user.is_main_admin) and (not settings.dry_run)
    dry_run_text = (
        "🔒 Тестовый режим: LIVE (только гл. админ)"
        if dry_run_locked
        else f"🧪 Тестовый режим: {_mark(settings.dry_run)}"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn(
                f"🔔 Мои уведомления: {_mark(user.notifications_enabled)}",
                "toggle:notifications_enabled",
            ),
            _btn(
                f"🔄 Автовыставление: {_mark(settings.auto_relist_enabled)}",
                "toggle:auto_relist_enabled",
            ),
        ],
        [
            _btn(
                "🚀 Автовыставление с бустом: "
                f"{_mark(settings.auto_boost_enabled)}",
                "toggle:auto_boost_enabled",
            )
        ],
        [_btn(dry_run_text, "toggle:dry_run")],
        [_btn("🛑 Стоп всё", "stop_all"), _btn("⬅️ Назад", "menu:back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def accounts_menu(users: list[User]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user in users:
        if user.is_main_admin:
            continue
        action = "enable" if not user.is_active else "disable"
        label = "включить" if not user.is_active else "отключить"
        role_label = "👷 работник" if user.is_worker else "👤 пользователь"
        rows.append(
            [
                _btn(
                    f"{user.username} ({role_label}): {label}",
                    f"acct:{action}:{user.id}",
                ),
                _btn("🗑 удалить", f"acct:delete:{user.id}"),
            ]
        )
    rows.append([_btn("➕ Создать работника", "acct:create_worker")])
    rows.append([_btn("🔄 Обновить", "menu:accounts"), _btn("⬅️ Назад", "menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirm(user_id: int, username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(f"✅ Удалить {username}", f"acct:confirm_delete:{user_id}"),
                _btn("⬅️ Отмена", "menu:accounts"),
            ]
        ]
    )


def dry_run_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ Включить LIVE", "dryrun:confirm_off"),
                _btn("⬅️ Отмена", "menu:settings"),
            ]
        ]
    )


__all__ = [
    "accounts_menu",
    "back_menu",
    "delete_confirm",
    "dry_run_confirm",
    "main_menu",
    "settings_menu",
]
