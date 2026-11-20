"""P&L, pips and margin arithmetic (pure functions). Account currency: USD.

Prices of the quote currency are converted with core.fx; pip size comes from the symbol's digits.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.fx import split_symbol
from core.pips import pip_size


@dataclass(frozen=True)
class Instrument:
    symbol: str
    base: str
    quote: str
    digits: int
    pip: float
    contract: int            # units per lot

    @staticmethod
    def of(symbol: str, suffix: str, digits: int, contract: int) -> "Instrument":
        base, quote = split_symbol(symbol, suffix)
        return Instrument(symbol, base, quote, digits, pip_size(digits), contract)


def price_diff(side: str, open_price: float, close_price: float) -> float:
    """Favourable price move of a position (positive = profit)."""
    return (close_price - open_price) if side == "buy" else (open_price - close_price)


def pips(inst: Instrument, side: str, open_price: float, close_price: float) -> float:
    return price_diff(side, open_price, close_price) / inst.pip


def gross_usd(inst: Instrument, side: str, volume: float, open_price: float, close_price: float,
              usd_per_quote: float) -> float:
    return price_diff(side, open_price, close_price) * volume * inst.contract * usd_per_quote


def pip_value_usd_per_lot(inst: Instrument, usd_per_quote: float) -> float:
    return inst.contract * inst.pip * usd_per_quote


def margin_usd(inst: Instrument, volume: float, usd_per_base: float, leverage: float) -> float:
    return volume * inst.contract * usd_per_base / leverage
