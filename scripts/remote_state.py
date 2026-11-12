"""Show the current state of the Railway bot (diagnostics)."""

from __future__ import annotations

import sqlite3

connection = sqlite3.connect("/data/playerok.db")
print("--- настройки ---")
for row in connection.execute(
    "select stopped, stop_reason, auto_relist_enabled, auto_boost_enabled, "
    "dry_run, daily_boost_limit, max_boost_amount, sales_initialized, "
    "items_in_sale, items_checked_at from bot_settings"
):
    print(row)
print("--- сессия ---")
for row in connection.execute(
    "select is_alive, last_checked_at, cookies_updated_at, last_error "
    "from playerok_session"
):
    print(row)
print("--- продажи (не baseline) ---")
for row in connection.execute(
    "select external_id, status, boost_state, title, price, lot_url, error "
    "from sales where status != 'baseline' order by id desc limit 5"
):
    print(row)
print("--- последние 8 действий ---")
for row in connection.execute(
    "select created_at, action, details from audit_log order by id desc limit 8"
):
    print(row)
