"""Resume the bot inside the Railway container (clear the stop flag, restore flags)."""

from __future__ import annotations

import sqlite3

DB = "/data/playerok.db"

connection = sqlite3.connect(DB)
connection.execute(
    "update bot_settings set stopped=0, stop_reason=NULL, stopped_at=NULL, "
    "auto_relist_enabled=1, auto_boost_enabled=1"
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
