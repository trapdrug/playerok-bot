"""Put a stuck sale back into the queue on the Railway instance."""

from __future__ import annotations

import sqlite3
import sys

DB = "/data/playerok.db"

external_id = sys.argv[1] if len(sys.argv) > 1 else None

connection = sqlite3.connect(DB)
if external_id:
    connection.execute(
        "update sales set status='detected', relisted_at=NULL, "
        "relist_attempts=0, error=NULL, lot_url=NULL "
        "where external_id=?",
        (external_id,),
    )
else:
    connection.execute(
        "update sales set status='detected', relisted_at=NULL, "
        "relist_attempts=0, error=NULL, lot_url=NULL "
        "where status in ('relisting', 'relist_uncertain')"
    )
connection.commit()
for row in connection.execute(
    "select external_id, status, boost_state, title, price from sales "
    "where status not in ('baseline', 'relisted', 'boosted', 'notified') "
    "or external_id=?",
    (external_id or "",),
):
    print(row)
