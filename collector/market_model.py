"""A deterministic, realistic-looking FX tick generator with NO exploitable structure by construction.

Why it exists: `sim_client.SimClient` mixes in sine waves, so any analysis run on it "finds" patterns
that are artefacts. This model is for exercising the whole lab (history, engine, reports, bots) on months
of data without that trap.

Properties (all checked in tests/test_market_model.py)
  * the mid price is a time-changed Brownian motion: increments are uncorrelated and zero-mean, so a
    strategy cannot earn money from direction - only lose the spread;
  * volatility and tick rate follow a New York intraday profile (quiet Asia, London, NY overlap,
    dead rollover) and cluster from day to day;
  * the four pairs are correlated through two common factors (USD, JPY);
  * spreads widen when liquidity is thin; prices are quantised to the pair's digits; ticks carry
    millisecond times (several can share a millisecond);
  * the market is closed Fri 17:00 -> Sun 17:00 New York (no ticks);
  * everything is a pure function of (seed, symbol, UTC day), so history written in bulk and "live"
    ticks served later are bit-identical.
Construction: day-to-day moves D_d are drawn up front; inside a day the path is a Brownian bridge between
the day's start and end anchors (time-changed by the intraday variance profile), which is unconditionally
a Brownian motion but is continuous across midnight.
Not modelled: swaps, news gaps with fat tails beyond the volatility clustering, order-book depth.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np

from collector.market_hours import market_open
from collector.mt5_client import Tick
from collector.time_offset import MS_PER_HOUR
from core.pips import pip_size

DAY_S, DAY_MS = 86_400, 86_400_000
NY = ZoneInfo("America/New_York")
DAY0 = (date(2019, 1, 1) - date(1970, 1, 1)).days          # first supported UTC day
N_DAYS = 4600                                               # ... through ~2031
REF_DAY = (date(2025, 1, 1) - date(1970, 1, 1)).days        # prices are pinned to SPECS[...].base on this day


@dataclass(frozen=True)
class PairSpec:
    base: float            # price at DAY0
    digits: int
    daily_vol_pips: float  # typical daily standard deviation, in pips
    spread_pips: float     # typical spread in normal conditions
    ticks_per_s: float     # average quote updates per second while open
    usd: float             # loading on the USD factor
    jpy: float             # loading on the JPY factor


SPECS: Dict[str, PairSpec] = {
    "EURUSD": PairSpec(1.0850, 5, 55, 0.8, 1.1, 0.75, 0.15),
    "GBPUSD": PairSpec(1.2700, 5, 65, 1.1, 0.9, 0.75, 0.15),
    "EURJPY": PairSpec(162.50, 3, 90, 1.3, 0.8, 0.25, 0.85),
    "GBPJPY": PairSpec(190.40, 3, 110, 1.8, 0.7, 0.25, 0.85),
}
ORDER = list(SPECS)

# relative variance by New York hour of day, 0..23 (interpolated through the hour)
VOL_NY = np.array([0.30, 0.30, 0.45, 1.00, 1.20, 1.30, 1.30, 1.50, 2.00, 2.20, 2.00, 1.60,
                   1.30, 1.20, 1.00, 0.90, 0.70, 0.15, 0.15, 0.20, 0.30, 0.30, 0.30, 0.30])
SPREAD_NY = np.array([1.6, 1.6, 1.4, 1.0, 1.0, 0.9, 0.9, 0.9, 0.8, 0.8, 0.8, 0.9,
                      0.9, 1.0, 1.0, 1.1, 1.4, 3.0, 2.5, 1.8, 1.6, 1.6, 1.6, 1.6])
# share of a day's variance that is "open", by UTC weekday (Mon=0): Friday closes, Saturday shut, Sunday opens late
OPEN_FRAC = {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0, 4: 0.88, 5: 0.0, 6: 0.12}


def _wrap_interp(table: np.ndarray, hour_float: np.ndarray) -> np.ndarray:
    pts = np.append(table, table[0])
    return np.interp(hour_float % 24.0, np.arange(25), pts)


class MarketModel:
    def __init__(self, seed: int = 1):
        self.seed = seed
        self._day_cache: Dict[int, dict] = {}
        self._tick_cache: Dict[Tuple[str, int], Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        rng = np.random.default_rng([seed, 0])
        z = rng.standard_normal((N_DAYS, 2 + len(ORDER)))                 # usd, jpy, idio per pair (daily moves)
        zv = rng.standard_normal(N_DAYS)
        sx = 0.25                                                          # stationary std of log-vol
        x = np.zeros(N_DAYS); phi = 0.85
        x[0] = sx * zv[0]
        for i in range(1, N_DAYS):
            x[i] = phi * x[i - 1] + sx * np.sqrt(1 - phi ** 2) * zv[i]
        self.vol_mult = np.exp(x - sx ** 2)                                # E[m^2] = 1
        wd = (np.arange(N_DAYS) + DAY0 + 3) % 7                            # Monday = 0
        frac = np.array([OPEN_FRAC[int(w)] for w in wd])
        self._frac = frac
        self.anchors: Dict[str, np.ndarray] = {}
        for j, sym in enumerate(ORDER):
            sp = SPECS[sym]
            pip = pip_size(sp.digits)
            idio = np.sqrt(max(1 - sp.usd ** 2 - sp.jpy ** 2, 0.0))
            zd = sp.usd * z[:, 0] + sp.jpy * z[:, 1] + idio * z[:, 2 + j]
            delta = zd * sp.daily_vol_pips * pip * self.vol_mult * np.sqrt(frac)
            cum = np.concatenate(([0.0], np.cumsum(delta)))
            self.anchors[sym] = sp.base + cum - cum[REF_DAY - DAY0]            # anchor[d] = price at the start of day d
        self._delta = {s: np.diff(a) for s, a in self.anchors.items()}

    # ------------------------------------------------------------------ one UTC day
    def _day(self, d: int) -> dict:
        if d in self._day_cache:
            return self._day_cache[d]
        i = d - DAY0
        if not 0 <= i < N_DAYS:
            raise ValueError(f"day {d} outside the supported range")
        secs = np.arange(DAY_S)
        day_start_ms = d * DAY_MS
        # open mask per second from the minute-level market schedule
        minute_open = np.fromiter((market_open(day_start_ms + m * 60_000) for m in range(1440)), dtype=bool, count=1440)
        open_s = np.repeat(minute_open, 60)
        noon = datetime.fromtimestamp(day_start_ms / 1000 + 43200, tz=timezone.utc).astimezone(NY)
        off_h = noon.utcoffset().total_seconds() / 3600                   # -5 or -4
        ny_hour = ((secs + 0.5) / 3600.0 + off_h) % 24.0
        prof = _wrap_interp(VOL_NY, ny_hour)
        spr = _wrap_interp(SPREAD_NY, ny_hour)
        act = prof ** 0.6
        act = act / act.mean()
        vsec = prof * open_s
        rng = np.random.default_rng([self.seed, 1, d])
        f_usd, f_jpy = rng.standard_normal(DAY_S), rng.standard_normal(DAY_S)
        paths = {}
        tot = vsec.sum()
        for j, sym in enumerate(ORDER):
            sp = SPECS[sym]
            pip = pip_size(sp.digits)
            a0 = self.anchors[sym][i]
            delta = self._delta[sym][i]
            if tot <= 0:                                                   # Saturday: flat
                paths[sym] = np.full(DAY_S + 1, a0)
                continue
            idio = np.sqrt(max(1 - sp.usd ** 2 - sp.jpy ** 2, 0.0))
            noise = sp.usd * f_usd + sp.jpy * f_jpy + idio * rng.standard_normal(DAY_S)
            v_day = (sp.daily_vol_pips * pip * self.vol_mult[i]) ** 2 * self._frac[i]
            incr = np.sqrt(v_day * vsec / tot) * noise
            b = np.cumsum(incr)
            cv = np.cumsum(vsec) / tot                                     # variance clock, 0..1
            bridge = b - b[-1] * cv
            p = np.empty(DAY_S + 1)
            p[0] = a0
            p[1:] = a0 + delta * cv + bridge
            paths[sym] = p
        out = {"open": open_s, "act": act, "spr": spr, "ny_hour": ny_hour, "paths": paths, "start_ms": day_start_ms}
        if len(self._day_cache) > 6:
            self._day_cache.pop(next(iter(self._day_cache)))
        self._day_cache[d] = out
        return out

    # ------------------------------------------------------------------ ticks of one pair on one day
    def day_ticks(self, symbol: str, d: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(t_ms int64, bid, ask) for the UTC day `d`, sorted by time. Pure function of (seed, symbol, d)."""
        key = (symbol, d)
        if key in self._tick_cache:
            return self._tick_cache[key]
        sp = SPECS[symbol]
        day = self._day(d)
        rng = np.random.default_rng([self.seed, 2, d, ORDER.index(symbol)])
        lam = sp.ticks_per_s * day["act"] * day["open"]
        counts = rng.poisson(lam)
        n = int(counts.sum())
        if n == 0:
            res = (np.empty(0, np.int64), np.empty(0), np.empty(0))
        else:
            tsec = np.sort(np.repeat(np.arange(DAY_S), counts) + rng.random(n))
            unit = 10.0 ** -sp.digits
            pip = pip_size(sp.digits)
            mid = np.interp(tsec, np.arange(DAY_S + 1), day["paths"][symbol]) + rng.normal(0, 0.03 * pip, n)
            hour = day["ny_hour"][np.minimum(tsec.astype(int), DAY_S - 1)]
            sprd = sp.spread_pips * pip * _wrap_interp(SPREAD_NY, hour) * np.exp(0.15 * rng.standard_normal(n))
            s_u = np.maximum(1, np.rint(sprd / unit)).astype(np.int64)
            bid_u = np.floor(mid / unit - s_u / 2 + 0.5).astype(np.int64)
            bid = np.round(bid_u * unit, sp.digits)
            ask = np.round((bid_u + s_u) * unit, sp.digits)
            t_ms = day["start_ms"] + np.floor(tsec * 1000).astype(np.int64)
            res = (t_ms, bid, ask)
        if len(self._tick_cache) > 24:
            self._tick_cache.pop(next(iter(self._tick_cache)))
        self._tick_cache[key] = res
        return res

    def ticks_between(self, symbol: str, lo_ms: int, hi_ms: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Ticks with lo_ms <= t <= hi_ms (UTC ms)."""
        parts = []
        for d in range(lo_ms // DAY_MS, hi_ms // DAY_MS + 1):
            t, b, a = self.day_ticks(symbol, d)
            l, r = np.searchsorted(t, lo_ms, "left"), np.searchsorted(t, hi_ms, "right")
            if r > l:
                parts.append((t[l:r], b[l:r], a[l:r]))
        if not parts:
            return np.empty(0, np.int64), np.empty(0), np.empty(0)
        return tuple(np.concatenate(x) for x in zip(*parts))        # type: ignore[return-value]


def seq_within_ms(t_ms: np.ndarray) -> np.ndarray:
    """Position of each tick inside its millisecond (the `seq` of the ticks table)."""
    if len(t_ms) == 0:
        return np.empty(0, np.int64)
    idx = np.arange(len(t_ms))
    new = np.r_[True, t_ms[1:] != t_ms[:-1]]
    return idx - np.maximum.accumulate(np.where(new, idx, 0))


class MarketSimClient:
    """BrokerClient over MarketModel: serves exactly the ticks a bulk history run would have written."""

    def __init__(self, now_utc_ms, offset_hours: int = 3, seed: int = 1, model: Optional[MarketModel] = None):
        self._now = now_utc_ms
        self.off = offset_hours
        self.model = model or MarketModel(seed)
        self.connected = False
        self.fail_next = 0

    def connect(self, symbols: Sequence[str]) -> None:
        bad = [s for s in symbols if s not in SPECS]
        if bad:
            raise ValueError(f"market model has no pair {bad}; available: {ORDER}")
        self.connected = True

    def shutdown(self) -> None:
        self.connected = False

    def digits(self, symbol: str) -> int:
        return SPECS[symbol].digits

    def _maybe_fail(self) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError("simulated disconnect")

    def _ticks(self, symbol: str, lo_srv: int, hi_srv: int) -> List[Tick]:
        off = self.off * MS_PER_HOUR
        hi = min(hi_srv, self._now() + off)
        if hi < lo_srv:
            return []
        t, b, a = self.model.ticks_between(symbol, lo_srv - off, hi - off)
        return [Tick(int(x) + off, float(y), float(z), 0) for x, y, z in zip(t, b, a)]

    def ticks_range(self, symbol: str, from_server_ms: int, to_server_ms: int) -> List[Tick]:
        self._maybe_fail()
        return self._ticks(symbol, from_server_ms, to_server_ms)

    def ticks_from(self, symbol: str, from_server_ms: int, count: int) -> List[Tick]:
        self._maybe_fail()
        out: List[Tick] = []
        lo = from_server_ms // 1000 * 1000                                # like MT5: second granularity
        now_srv = self._now() + self.off * MS_PER_HOUR
        while lo <= now_srv and len(out) < count:
            hi = min(lo + 6 * MS_PER_HOUR, now_srv)
            out.extend(self._ticks(symbol, lo, hi))
            lo = hi + 1
        return out[:count]

    def last_tick_server_ms(self, symbol: str) -> Optional[int]:
        now = self._now()
        for back in range(0, 6):
            lo = now - (back + 1) * DAY_MS
            t, _, _ = self.model.ticks_between(symbol, lo, now - back * DAY_MS)
            if len(t):
                return int(t[-1]) + self.off * MS_PER_HOUR
        return None
