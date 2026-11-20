"""OHLC candles from ticks.

- Built from the configured price side (bid by default, like MT5 charts).
- Each candle also carries tick count and average spread (price units; use
  `pips.to_pips` to convert).
- Intraday bars align to UTC minute/hour boundaries (epoch-aligned buckets).
- `build_candles`  : batch, numpy.            Used for backfill.
- `CandleBuilder`  : incremental, per tick.   Used for the live forming candle.
- `CandleCache`    : settled candles kept in memory (candles are never stored in the DB).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

TF_MS: Dict[str, int] = {"M1": 60_000, "M5": 300_000, "M15": 900_000, "M30": 1_800_000, "H1": 3_600_000,
                         "H4": 14_400_000, "D1": 86_400_000}     # W1 / MN are calendar periods: built from D1 by the client


def tf_ms(tf: str) -> int:
    try:
        return TF_MS[tf]
    except KeyError:
        raise ValueError(f"unknown timeframe {tf!r}; expected one of {list(TF_MS)}") from None


def bucket_open(t_ms: int, tf: str) -> int:
    m = tf_ms(tf)
    return t_ms - t_ms % m


@dataclass
class Candle:
    t_ms: int        # bucket open, UTC ms
    o: float
    h: float
    l: float
    c: float
    n: int           # tick count
    spread_sum: float  # sum of (ask - bid) over ticks, price units

    @property
    def avg_spread(self) -> float:
        return self.spread_sum / self.n if self.n else 0.0

    def to_dict(self, pip: Optional[float] = None) -> dict:
        d = {"time": self.t_ms // 1000, "open": self.o, "high": self.h, "low": self.l,
             "close": self.c, "ticks": self.n, "avg_spread": self.avg_spread}
        if pip:
            d["avg_spread_pips"] = self.avg_spread / pip
        return d


# A tick row is (t_ms, seq, bid, ask) - the shape DataView returns.
TickRow = Tuple[int, int, float, float]


def build_candles(ticks: Sequence[TickRow], tf: str, side: str = "bid") -> List[Candle]:
    """Batch build. `ticks` must be sorted by (t_ms, seq)."""
    if not ticks:
        return []
    m = tf_ms(tf)
    a = np.asarray(ticks, dtype=np.float64)
    t = a[:, 0].astype(np.int64)
    bid, ask = a[:, 2], a[:, 3]
    px = bid if side == "bid" else ask
    b = t - t % m
    starts = np.concatenate(([0], np.flatnonzero(np.diff(b)) + 1))
    ends = np.concatenate((starts[1:], [len(t)]))
    hi = np.maximum.reduceat(px, starts)
    lo = np.minimum.reduceat(px, starts)
    sp = np.add.reduceat(ask - bid, starts)
    return [Candle(int(b[s]), float(px[s]), float(hi[i]), float(lo[i]), float(px[e - 1]),
                   int(e - s), float(sp[i]))
            for i, (s, e) in enumerate(zip(starts, ends))]


class CandleBuilder:
    """Incremental builder for one (symbol, timeframe): feed ticks in time order."""

    def __init__(self, tf: str, side: str = "bid", seed: Optional[Candle] = None):
        self.tf, self.side, self.m = tf, side, tf_ms(tf)
        self.current: Optional[Candle] = replace(seed) if seed else None

    def add(self, rows: Iterable[TickRow]) -> List[Candle]:
        """Apply ticks. Returns a snapshot of every candle touched, oldest first,
        each in its final state for this batch."""
        touched: Dict[int, Candle] = {}
        for t_ms, _seq, bid, ask in rows:
            px = bid if self.side == "bid" else ask
            b = t_ms - t_ms % self.m
            cur = self.current
            if cur is None or b > cur.t_ms:
                cur = self.current = Candle(b, px, px, px, px, 0, 0.0)
            elif b < cur.t_ms:
                continue  # out-of-order tick for a finished bucket: ignore
            if px > cur.h: cur.h = px
            if px < cur.l: cur.l = px
            cur.c = px
            cur.n += 1
            cur.spread_sum += ask - bid
            touched[cur.t_ms] = cur
        return [replace(c) for _, c in sorted(touched.items())]


class CandleCache:
    """Settled-candle cache keyed (symbol, tf, bucket_open). `None` marks an empty
    bucket (no ticks, e.g. weekend) so it is not recomputed. Thread-safe."""

    def __init__(self, max_per_key: int = 60_000):
        self._d: Dict[Tuple[str, str], Dict[int, Optional[Candle]]] = {}
        self._max = max_per_key
        self.lock = threading.RLock()

    def get(self, symbol: str, tf: str) -> Dict[int, Optional[Candle]]:
        with self.lock:
            return self._d.setdefault((symbol, tf), {})

    def trim(self, symbol: str, tf: str) -> None:
        with self.lock:
            d = self._d.get((symbol, tf))
            if d and len(d) > self._max:
                for k in sorted(d)[: len(d) - self._max]:
                    del d[k]
