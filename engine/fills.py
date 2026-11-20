"""Fill prices and trigger rules (pure functions).

Conventions (match the Phase 3 grader where they overlap)
  * buys open at ASK and close at BID; sells open at BID and close at ASK. Never mid.
  * `extra_slippage_pips` is charged adversely on EVERY fill (entry and exit).
  * market orders fill at the quote in force at (order time + latency).
  * pending orders trigger on the first tick that touches them and fill at THAT tick's price (better than
    the order price if the market gaps through, worse for stops), plus slippage.
  * SL / TP are checked on the exit side (bid for longs, ask for shorts) on every tick, starting with the
    tick on which a pending order filled; they fill at that tick's price, plus slippage.
"""
from __future__ import annotations

from typing import Optional


def entry_price(side: str, bid: float, ask: float, slip_pips: float, pip: float) -> float:
    return ask + slip_pips * pip if side == "buy" else bid - slip_pips * pip


def exit_price(side: str, bid: float, ask: float, slip_pips: float, pip: float) -> float:
    """Price at which a position of `side` closes: longs sell at bid, shorts buy back at ask."""
    return bid - slip_pips * pip if side == "buy" else ask + slip_pips * pip


def pending_triggered(order_type: str, side: str, price: float, bid: float, ask: float) -> bool:
    if side == "buy":
        return ask <= price if order_type == "limit" else ask >= price      # buy limit / buy stop
    return bid >= price if order_type == "limit" else bid <= price          # sell limit / sell stop


def sl_tp_hit(side: str, sl: Optional[float], tp: Optional[float], bid: float, ask: float) -> Optional[str]:
    """'sl' / 'tp' if this quote touches either level (long: bid, short: ask), else None."""
    if side == "buy":
        if sl is not None and bid <= sl:
            return "sl"
        if tp is not None and bid >= tp:
            return "tp"
    else:
        if sl is not None and ask >= sl:
            return "sl"
        if tp is not None and ask <= tp:
            return "tp"
    return None
