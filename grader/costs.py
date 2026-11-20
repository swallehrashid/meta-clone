"""Commission (money per lot, round trip) -> pips, so every label is comparable in pips.

pip value per lot (in the pair's quote currency) = contract_size * pip.
Converted to USD (the account currency) for quote currencies USD (as is) and JPY
(divide by USDJPY, derived as  XJPY / XUSD  from the same instant's mid prices, where X is
the pair's base currency - no USDJPY feed is needed). Other quote currencies raise, so a
wrong number is never silently used. With commission 0 (the default) nothing is looked up.
"""
from __future__ import annotations

import sqlite3

from core.config import Config
from core.dataview import DataView
from core.fx import usd_per_quote


def _mid(dv: DataView) -> float:
    t = dv.last_tick()
    if t is None:
        raise LookupError(f"no {dv.symbol} quote at/before {dv.cutoff_ms} to convert commission")
    return (t[2] + t[3]) / 2


def commission_pips(conn: sqlite3.Connection, cfg: Config, symbol: str, t_ms: int,
                    pip: float) -> float:
    comm = cfg.costs.commission_per_lot_round_trip
    if comm == 0:
        return 0.0
    mid = lambda sym: _mid(DataView(conn, sym, t_ms))          # quotes as of the entry time only
    pip_value_usd = cfg.costs.contract_size * pip * usd_per_quote(symbol, cfg.symbol_suffix, mid)
    return comm / pip_value_usd
