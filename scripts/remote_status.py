"""Print the bot state from the Railway container (runs inside the container)."""

from __future__ import annotations

import sqlite3

DB = "/data/playerok.db"

connection = sqlite3.connect(DB)
print("--- сессия Playerok ---")
for row in connection.execute(
    "select is_alive, last_checked_at, cookies_updated_at, last_error "
    "from playerok_session"
):
    print(row)
print("--- настройки ---")
for row in connection.execute(
    "select items_in_sale, items_checked_at, stopped, stop_reason, "
    "auto_relist_enabled, auto_boost_enabled, dry_run, daily_boost_limit, "
    "max_boost_amount, sales_initialized from bot_settings"
):
    print(row)
print("--- аккаунты ---")
for row in connection.execute(
    "select username, role, is_active, notifications_enabled, telegram_id "
    "from users"
):
    print(row)
print("--- последние действия ---")
for row in connection.execute(
    "select created_at, action, details from audit_log order by id desc limit 8"
):
    print(row)
print("--- продажи ---")
for row in connection.execute(
    "select external_id, status, boost_state, title, price from sales "
    "order by id desc limit 4"
):
    print(row)
