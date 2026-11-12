"""Resume the bot inside the Railway container (clear the stop flag)."""

from __future__ import annotations

import sqlite3

DB = "/data/playerok.db"

connection = sqlite3.connect(DB)
connection.execute(
    "update bot_settings set stopped=0, stop_reason=NULL, stopped_at=NULL"
)
connection.commit()
print(
    list(
        connection.execute(
            "select stopped, stop_reason, auto_relist_enabled, auto_boost_enabled, "
            "dry_run, daily_boost_limit, max_boost_amount from bot_settings"
        )
    )
)
