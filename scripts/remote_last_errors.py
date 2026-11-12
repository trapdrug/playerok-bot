"""Show the latest failures of the Railway instance."""

from __future__ import annotations

import sqlite3

connection = sqlite3.connect("/data/playerok.db")
print("--- последние publish_failed / relist_failed ---")
for row in connection.execute(
    "select created_at, action, details from audit_log "
    "where action in ('publish_failed', 'relist_failed', 'published_without_premium') "
    "order by id desc limit 5"
):
    print(row)
print("--- последний лог ошибок в файле ---")
try:
    with open("/data/bot.log", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    for line in lines[-40:]:
        if "ERROR" in line or "publish" in line.lower() or "priority" in line.lower():
            print(line.strip()[:200])
except Exception as exc:  # noqa: BLE001
    print(exc)
