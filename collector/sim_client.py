"""Deterministic simulated broker feed (same interface as MT5Client).

Ticks are a pure function of (symbol, second), so history and "live" data are always
consistent and re-fetching a window returns identical ticks - exactly the property the
collector's de-duplication relies on with real MT5. Several ticks can share one
millisecond on purpose, to exercise the (t_ms, seq) key.

Not market data. For plumbing tests and developing on non-Windows machines only.
"""
from __future__ import annotations

import math
import random
import zlib
from typing import Callable, Dict, List, Optional, Sequence

from collector.market_hours import market_open
from collector.mt5_client import Tick
from collector.time_offset import MS_PER_HOUR

_BASE = {"EURUSD": (1.0850, 5), "GBPUSD": (1.2700, 5), "EURJPY": (162.50, 3),
         "GBPJPY": (190.40, 3)}


class SimClient:
    def __init__(self, now_utc_ms: Callable[[], int], offset_hours: int = 3,
                 seed: int = 7, max_ticks_per_second: int = 6,
                 offset_hours_fn: Optional[Callable[[int], int]] = None):
        self._now = now_utc_ms
        self._offset_fn = offset_hours_fn or (lambda utc_ms: offset_hours)
        self._seed = seed
        self._max_tps = max_ticks_per_second
        self.connected = False
        self.fail_next = 0  # tests: raise on the next N data calls to simulate disconnects
        self._symbols: List[str] = []

    # ---- BrokerClient ----
    def connect(self, symbols: Sequence[str]) -> None:
        self._symbols = list(symbols)
        self.connected = True

    def shutdown(self) -> None:
        self.connected = False

    def digits(self, symbol: str) -> int:
        return _BASE.get(symbol.rstrip("m"), (1.0, 5))[1]

    def last_tick_server_ms(self, symbol: str) -> Optional[int]:
        now_srv = self._server_now()
        for s in range(now_srv // 1000, now_srv // 1000 - 600, -1):
            ticks = [t for t in self._second(symbol, s) if t.time_msc <= now_srv]
            if ticks:
                return ticks[-1].time_msc
        return None

    def ticks_from(self, symbol: str, from_server_ms: int, count: int) -> List[Tick]:
        self._maybe_fail()
        now_srv = self._server_now()
        out: List[Tick] = []
        s = from_server_ms // 1000  # like MT5: second granularity on the request
        while s * 1000 <= now_srv and len(out) < count:
            out.extend(t for t in self._second(symbol, s) if t.time_msc <= now_srv)
            s += 1
        return out[:count]

    def ticks_range(self, symbol: str, from_server_ms: int, to_server_ms: int) -> List[Tick]:
        self._maybe_fail()
        now_srv = self._server_now()
        hi = min(to_server_ms, now_srv)
        out: List[Tick] = []
        for s in range(from_server_ms // 1000, hi // 1000 + 1):
            out.extend(t for t in self._second(symbol, s)
                       if from_server_ms <= t.time_msc <= hi)
        return out

    # ---- internals ----
    def _server_now(self) -> int:
        utc = self._now()
        return utc + self._offset_fn(utc) * MS_PER_HOUR

    def _maybe_fail(self) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError("simulated MT5 disconnect")

    def _second(self, symbol: str, server_s: int) -> List[Tick]:
        # server second -> approximate UTC (exact except within an hour of a DST switch)
        utc_ms = server_s * 1000 - self._offset_fn(server_s * 1000) * MS_PER_HOUR
        if not market_open(utc_ms):
            return []
        key = zlib.crc32(f"{self._seed}:{symbol}:{server_s}".encode())
        rng = random.Random(key)
        n = rng.randint(0, self._max_tps)
        if n == 0:
            return []
        base, digits = _BASE.get(symbol.rstrip("m"), (1.0, 5))
        # a few ticks deliberately collide on the same millisecond
        ms = sorted(rng.choice(range(0, 1000, 7)) if rng.random() < 0.35 else rng.randrange(1000)
                    for _ in range(n))
        pip = 10 ** -(digits - 1)
        out = []
        for m in ms:
            mid = (base + 25 * pip * math.sin(server_s / 600.0)
                   + 6 * pip * math.sin(server_s / 37.0) + rng.uniform(-1.5, 1.5) * pip)
            spread = rng.choice([0.6, 0.8, 1.0, 1.2, 1.6]) * pip
            bid = round(mid - spread / 2, digits)
            ask = round(mid + spread / 2, digits)
            out.append(Tick(server_s * 1000 + m, bid, ask, 134 if rng.random() < .5 else 6))
        return out
