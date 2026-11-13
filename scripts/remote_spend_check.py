"""Show the recorded boost spend of the Railway instance."""

from __future__ import annotations

import sqlite3

connection = sqlite3.connect("/data/playerok.db")
print("--- дневной расход ---")
for row in connection.execute(
    "select day, amount from daily_boost_spend order by day desc limit 5"
):
    print("   ", row)
print("--- покупки премиума ---")
for row in connection.execute(
    "select created_at, action, details from audit_log "
    "where action like 'boost_paid%' order by id desc limit 10"
):
    print("   ", row)
print("--- продажи со статусом boosted ---")
for row in connection.execute(
    "select external_id, status, boost_state, title, price from sales "
    "where boost_state = 'boosted' order by id desc limit 10"
):
    print("   ", row)
