"""Currency conversion shared by the grader (commission -> pips) and the paper engine (P&L, margin).

`mid(symbol)` is a callback returning the current mid price of a pair (or None). Every conversion that
needs another pair raises LookupError when that quote is missing, and NotImplementedError for quote
currencies other than USD / JPY, so a wrong number is never silently used. Account currency is USD.
"""
from __future__ import annotations

from typing import Callable, Optional, Tuple

MidFn = Callable[[str], Optional[float]]


def split_symbol(symbol: str, suffix: str = "") -> Tuple[str, str]:
    plain = symbol[:-len(suffix)] if suffix and symbol.endswith(suffix) else symbol
    if len(plain) != 6:
        raise ValueError(f"cannot split {symbol!r} into base/quote")
    return plain[:3], plain[3:]


def usd_per_quote(symbol: str, suffix: str, mid: MidFn) -> float:
    """USD value of one unit of the pair's QUOTE currency."""
    base, quote = split_symbol(symbol, suffix)
    if quote == "USD":
        return 1.0
    if quote == "JPY":                              # (USD per base) / (JPY per base) = USD per JPY
        own, ref = mid(symbol), mid(f"{base}USD{suffix}")
        if not own or not ref:
            raise LookupError(f"need quotes for {symbol} and {base}USD to convert JPY to USD")
        return ref / own
    raise NotImplementedError(f"conversion for quote currency {quote} ({symbol}) is not implemented")


def usd_per_base(symbol: str, suffix: str, mid: MidFn) -> float:
    """USD value of one unit of the pair's BASE currency (for margin)."""
    base, quote = split_symbol(symbol, suffix)
    if base == "USD":
        return 1.0
    ref = mid(symbol) if quote == "USD" else mid(f"{base}USD{suffix}")
    if not ref:
        raise LookupError(f"need a {base}USD quote to value {symbol} margin")
    return ref
