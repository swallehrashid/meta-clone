"""Terminal settings that the back office controls and the phone-style terminal obeys.

The public terminal has no settings screen: how far behind the chart runs, which pairs are quoted, the default timeframe,
the one-click lot size, whether the Ask line is drawn and the displayed server time zone are all decided here (admin) and
fetched by the terminal. Persisted in SQLite so they survive a restart.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

from core.config import Config

SCHEMA = "CREATE TABLE IF NOT EXISTS lab_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN")


def defaults(cfg: Config) -> Dict[str, Any]:
    return {
        "delay_s": cfg.delay.default_seconds,
        "tf": "M1",
        "volume": 0.10,
        "show_ask_line": True,
        "tz": cfg.display_timezone,
        "symbols": list(cfg.symbols),
        "account_name": "Demo account",
        "server_name": "Lab-Demo",
        "login": 10000001,
    }


def validate(cfg: Config, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Return a cleaned copy of `patch`; raises ValueError naming the first bad field."""
    out: Dict[str, Any] = {}
    known = set(defaults(cfg))
    for k, v in patch.items():
        if k not in known:
            raise ValueError(f"unknown setting {k!r}")
        if k == "delay_s":
            v = float(v)
            if not cfg.delay.min_seconds <= v <= cfg.delay.max_seconds:
                raise ValueError(f"delay_s must be between {cfg.delay.min_seconds} and {cfg.delay.max_seconds}")
            v = int(v) if v == int(v) else v
        elif k == "tf":
            if v not in TIMEFRAMES:
                raise ValueError(f"tf must be one of {list(TIMEFRAMES)}")
        elif k == "volume":
            v = float(v)
            if not cfg.engine.min_volume <= v <= cfg.engine.max_volume:
                raise ValueError(f"volume must be between {cfg.engine.min_volume} and {cfg.engine.max_volume}")
        elif k == "show_ask_line":
            v = bool(v)
        elif k == "tz":
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(str(v))
            except Exception:                                                   # noqa: BLE001
                raise ValueError(f"unknown time zone {v!r}") from None
        elif k == "symbols":
            if not isinstance(v, list) or not v or any(s not in cfg.symbols for s in v):
                raise ValueError(f"symbols must be a non-empty list from {cfg.symbols}")
        elif k == "login":
            v = int(v)
        else:
            v = str(v)[:60]
        out[k] = v
    return out


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def load(cfg: Config, conn: sqlite3.Connection) -> Dict[str, Any]:
    ensure(conn)
    d = defaults(cfg)
    row = conn.execute("SELECT value FROM lab_settings WHERE key='terminal'").fetchone()
    if row:
        try:
            saved = json.loads(row[0])
            d.update({k: v for k, v in saved.items() if k in d})
        except ValueError:
            pass
    return d


def save(cfg: Config, conn: sqlite3.Connection, patch: Dict[str, Any]) -> Dict[str, Any]:
    clean = validate(cfg, patch)
    cur = load(cfg, conn)
    cur.update(clean)
    conn.execute("INSERT INTO lab_settings (key, value) VALUES ('terminal', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (json.dumps(cur),))
    conn.commit()
    return cur
