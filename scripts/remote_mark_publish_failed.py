"""Mark a sale for the publication retry (inside the Railway container)."""

from __future__ import annotations

import sqlite3
import sys

DB = "/data/playerok.db"

external_id = sys.argv[1] if len(sys.argv) > 1 else None

connection = sqlite3.connect(DB)
if external_id:
    connection.execute(
        "update sales set boost_state='publish_failed' where external_id=?",
        (external_id,),
    )
else:
    connection.execute(
        "update sales set boost_state='publish_failed' "
        "where status='relisted' and lot_url is not null"
    )
connection.commit()
for row in connection.execute(
    "select external_id, status, boost_state, title, lot_url from sales "
    "where status='relisted' or boost_state='publish_failed'"
):
    print(row)
