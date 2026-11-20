"""The contract between a trading bot and the lab.

A bot is a function from "what the market looked like up to time t" to "what to do now". Everything it can see
arrives in a `Context`; everything it can do is a returned `Action`. It is never handed the engine, a database
connection, the grader, labels, or Peek data, so it cannot look ahead or cheat (tests/test_bot_architecture.py
enforces the import rules for strategy modules).

    class MyBot(Bot):
        name = "my_bot"
        PARAMS = {"volume": ParamSpec("float", 0.1, min=0.01, max=1.0, help="lots per trade")}
        def decide(self, ctx: Context) -> list[Action]:
            ...

Forward data (training / analysis only)
  A run may be started with `forward_s > 0`. Then `ctx.forward` is a `ForwardView` of the ticks that come AFTER ctx.t_ms (the
  next `forward_s` seconds - e.g. the minute the delayed chart is hiding). Only bots that declare `needs_forward = True`
  are meant to use it (teachers: they show what a perfect-hindsight trader would do). Every order of a forward run is tagged
  `peeked`, so its results can never be mixed up with blind (clean) results. With `forward_s = 0` (the default) `ctx.forward`
  is None and the bot is exactly as blind as before.

Rules for bots
  * `decide` must be a PURE function of the context (and the bot's parameters): no clock, no unseeded randomness,
    no state carried between calls. This is what makes a replay of stored days and the delayed-live run take exactly
    the same decisions, and lets a restarted bot carry on without losing anything. (Randomness: derive it from
    (seed, symbol, ctx.t_ms) - see `stable_random`.)
  * Decisions happen on a grid (`interval_ms`, default every minute of market time) at chart time t: the context
    holds data with timestamp <= t only.
  * A bot manages its own positions (`ctx.positions` lists only trades tagged source="bot").
"""
from __future__ import annotations

import random
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple

from core.candles import Candle, build_candles, tf_ms
from core.config import Config
from core.dataview import DataView, TickKey, TickRow
from grader.features import compute_features


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()      # "leave unchanged" in Modify (None means "clear")


# ----------------------------------------------------------------------------- actions (what a bot can do)
@dataclass(frozen=True)
class Open:
    """Open a position. SL / TP either as absolute prices or as pips from the exit-side price (not both)."""
    symbol: str
    side: str                              # 'buy' | 'sell'
    volume: float
    type: str = "market"                   # 'market' | 'limit' | 'stop'
    price: Optional[float] = None          # pending orders only
    sl: Optional[float] = None
    tp: Optional[float] = None
    sl_pips: Optional[float] = None
    tp_pips: Optional[float] = None
    comment: str = ""


@dataclass(frozen=True)
class Close:
    position_id: int
    volume: Optional[float] = None         # None = all


@dataclass(frozen=True)
class Modify:
    position_id: int
    sl: Any = UNSET                        # price, None to clear, UNSET to keep
    tp: Any = UNSET


@dataclass(frozen=True)
class CancelOrder:
    order_id: int


@dataclass(frozen=True)
class CloseAll:
    symbol: Optional[str] = None


Action = (Open, Close, Modify, CancelOrder, CloseAll)


# ----------------------------------------------------------------------------- what a bot can see
@dataclass(frozen=True)
class Quote:
    t_ms: int
    bid: float
    ask: float
    spread_pips: float


@dataclass(frozen=True)
class PositionInfo:
    id: int
    symbol: str
    side: str
    volume: float
    open_price: float
    open_ms: int
    sl: Optional[float]
    tp: Optional[float]
    price: Optional[float]                 # exit-side price now
    pips: Optional[float]                  # unrealised, gross of commission
    profit: Optional[float]                # unrealised USD


@dataclass(frozen=True)
class OrderInfo:
    id: int
    symbol: str
    side: str
    type: str
    volume: float
    price: Optional[float]
    sl: Optional[float]
    tp: Optional[float]


@dataclass(frozen=True)
class AccountInfo:
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: Optional[float]
    unrealized: float


class GuardedView:
    """A DataView with nothing but its public read methods: no connection handle, no way to widen the cutoff."""
    __slots__ = ("_dv",)

    def __init__(self, dv: DataView):
        object.__setattr__(self, "_dv", dv)

    def __setattr__(self, k, v):
        raise AttributeError("views are read-only")

    @property
    def symbol(self) -> str:
        return self._dv.symbol

    @property
    def cutoff_ms(self) -> int:
        return self._dv.cutoff_ms

    def ticks(self, start_ms: int, end_ms: Optional[int] = None) -> List[TickRow]:
        return self._dv.ticks(start_ms, end_ms)

    def ticks_after(self, key: Optional[TickKey], limit: int = 5000) -> List[TickRow]:
        return self._dv.ticks_after(key, limit)

    def last_tick(self) -> Optional[TickRow]:
        return self._dv.last_tick()

    def count_ticks(self, start_ms: int, end_ms: Optional[int] = None) -> int:
        return self._dv.count_ticks(start_ms, end_ms)

    def bid_extremes(self, start_ms: int, end_ms: Optional[int] = None) -> Optional[Tuple[float, float]]:
        return self._dv.bid_extremes(start_ms, end_ms)

    def candles(self, tf: str, n: int) -> List[Candle]:
        return self._dv.candles(tf, n)


class ForwardView:
    """What happens in the NEXT `horizon_s` seconds after the decision time (strictly later ticks only). Read-only.

    Built by the runner only when a run asks for forward data. `complete` is False when the window is cut short (it runs past
    the live edge, or the market closes in it): a teacher should then stand aside rather than learn from half a minute.
    """
    __slots__ = ("_ticks", "_side", "t_ms", "horizon_s", "complete")

    def __init__(self, ticks: List[TickRow], price_side: str, t_ms: int, horizon_s: int, complete: bool):
        object.__setattr__(self, "_ticks", list(ticks))
        object.__setattr__(self, "_side", price_side)
        object.__setattr__(self, "t_ms", t_ms)
        object.__setattr__(self, "horizon_s", horizon_s)
        object.__setattr__(self, "complete", complete)

    def __setattr__(self, k, v):
        raise AttributeError("views are read-only")

    def ticks(self, within_s: Optional[float] = None) -> List[TickRow]:
        """Ticks in (t, t + within_s] (default: the whole window), oldest first."""
        if within_s is None:
            return list(self._ticks)
        end = self.t_ms + int(within_s * 1000)
        return [r for r in self._ticks if r[0] <= end]

    def quote_at(self, seconds: float) -> Optional[Tuple[float, float]]:
        """(bid, ask) of the newest tick at or before t + seconds, or None if no tick arrived by then."""
        end = self.t_ms + int(seconds * 1000)
        last = None
        for r in self._ticks:
            if r[0] > end:
                break
            last = r
        return None if last is None else (last[2], last[3])

    def extremes(self, within_s: Optional[float] = None) -> Optional[Tuple[float, float, float, float]]:
        """(lowest bid, highest bid, lowest ask, highest ask) in the window, or None without ticks."""
        rows = self.ticks(within_s)
        if not rows:
            return None
        bids, asks = [r[2] for r in rows], [r[3] for r in rows]
        return min(bids), max(bids), min(asks), max(asks)

    def candles(self, tf: str = "M1") -> List[Candle]:
        """Complete candles that open at or after t and close inside the window (built from forward ticks only)."""
        m = tf_ms(tf)
        end = self.t_ms + self.horizon_s * 1000
        return [c for c in build_candles(self._ticks, tf, self._side) if c.t_ms >= self.t_ms and c.t_ms + m <= end + 1]

    def next_candle(self, tf: str = "M1") -> Optional[Candle]:
        """The next complete candle of this timeframe - e.g. the minute that the delayed chart has not drawn yet."""
        c = self.candles(tf)
        return c[0] if c else None


@dataclass
class Context:
    """Everything a bot may know at decision time `t_ms`. Built fresh for every decision."""
    t_ms: int
    symbols: Tuple[str, ...]
    account: AccountInfo
    positions: Tuple[PositionInfo, ...]          # this bot's open positions only
    orders: Tuple[OrderInfo, ...]                # this bot's working pending orders
    cfg: Config
    _views: Dict[str, GuardedView] = field(repr=False, default_factory=dict)
    _digits: Dict[str, int] = field(repr=False, default_factory=dict)
    _quotes: Dict[str, Optional[Quote]] = field(repr=False, default_factory=dict)
    forward_s: int = 0                                            # 0 = blind (the normal case)
    _forward: Dict[str, "ForwardView"] = field(repr=False, default_factory=dict)

    def forward(self, symbol: str) -> Optional["ForwardView"]:
        """The next `forward_s` seconds of this symbol, or None when this run is blind. Training / analysis runs only."""
        return self._forward.get(symbol)

    def view(self, symbol: str) -> GuardedView:
        return self._views[symbol]

    def quote(self, symbol: str) -> Optional[Quote]:
        """Newest quote at or before t_ms (None if there is none)."""
        return self._quotes.get(symbol)

    def digits(self, symbol: str) -> int:
        return self._digits[symbol]

    def pip(self, symbol: str) -> float:
        d = self._digits[symbol]
        return 10.0 ** -(d - 1 if d in (3, 5) else d)

    def features(self, symbol: str) -> Dict[str, float]:
        """The starter feature set (grader/features.py) at t_ms, computed from this symbol's view only."""
        return compute_features(self._views[symbol]._dv, self.cfg, self._digits[symbol])

    def my_positions(self, symbol: str) -> List[PositionInfo]:
        return [p for p in self.positions if p.symbol == symbol]

    def has_exposure(self, symbol: str) -> bool:
        return bool(self.my_positions(symbol)) or any(o.symbol == symbol for o in self.orders)


# ----------------------------------------------------------------------------- bots
@dataclass(frozen=True)
class ParamSpec:
    type: str                                    # 'float' | 'int' | 'bool' | 'choice' | 'str'
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    choices: Tuple[str, ...] = ()
    help: str = ""

    def coerce(self, name: str, value: Any) -> Any:
        try:
            if self.type == "bool":
                if isinstance(value, str):
                    return value.lower() in ("1", "true", "yes", "on")
                return bool(value)
            if self.type == "choice":
                if value not in self.choices:
                    raise ValueError(f"must be one of {list(self.choices)}")
                return value
            if self.type == "str":
                return str(value)[:120]
            v = int(value) if self.type == "int" else float(value)
            if self.type == "int" and float(value) != v:
                raise ValueError("must be a whole number")
        except (TypeError, ValueError) as e:
            raise ValueError(f"parameter {name!r}: {e}") from None
        if self.min is not None and v < self.min or self.max is not None and v > self.max:
            raise ValueError(f"parameter {name!r} must be between {self.min} and {self.max}")
        return v

    def describe(self) -> dict:
        return {"type": self.type, "default": self.default, "min": self.min, "max": self.max,
                "choices": list(self.choices), "help": self.help}


class Bot(ABC):
    name: ClassVar[str] = "bot"
    description: ClassVar[str] = ""
    PARAMS: ClassVar[Dict[str, ParamSpec]] = {}
    interval_ms: ClassVar[int] = 60_000
    needs_forward: ClassVar[bool] = False        # True = a teacher: refuses to run without forward data

    def __init__(self, **params: Any):
        unknown = set(params) - set(self.PARAMS)
        if unknown:
            raise ValueError(f"unknown parameter(s) for {self.name}: {sorted(unknown)}")
        self.params: Dict[str, Any] = {k: spec.coerce(k, params.get(k, spec.default)) for k, spec in self.PARAMS.items()}

    @abstractmethod
    def decide(self, ctx: Context) -> Sequence[Any]:
        """Return the actions to take at ctx.t_ms (possibly none). Must be a pure function of (ctx, params)."""

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "description": cls.description, "interval_ms": cls.interval_ms, "needs_forward": cls.needs_forward,
                "params": {k: s.describe() for k, s in cls.PARAMS.items()}}


def stable_random(seed: int, symbol: str, t_ms: int, salt: str = "") -> random.Random:
    """A random generator that is a pure function of (seed, symbol, time): the same decision in a replay and live."""
    return random.Random(zlib.crc32(f"{seed}:{symbol}:{t_ms}:{salt}".encode()))
