"""Broker access behind a small interface so the collector never touches MetaTrader5
directly. `MT5Client` is the real thing (Windows only); `collector/sim_client.py` is a
drop-in simulated feed used for tests and for developing on non-Windows machines.

All times crossing this interface are broker SERVER-time milliseconds. Conversion to
UTC happens once, in the collector (hard rule 3).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Protocol, Sequence


@dataclass(frozen=True)
class Tick:
    time_msc: int   # server-time milliseconds
    bid: float
    ask: float
    flags: int = 0


class BrokerClient(Protocol):
    def connect(self, symbols: Sequence[str]) -> None: ...
    def shutdown(self) -> None: ...
    def digits(self, symbol: str) -> int: ...
    def last_tick_server_ms(self, symbol: str) -> Optional[int]: ...
    def ticks_from(self, symbol: str, from_server_ms: int, count: int) -> List[Tick]: ...
    def ticks_range(self, symbol: str, from_server_ms: int, to_server_ms: int) -> List[Tick]: ...


class MT5ConnectionError(RuntimeError):
    pass


def _dt(server_ms: int) -> datetime:
    # The MT5 Python API treats a tz-aware UTC datetime as "server-time seconds as-is".
    # Floor to the second: the collector's (t_ms, seq) de-duplication absorbs the overlap.
    return datetime.fromtimestamp(server_ms // 1000, tz=timezone.utc)


def _to_ticks(arr) -> List[Tick]:
    if arr is None:
        return []
    return [Tick(int(r["time_msc"]), float(r["bid"]), float(r["ask"]), int(r["flags"]))
            for r in arr]


class MT5Client:
    """Real MetaTrader5 client. Requires Windows, the MT5 terminal and `pip install MetaTrader5`."""

    def __init__(self, terminal_path: Optional[str] = None, login: Optional[int] = None,
                 password: Optional[str] = None, server: Optional[str] = None,
                 retries: int = 5, retry_delay_s: float = 3.0):
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as e:  # pragma: no cover - platform dependent
            raise MT5ConnectionError(
                "MetaTrader5 package not available (Windows only). "
                "Use --sim to run against the simulated feed."
            ) from e
        self._mt5 = mt5
        self._kw = {k: v for k, v in dict(path=terminal_path, login=login, password=password,
                                          server=server).items() if v is not None}
        self._retries, self._retry_delay = retries, retry_delay_s
        self._digits: dict[str, int] = {}

    def connect(self, symbols: Sequence[str]) -> None:
        last = None
        for _ in range(self._retries):
            if self._mt5.initialize(**self._kw):
                break
            last = self._mt5.last_error()
            time.sleep(self._retry_delay)
        else:
            raise MT5ConnectionError(f"MT5 initialize failed: {last}")
        for s in symbols:
            if not self._mt5.symbol_select(s, True):
                raise MT5ConnectionError(f"symbol_select({s}) failed: {self._mt5.last_error()} "
                                         "- check the exact broker name / symbol_suffix")
            info = self._mt5.symbol_info(s)
            if info is None:
                raise MT5ConnectionError(f"symbol_info({s}) returned None")
            self._digits[s] = int(info.digits)

    def shutdown(self) -> None:
        self._mt5.shutdown()

    def digits(self, symbol: str) -> int:
        return self._digits[symbol]

    def last_tick_server_ms(self, symbol: str) -> Optional[int]:
        t = self._mt5.symbol_info_tick(symbol)
        return int(t.time_msc) if t is not None else None

    def ticks_from(self, symbol: str, from_server_ms: int, count: int) -> List[Tick]:
        arr = self._mt5.copy_ticks_from(symbol, _dt(from_server_ms), count,
                                        self._mt5.COPY_TICKS_ALL)
        if arr is None:
            code = self._mt5.last_error()
            if code and code[0] != 1:  # 1 == RES_S_OK
                raise MT5ConnectionError(f"copy_ticks_from({symbol}) failed: {code}")
        return _to_ticks(arr)

    def ticks_range(self, symbol: str, from_server_ms: int, to_server_ms: int) -> List[Tick]:
        arr = self._mt5.copy_ticks_range(symbol, _dt(from_server_ms), _dt(to_server_ms + 1000),
                                         self._mt5.COPY_TICKS_ALL)
        if arr is None:
            code = self._mt5.last_error()
            if code and code[0] != 1:
                raise MT5ConnectionError(f"copy_ticks_range({symbol}) failed: {code}")
        # range end is second-floored + 1s above; trim to the requested window exactly
        return [t for t in _to_ticks(arr) if from_server_ms <= t.time_msc <= to_server_ms]
