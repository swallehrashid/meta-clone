"""A safe way to run the engine / bots over a history database.

`open_sandbox(path)` gives an in-memory SQLite connection whose `ticks`, `collector_state` and `labels` are READ-ONLY views
of the history file. The engine's own tables (account, orders, positions, deals, equity) therefore live in memory: a
replay can never modify the ticks file or your real paper account.
"""
from __future__ import annotations

import sqlite3


def open_sandbox(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", uri=True, check_same_thread=False)
    conn.execute(f"ATTACH DATABASE 'file:{path}?mode=ro' AS hist")
    for t in ("ticks", "collector_state", "labels", "features"):
        conn.execute(f"CREATE TEMP VIEW {t} AS SELECT * FROM hist.{t}")
    return conn
