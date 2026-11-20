"""Loads graded labels joined with their entry-time features, with the report filters applied.

Only `status = 'final'` labels are used (a pending label has no outcome yet). Times are UTC
ms in the database; the time-of-day columns here are in the DISPLAY timezone (DST-aware).
This module reads the labels/features tables only - never raw ticks.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.config import Config

HORIZON_COLS = {30: "pnl_30s", 60: "pnl_60s", 120: "pnl_120s", 300: "pnl_300s"}
FEATURE_PREFIX = "feat:"   # feature columns are namespaced so they can never collide with label columns
LABEL_SQL_COLS = ["symbol", "entry_t_ms", "direction", "spread_pips", "pnl_30s", "pnl_60s", "pnl_120s",
                  "pnl_300s", "mfe_pips", "mae_pips", "t_to_mfe_ms", "t_to_mae_ms", "fixed_outcome",
                  "fixed_pnl_pips", "fixed_exit_ms"]


@dataclass
class Filters:
    symbols: Optional[List[str]] = None          # broker names; None = all
    t_from_ms: Optional[int] = None              # inclusive, UTC ms
    t_to_ms: Optional[int] = None                # inclusive, UTC ms
    session: Optional[Tuple[int, int]] = None    # (start_min, end_min) of the day, display tz; end exclusive
    max_spread_pips: Optional[float] = None      # fill-tick spread of the label
    direction: Optional[str] = None              # 'buy' | 'sell' | None = both

    def describe(self, tz: str) -> dict:
        def fmt(ms):
            return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M %Z")
        hhmm = lambda m: f"{m // 60:02d}:{m % 60:02d}"
        return {"symbols": self.symbols, "from": fmt(self.t_from_ms), "to": fmt(self.t_to_ms),
                "from_ms": self.t_from_ms, "to_ms": self.t_to_ms,
                "session": None if self.session is None else f"{hhmm(self.session[0])}-{hhmm(self.session[1])} {tz}",
                "max_spread_pips": self.max_spread_pips, "direction": self.direction}


# ----------------------------------------------------------------------------- parsing helpers
def parse_session(text: Optional[str], cfg: Config) -> Optional[Tuple[int, int]]:
    """'08:00-11:00' (display tz) or 'ny' (= sessions.ny_open .. sessions.ny_session_end). None = all day."""
    if not text:
        return None
    if text.lower() == "ny":
        text = f"{cfg.sessions.ny_open}-{cfg.sessions.ny_session_end}"
    try:
        a, b = text.split("-")
        h1, m1 = (int(x) for x in a.split(":")); h2, m2 = (int(x) for x in b.split(":"))
        s, e = h1 * 60 + m1, h2 * 60 + m2
    except ValueError:
        raise ValueError(f"bad session {text!r}; use HH:MM-HH:MM or 'ny'") from None
    if not (0 <= s < 1440 and 0 <= e <= 1440) or s == e:
        raise ValueError(f"bad session {text!r}")
    return s, (0 if e == 1440 else e)


def day_start_ms(d: date, tz: str) -> int:
    return int(datetime.combine(d, dtime(0, 0), tzinfo=ZoneInfo(tz)).astimezone(timezone.utc).timestamp() * 1000)


def date_range_ms(date_from: Optional[str], date_to: Optional[str], tz: str) -> Tuple[Optional[int], Optional[int]]:
    """Calendar dates in `tz`; `date_to` is INCLUSIVE (through the end of that day)."""
    lo = day_start_ms(date.fromisoformat(date_from), tz) if date_from else None
    hi = (day_start_ms(date.fromisoformat(date_to) + timedelta(days=1), tz) - 1) if date_to else None
    return lo, hi


# ----------------------------------------------------------------------------- loading
def load_frame(conn: sqlite3.Connection, cfg: Config, f: Filters) -> pd.DataFrame:
    """Final labels joined with features, filtered. One row per (symbol, entry time, direction)."""
    where, args = ["status = 'final'"], []
    if f.symbols:
        where.append(f"symbol IN ({','.join('?' * len(f.symbols))})"); args += f.symbols
    if f.t_from_ms is not None:
        where.append("entry_t_ms >= ?"); args.append(f.t_from_ms)
    if f.t_to_ms is not None:
        where.append("entry_t_ms <= ?"); args.append(f.t_to_ms)
    if f.max_spread_pips is not None:
        where.append("spread_pips <= ?"); args.append(f.max_spread_pips)
    if f.direction:
        where.append("direction = ?"); args.append(f.direction)
    df = pd.read_sql_query(f"SELECT {','.join(LABEL_SQL_COLS)} FROM labels WHERE {' AND '.join(where)}",
                           conn, params=args)
    if df.empty:
        return _with_time_cols(df, cfg)

    fw, fa = [], []
    if f.symbols:
        fw.append(f"symbol IN ({','.join('?' * len(f.symbols))})"); fa += f.symbols
    if f.t_from_ms is not None:
        fw.append("t_ms >= ?"); fa.append(f.t_from_ms)
    if f.t_to_ms is not None:
        fw.append("t_ms <= ?"); fa.append(f.t_to_ms)
    feats = pd.read_sql_query(
        "SELECT symbol, t_ms AS entry_t_ms, name, value FROM features" + (" WHERE " + " AND ".join(fw) if fw else ""),
        conn, params=fa)
    if not feats.empty:
        wide = feats.pivot_table(index=["symbol", "entry_t_ms"], columns="name", values="value", aggfunc="first")
        wide.columns = [FEATURE_PREFIX + c for c in wide.columns]
        df = df.merge(wide.reset_index(), on=["symbol", "entry_t_ms"], how="left")
    df = _with_time_cols(df, cfg)
    if f.session is not None:
        s, e = f.session
        m = df["minute_of_day"]
        df = df[(m >= s) & (m < e)] if s < e else df[(m >= s) | (m < e)]   # window may wrap past midnight
    return df.reset_index(drop=True)


def _with_time_cols(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    if df.empty:
        df["hour"] = pd.Series(dtype="int64"); df["minute_of_day"] = pd.Series(dtype="int64")
        return df
    local = pd.to_datetime(df["entry_t_ms"], unit="ms", utc=True).dt.tz_convert(cfg.display_timezone)
    df["hour"] = local.dt.hour
    df["minute_of_day"] = local.dt.hour * 60 + local.dt.minute
    return df


def feature_columns(df: pd.DataFrame) -> List[str]:
    """Namespaced feature columns present in the frame (display name = column minus FEATURE_PREFIX)."""
    return sorted(c for c in df.columns if c.startswith(FEATURE_PREFIX))
