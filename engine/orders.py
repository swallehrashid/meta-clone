"""Order / position / deal records and input validation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from core.config import EngineCfg

SIDES = ("buy", "sell")
TYPES = ("market", "limit", "stop", "stop_limit")


class EngineError(Exception):
    """A request the engine refuses. `code` is machine-readable, str(e) is for people."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()      # "leave unchanged" (None means "clear")


@dataclass
class Order:
    id: int
    symbol: str
    side: str
    type: str                      # market | limit | stop
    volume: float
    price: Optional[float]         # pending orders only
    sl: Optional[float]
    tp: Optional[float]
    status: str                    # working | filled | cancelled | rejected
    placed_ms: int                 # engine time the order was placed
    fill_at_ms: Optional[int]      # market orders: placed + latency
    kind: str = "open"             # open | close
    target_position: Optional[int] = None    # close orders
    source: str = "manual"
    peeked: int = 0
    session_id: Optional[str] = None
    comment: str = ""
    reason: str = ""
    position_id: Optional[int] = None
    stoplimit: Optional[float] = None      # stop_limit only: the limit price placed once `price` (the stop) is reached
    expire_ms: Optional[int] = None        # pending orders: cancelled ("expired") at this engine time; None = GTC

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Position:
    id: int
    symbol: str
    side: str
    volume: float
    open_price: float
    open_ms: int
    sl: Optional[float]
    tp: Optional[float]
    source: str = "manual"
    peeked: int = 0
    session_id: Optional[str] = None
    comment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def check_volume(volume: float, e: EngineCfg) -> float:
    if not volume or volume != volume:
        raise EngineError("bad_volume", "volume is required")
    if volume < e.min_volume - 1e-12 or volume > e.max_volume + 1e-12:
        raise EngineError("bad_volume", f"volume must be between {e.min_volume} and {e.max_volume} lots")
    steps = volume / e.volume_step
    if abs(steps - round(steps)) > 1e-6:
        raise EngineError("bad_volume", f"volume must be a multiple of {e.volume_step} lots")
    return round(steps) * e.volume_step


def check_levels(side: str, ref: float, sl: Optional[float], tp: Optional[float], min_dist: float,
                 what: str = "price") -> None:
    """SL/TP must sit on the correct side of `ref` (the exit-side price, or a pending order's price)."""
    if side == "buy":
        if sl is not None and not sl < ref - min_dist + 1e-12:
            raise EngineError("invalid_stops", f"stop loss must be below the {what} for a buy")
        if tp is not None and not tp > ref + min_dist - 1e-12:
            raise EngineError("invalid_stops", f"take profit must be above the {what} for a buy")
    else:
        if sl is not None and not sl > ref + min_dist - 1e-12:
            raise EngineError("invalid_stops", f"stop loss must be above the {what} for a sell")
        if tp is not None and not tp < ref - min_dist + 1e-12:
            raise EngineError("invalid_stops", f"take profit must be below the {what} for a sell")


def check_pending_price(order_type: str, side: str, price: float, bid: float, ask: float,
                        stoplimit: Optional[float] = None) -> None:
    """Pending orders must wait for a price the market has not reached yet (as on MT5).

    A stop-limit is a stop at `price` that, once reached, places a limit order at `stoplimit`: below the stop for a buy,
    above it for a sell."""
    trigger = "stop" if order_type == "stop_limit" else order_type
    ok = {("limit", "buy"): price < ask, ("stop", "buy"): price > ask,
          ("limit", "sell"): price > bid, ("stop", "sell"): price < bid}[(trigger, side)]
    if not ok:
        raise EngineError("invalid_price", f"{side} {order_type.replace('_', ' ')} price {price} is on the wrong side of the market "
                                           f"(bid {bid}, ask {ask})")
    if order_type == "stop_limit":
        if stoplimit is None:
            raise EngineError("bad_order", "a stop limit order needs a stop limit price")
        if (side == "buy" and not stoplimit < price) or (side == "sell" and not stoplimit > price):
            raise EngineError("invalid_price", f"the stop limit price must be {'below' if side == 'buy' else 'above'} the stop price "
                                               f"for a {side} stop limit")
    elif stoplimit is not None:
        raise EngineError("bad_order", "only stop limit orders have a stop limit price")
