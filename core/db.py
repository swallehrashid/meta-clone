"""SQLite access (WAL mode) and the schema from BUILD_PLAN section 4.

All timestamps stored here are UTC milliseconds (hard rule 3).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
  symbol     TEXT    NOT NULL,
  t_ms       INTEGER NOT NULL,
  bid        REAL    NOT NULL,
  ask        REAL    NOT NULL,
  flags      INTEGER,
  seq        INTEGER NOT NULL,
  PRIMARY KEY (symbol, t_ms, seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS collector_log (
  t_ms INTEGER, symbol TEXT, event TEXT, detail TEXT
);

CREATE TABLE IF NOT EXISTS labels (
  symbol      TEXT, entry_t_ms INTEGER, direction TEXT,
  entry_price REAL, spread_pips REAL,
  pnl_30s REAL, pnl_60s REAL, pnl_120s REAL, pnl_300s REAL,
  mfe_pips REAL, mae_pips REAL,
  t_to_mfe_ms INTEGER, t_to_mae_ms INTEGER,
  fixed_outcome TEXT,
  fixed_pnl_pips REAL, fixed_exit_ms INTEGER,
  status TEXT,
  PRIMARY KEY (symbol, entry_t_ms, direction)
);

CREATE TABLE IF NOT EXISTS features (
  symbol TEXT, t_ms INTEGER, name TEXT, value REAL,
  PRIMARY KEY (symbol, t_ms, name)
);

CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY, symbol TEXT, t_ms INTEGER, direction TEXT,
  note TEXT, peeked INTEGER DEFAULT 0
);

-- Not in the original plan: lets the collector resume exactly where it stopped
-- (last server-time ms seen per symbol, last known server UTC offset).
CREATE TABLE IF NOT EXISTS collector_state (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);

-- Graded outcome of each practice call (kept apart from `labels`: calls are at arbitrary
-- times and must not disturb the labeler's grid cursor). status adds 'ungradable'.
CREATE TABLE IF NOT EXISTS call_results (
  call_id INTEGER PRIMARY KEY,
  entry_price REAL, spread_pips REAL,
  pnl_30s REAL, pnl_60s REAL, pnl_120s REAL, pnl_300s REAL,
  mfe_pips REAL, mae_pips REAL, t_to_mfe_ms INTEGER, t_to_mae_ms INTEGER,
  fixed_outcome TEXT, fixed_pnl_pips REAL, fixed_exit_ms INTEGER,
  status TEXT                                  -- 'pending' | 'final' | 'ungradable'
);

-- Peek is an explicit, logged action (hard rule 2).
CREATE TABLE IF NOT EXISTS peek_log (
  t_ms INTEGER, session_id TEXT, symbols TEXT, event TEXT   -- 'open' | 'close'
);

CREATE INDEX IF NOT EXISTS idx_collector_log_t ON collector_log (t_ms);
"""

# columns of the labels table, in order (shared by the grader that writes it and the API that reads it)
LABEL_COLS = ["symbol", "entry_t_ms", "direction", "entry_price", "spread_pips", "pnl_30s",
              "pnl_60s", "pnl_120s", "pnl_300s", "mfe_pips", "mae_pips", "t_to_mfe_ms",
              "t_to_mae_ms", "fixed_outcome", "fixed_pnl_pips", "fixed_exit_ms", "status"]

TickRow = Tuple[str, int, float, float, Optional[int], int]  # symbol,t_ms,bid,ask,flags,seq


def connect(path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a connection in WAL mode. Pass ':memory:' for tests."""
    path = str(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=check_same_thread)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


# extra columns on `calls` beyond the original plan (added in place on older databases)
_CALLS_EXTRA = {"session_id": "TEXT", "created_ms": "INTEGER", "delay_s": "REAL"}


def _migrate(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(calls)")}
    for col, typ in _CALLS_EXTRA.items():
        if col not in have:
            conn.execute(f"ALTER TABLE calls ADD COLUMN {col} {typ}")


def insert_ticks(conn: sqlite3.Connection, rows: Iterable[TickRow]) -> int:
    """Insert ticks in one transaction, ignoring ones already stored.

    Returns the number of rows actually inserted.
    """
    before = conn.total_changes
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO ticks (symbol, t_ms, bid, ask, flags, seq) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
    return conn.total_changes - before


def last_tick_ms(conn: sqlite3.Connection, symbol: str) -> Optional[int]:
    row = conn.execute("SELECT MAX(t_ms) FROM ticks WHERE symbol=?", (symbol,)).fetchone()
    return row[0]


def log_event(conn: sqlite3.Connection, t_ms: int, symbol: Optional[str], event: str,
              detail: str = "") -> None:
    with conn:
        conn.execute(
            "INSERT INTO collector_log (t_ms, symbol, event, detail) VALUES (?,?,?,?)",
            (t_ms, symbol, event, detail),
        )


def log_peek(conn: sqlite3.Connection, t_ms: int, session_id: str, symbols: str,
             event: str) -> None:
    with conn:
        conn.execute("INSERT INTO peek_log (t_ms, session_id, symbols, event) VALUES (?,?,?,?)",
                     (t_ms, session_id, symbols, event))


def get_state(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM collector_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO collector_state (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def tick_range(conn: sqlite3.Connection, symbol: str):
    """(first, last) tick time in ms for a symbol, or None. Uses the primary-key index, so it is instant on a huge file."""
    lo = conn.execute("SELECT MIN(t_ms) FROM ticks WHERE symbol=?", (symbol,)).fetchone()[0]
    hi = conn.execute("SELECT MAX(t_ms) FROM ticks WHERE symbol=?", (symbol,)).fetchone()[0]
    return None if lo is None else (lo, hi)


def get_digits(conn: sqlite3.Connection, symbol: str) -> int:
    """Price digits for a symbol: recorded by the collector from the broker, else
    inferred from the most decimals seen in stored quotes (never hardcoded per pair)."""
    v = get_state(conn, f"digits:{symbol}")
    if v is not None:
        return int(v)
    best = 0
    for (bid,) in conn.execute(
            "SELECT bid FROM ticks WHERE symbol=? ORDER BY t_ms DESC LIMIT 300", (symbol,)):
        txt = repr(float(bid))
        if "." in txt and "e" not in txt:
            best = max(best, len(txt.split(".")[1]))
    if best == 0:
        raise LookupError(f"digits unknown for {symbol}: no state and no ticks")
    return best
