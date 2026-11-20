"""Pip size derived from the symbol's digits (hard rule 5) - never hardcoded per pair.

Brokers quote 5-digit (EURUSD 1.08123) or 3-digit (USDJPY 151.123) prices with a
fractional "pipette"; the pip is one digit above that. 4- and 2-digit brokers have no
pipette, so the pip is the last digit. This gives 0.0001 for xxxUSD/xxxEUR pairs and
0.01 for JPY pairs.
"""
from __future__ import annotations


def pip_size(digits: int) -> float:
    if digits < 1:
        raise ValueError(f"unsupported digits: {digits}")
    exp = digits - 1 if digits in (3, 5) else digits
    return 10.0 ** (-exp)


def to_pips(price_diff: float, digits: int) -> float:
    return price_diff / pip_size(digits)


def from_pips(pips: float, digits: int) -> float:
    return pips * pip_size(digits)
