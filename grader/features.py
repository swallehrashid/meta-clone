"""Features at entry time - computed through `DataView(cutoff=entry_t_ms)` ONLY.

A feature is a named function `f(ctx) -> float | None` registered with @feature. `ctx`
exposes the DataView and config, never a raw DB handle, so a feature cannot read past the
entry time. None means "not computable from the data before the entry" (e.g. the previous
minute had no ticks); such values are simply not stored.

Conventions
  * "last M1 candle" = the most recent COMPLETED M1 candle, i.e. the one that opened exactly
    one minute before the minute containing the entry. If that minute had no ticks, the
    candle-based features are None.
  * direction is +1 (close > open), -1 (close < open), 0 (doji).
  * minutes_since_*_open: minutes since the latest such open in the display timezone
    (0..1440), DST-aware.
  * session high/low are over bid ticks since the latest `sessions.high_low_anchor` open,
    up to the entry; distances are measured from the latest bid.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from core import db
from core.candles import Candle, bucket_open, CandleCache
from core.config import Config
from core.dataview import DataView
from core.pips import pip_size

M1 = 60_000
FEATURES: Dict[str, Callable[["Ctx"], Optional[float]]] = {}


def feature(fn):
    FEATURES[fn.__name__] = fn
    return fn


@dataclass
class Ctx:
    dv: DataView
    cfg: Config
    pip: float
    _cache: dict = field(default_factory=dict)

    @property
    def t(self) -> int:
        return self.dv.cutoff_ms

    def memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def last_tick(self):
        return self.memo("last_tick", self.dv.last_tick)

    def completed_m1(self) -> List[Candle]:
        """Up to the last 3 COMPLETED M1 candles (oldest first); may skip empty minutes."""
        def get():
            forming = bucket_open(self.t, "M1")
            return [c for c in self.dv.candles("M1", 4, copy=False) if c.t_ms < forming][-3:]   # read-only use
        return self.memo("m1", get)

    def prev_m1(self, back: int = 1) -> Optional[Candle]:
        """The completed candle that opened exactly `back` minutes before the forming one."""
        want = bucket_open(self.t, "M1") - back * M1
        return next((c for c in self.completed_m1() if c.t_ms == want), None)


# ----------------------------------------------------------------------- time helpers
def _tz(cfg: Config) -> ZoneInfo:
    return ZoneInfo(cfg.display_timezone)


def minutes_since_open(t_ms: int, hhmm: str, tz: ZoneInfo) -> float:
    """Minutes from the latest `hhmm` (local, DST-aware) at or before t_ms."""
    hh, mm = (int(x) for x in hhmm.split(":"))
    local = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).astimezone(tz)
    day = local.date()
    if (local.hour, local.minute) < (hh, mm):
        day -= timedelta(days=1)
    open_utc = datetime.combine(day, dtime(hh, mm), tzinfo=tz).astimezone(timezone.utc)
    return (t_ms - int(open_utc.timestamp() * 1000)) / 60_000


# ----------------------------------------------------------------------- features
@feature
def spread_pips(c: Ctx):
    t = c.last_tick()
    return None if t is None else (t[3] - t[2]) / c.pip


@feature
def minutes_since_ny_open(c: Ctx):
    return minutes_since_open(c.t, c.cfg.sessions.ny_open, _tz(c.cfg))


@feature
def minutes_since_london_open(c: Ctx):
    return minutes_since_open(c.t, c.cfg.sessions.london_open, _tz(c.cfg))


@feature
def last_m1_direction(c: Ctx):
    m = c.prev_m1()
    return None if m is None else float((m.c > m.o) - (m.c < m.o))


@feature
def last_m1_range_pips(c: Ctx):
    m = c.prev_m1()
    return None if m is None else (m.h - m.l) / c.pip


@feature
def last_m1_body_pct(c: Ctx):
    m = c.prev_m1()
    if m is None:
        return None
    rng = m.h - m.l
    return 0.0 if rng == 0 else abs(m.c - m.o) / rng * 100.0


@feature
def m1_closed_above_prev_high(c: Ctx):
    a, b = c.prev_m1(1), c.prev_m1(2)
    return None if a is None or b is None else float(a.c > b.h)


@feature
def m1_closed_below_prev_low(c: Ctx):
    a, b = c.prev_m1(1), c.prev_m1(2)
    return None if a is None or b is None else float(a.c < b.l)


def _session_extremes(c: Ctx):
    def get():
        anchor_hhmm = getattr(c.cfg.sessions, c.cfg.sessions.high_low_anchor)
        start = c.t - int(minutes_since_open(c.t, anchor_hhmm, _tz(c.cfg)) * 60_000)
        # M1 bucket highs/lows are exact when the anchor sits on a minute boundary
        n = int((c.t - start) // M1) + 2
        cs = [x for x in c.dv.candles("M1", n, copy=False) if x.t_ms >= start - start % M1]   # read-only use
        if not cs:
            return None
        return min(x.l for x in cs), max(x.h for x in cs)
    return c.memo("session_hl", get)


@feature
def dist_to_session_high_pips(c: Ctx):
    hl, t = _session_extremes(c), c.last_tick()
    return None if hl is None or t is None else (hl[1] - t[2]) / c.pip


@feature
def dist_to_session_low_pips(c: Ctx):
    hl, t = _session_extremes(c), c.last_tick()
    return None if hl is None or t is None else (t[2] - hl[0]) / c.pip


@feature
def ticks_last_60s(c: Ctx):
    return float(c.dv.count_ticks(c.t - 59_999, c.t))


@feature
def range_last_15m_pips(c: Ctx):
    ext = c.dv.bid_extremes(c.t - 15 * 60_000 + 1, c.t)
    return None if ext is None else (ext[1] - ext[0]) / c.pip


# ----------------------------------------------------------------------- driver
def compute_features(dv: DataView, cfg: Config, digits: int) -> Dict[str, float]:
    """All registered features at dv.cutoff_ms (None results are dropped)."""
    ctx = Ctx(dv, cfg, pip_size(digits))
    out = {}
    for name, fn in FEATURES.items():
        v = fn(ctx)
        if v is not None:
            out[name] = float(v)
    return out


def store_features(conn: sqlite3.Connection, symbol: str, t_ms: int, feats: Dict[str, float]) -> None:
    with conn:
        conn.executemany("INSERT OR REPLACE INTO features (symbol, t_ms, name, value) VALUES (?,?,?,?)",
                         [(symbol, t_ms, k, v) for k, v in feats.items()])
