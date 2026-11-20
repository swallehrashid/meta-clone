"""DataView: the ONLY way decision/display code reads market data (hard rule 1 and 2).

A DataView is pinned to (symbol, cutoff_ms). It returns ticks and candles with
timestamp <= cutoff and raises LookaheadError for anything later. The grader and the
peek view are the only modules allowed to read past a cutoff, and they live apart.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional, Tuple

from core.candles import (Candle, CandleCache, build_candles, tf_ms)

TickRow = Tuple[int, int, float, float]   # (t_ms, seq, bid, ask)
TickKey = Tuple[int, int]                 # (t_ms, seq)

_MAX_LOOKBACK_MS = 30 * 24 * 3_600_000    # never scan further back than 30 days for a backfill


class LookaheadError(Exception):
    """Raised when data later than the view's cutoff is requested."""


class DataView:
    def __init__(self, conn: sqlite3.Connection, symbol: str, cutoff_ms: int,
                 cache: Optional[CandleCache] = None, price_side: str = "bid"):
        self._conn, self.symbol, self.cutoff_ms = conn, symbol, int(cutoff_ms)
        self._cache = cache if cache is not None else CandleCache()
        self._side = price_side

    # ---------------------------------------------------------------- guards
    def _check(self, t_ms: int, what: str) -> None:
        if t_ms > self.cutoff_ms:
            raise LookaheadError(
                f"{what}: {t_ms} is after cutoff {self.cutoff_ms} ({self.symbol})")

    def with_cutoff(self, cutoff_ms: int) -> "DataView":
        """A view that sees the same or LESS data. Widening is refused."""
        self._check(cutoff_ms, "with_cutoff")
        return DataView(self._conn, self.symbol, cutoff_ms, self._cache, self._side)

    # ---------------------------------------------------------------- ticks
    def ticks(self, start_ms: int, end_ms: Optional[int] = None) -> List[TickRow]:
        """Ticks with start_ms <= t <= end_ms (default: the cutoff)."""
        end = self.cutoff_ms if end_ms is None else end_ms
        self._check(start_ms, "ticks(start)")
        self._check(end, "ticks(end)")
        return self._conn.execute(
            "SELECT t_ms, seq, bid, ask FROM ticks WHERE symbol=? AND t_ms BETWEEN ? AND ? "
            "ORDER BY t_ms, seq", (self.symbol, start_ms, end)).fetchall()

    def ticks_after(self, key: Optional[TickKey], limit: int = 5000) -> List[TickRow]:
        """Ticks strictly after `key` (t_ms, seq) up to the cutoff, oldest first.
        key=None starts from the beginning of stored data."""
        if key is not None:
            self._check(key[0], "ticks_after(key)")
            t, s = key
            # Row-value comparison lets SQLite do a primary-key RANGE scan. The equivalent
            # "t_ms > ? OR (t_ms = ? AND seq > ?)" defeats the index and scans the symbol's whole history
            # (0.7 s vs 0.1 ms on 14M rows) - invisible on small test databases.
            return self._conn.execute(
                "SELECT t_ms, seq, bid, ask FROM ticks WHERE symbol=? AND (t_ms, seq) > (?, ?) AND t_ms <= ? "
                "ORDER BY t_ms, seq LIMIT ?", (self.symbol, t, s, self.cutoff_ms, limit)
            ).fetchall()
        return self._conn.execute(
            "SELECT t_ms, seq, bid, ask FROM ticks WHERE symbol=? AND t_ms <= ? "
            "ORDER BY t_ms, seq LIMIT ?", (self.symbol, self.cutoff_ms, limit)).fetchall()

    def count_ticks(self, start_ms: int, end_ms: Optional[int] = None) -> int:
        end = self.cutoff_ms if end_ms is None else end_ms
        self._check(start_ms, "count_ticks(start)")
        self._check(end, "count_ticks(end)")
        return self._conn.execute(
            "SELECT COUNT(*) FROM ticks WHERE symbol=? AND t_ms BETWEEN ? AND ?",
            (self.symbol, start_ms, end)).fetchone()[0]

    def bid_extremes(self, start_ms: int, end_ms: Optional[int] = None
                     ) -> Optional[Tuple[float, float]]:
        """(lowest bid, highest bid) over start_ms..end_ms, or None if no ticks."""
        end = self.cutoff_ms if end_ms is None else end_ms
        self._check(start_ms, "bid_extremes(start)")
        self._check(end, "bid_extremes(end)")
        lo, hi = self._conn.execute(
            "SELECT MIN(bid), MAX(bid) FROM ticks WHERE symbol=? AND t_ms BETWEEN ? AND ?",
            (self.symbol, start_ms, end)).fetchone()
        return None if lo is None else (lo, hi)

    def last_tick(self) -> Optional[TickRow]:
        return self._conn.execute(
            "SELECT t_ms, seq, bid, ask FROM ticks WHERE symbol=? AND t_ms <= ? "
            "ORDER BY t_ms DESC, seq DESC LIMIT 1", (self.symbol, self.cutoff_ms)).fetchone()

    def last_tick_key(self) -> Optional[TickKey]:
        t = self.last_tick()
        return (t[0], t[1]) if t else None

    # ---------------------------------------------------------------- candles
    def _latest_db_tick(self) -> Optional[int]:
        r = self._conn.execute("SELECT MAX(t_ms) FROM ticks WHERE symbol=?", (self.symbol,)).fetchone()
        return r[0]

    def candles(self, tf: str, n: int, copy: bool = True) -> List[Candle]:
        """The last `n` candles (fewer if history is short) with open <= cutoff,
        including the forming one. Built only from ticks <= cutoff.

        copy=False hands out the cached objects themselves (fast, for READ-ONLY callers such as
        features); the default returns copies so callers can never corrupt the cache."""
        m = tf_ms(tf)
        hi_open = self.cutoff_ms - self.cutoff_ms % m
        latest = self._latest_db_tick()
        if latest is None:
            return []
        first_row = self._conn.execute("SELECT MIN(t_ms) FROM ticks WHERE symbol=?",
                                       (self.symbol,)).fetchone()
        floor_open = first_row[0] - first_row[0] % m
        # A bucket may be cached once it is complete AS SEEN FROM THIS VIEW and the
        # database already holds a later tick (so no more ticks can land in it).
        settled_end = min(self.cutoff_ms + 1, latest - 1000)

        span = n
        while True:
            lo_open = max(hi_open - (span - 1) * m, floor_open)
            out = self._candles_between(tf, lo_open, hi_open, settled_end, copy)
            if len(out) >= n or lo_open <= floor_open or hi_open - lo_open >= max(_MAX_LOOKBACK_MS, min(n * m, 400 * 86_400_000)):
                return out[-n:]
            span *= 2

    def _candles_between(self, tf: str, lo_open: int, hi_open: int, settled_end: int,
                         copy: bool = True) -> List[Candle]:
        m = tf_ms(tf)
        # last bucket whose end is <= settled_end can be cached
        last_settled = ((settled_end - m) // m) * m if settled_end >= m else lo_open - m
        last_settled = min(last_settled, hi_open)
        out: List[Candle] = []
        with self._cache.lock:
            cache = self._cache.get(self.symbol, tf)
            if last_settled >= lo_open:
                # find runs of buckets missing from the cache and fill each with one query
                b = lo_open
                while b <= last_settled:
                    if b in cache:
                        b += m
                        continue
                    run_start = b
                    while b <= last_settled and b not in cache:
                        b += m
                    run_end = b  # exclusive
                    assert run_end - 1 <= self.cutoff_ms, "cache fill would read past cutoff"
                    ticks = self._conn.execute(
                        "SELECT t_ms, seq, bid, ask FROM ticks WHERE symbol=? AND t_ms >= ? "
                        "AND t_ms < ? ORDER BY t_ms, seq", (self.symbol, run_start, run_end)
                    ).fetchall()
                    for c in build_candles(ticks, tf, self._side):
                        cache[c.t_ms] = c
                    for x in range(run_start, run_end, m):
                        cache.setdefault(x, None)
                for x in range(lo_open, last_settled + 1, m):
                    c = cache.get(x)
                    if c is not None:
                        out.append(c)
                self._cache.trim(self.symbol, tf)
        tail_lo = max(last_settled + m, lo_open)
        if tail_lo <= hi_open:
            ticks = self.ticks(tail_lo, self.cutoff_ms)
            out.extend(build_candles(ticks, tf, self._side))
        if not copy:
            return out
        from dataclasses import replace         # copies, so callers can never mutate the cache
        return [replace(c) for c in out]
