"""Show the sales and the audit log of the Railway instance."""

from __future__ import annotations

import sqlite3

connection = sqlite3.connect("/data/playerok.db")
print("--- продажи (не baseline) ---")
for row in connection.execute(
    "select external_id, status, boost_state, title, price, lot_url, "
    "relist_attempts, error from sales "
    "where status != 'baseline' order by id desc limit 6"
):
    print(row)
print("--- аудит ---")
for row in connection.execute(
    "select created_at, action, details from audit_log order by id desc limit 8"
):
    print(row)
print("--- настройки ---")
for row in connection.execute(
    "select stopped, stop_reason, auto_relist_enabled, auto_boost_enabled, "
    "dry_run, daily_boost_limit, max_boost_amount, sales_initialized "
    "from bot_settings"
):
    print(row)
