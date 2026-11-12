"""Show the pending boost queue of the Railway instance."""

from __future__ import annotations

import sqlite3

connection = sqlite3.connect("/data/playerok.db")
print("--- очередь бустов ---")
for row in connection.execute(
    "select id, sale_external_id, lot_url, item_price, amount, status, attempts, "
    "reason, created_at, updated_at from pending_boosts order by id desc limit 6"
):
    print(" ", row)
print("--- продажи (не baseline) ---")
for row in connection.execute(
    "select external_id, status, boost_state, title, price, lot_url, error "
    "from sales where status != 'baseline' order by id desc limit 5"
):
    print(" ", row)
print("--- последние действия ---")
for row in connection.execute(
    "select created_at, action, details from audit_log order by id desc limit 8"
):
    print(" ", row)
