"""A deliberately simple rule-based bot: trade a one-minute breakout, exit on a timer / SL / TP.

NOT a strategy anyone should trade - it exists to show how a rule uses the shared feature set, to give the
evaluation something concrete to grade, and to be compared with RandomEntryBot. Buy when the last completed M1 candle
closed above the previous candle's high, sell when it closed below the previous low (and the spread is acceptable).
"""
from __future__ import annotations

from typing import List

from bot.interface import Action, Bot, Close, Context, Open, ParamSpec


class MomentumBreakoutBot(Bot):
    name = "momentum_breakout"
    description = "Follows a 1-minute breakout (close beyond the previous candle's extreme). A demo rule, not an edge."
    PARAMS = {
        "volume": ParamSpec("float", 0.1, min=0.01, max=5.0, help="lots per trade"),
        "hold_s": ParamSpec("int", 120, min=10, max=3600, help="close after this many seconds"),
        "sl_pips": ParamSpec("float", 6.0, min=0.0, max=500.0, help="stop loss in pips (0 = none)"),
        "tp_pips": ParamSpec("float", 9.0, min=0.0, max=500.0, help="take profit in pips (0 = none)"),
        "max_spread_pips": ParamSpec("float", 1.5, min=0.0, max=50.0, help="skip entries when the spread is wider (0 = no limit)"),
        "min_range_pips": ParamSpec("float", 3.0, min=0.0, max=500.0, help="ignore breakouts of tiny candles"),
        "reverse": ParamSpec("bool", False, help="fade the breakout instead of following it"),
    }

    def decide(self, ctx: Context) -> List[Action]:
        p = self.params
        out: List[Action] = []
        for sym in ctx.symbols:
            for pos in ctx.my_positions(sym):
                if ctx.t_ms - pos.open_ms >= p["hold_s"] * 1000:
                    out.append(Close(pos.id))
            if ctx.has_exposure(sym):
                continue
            q = ctx.quote(sym)
            if q is None or ctx.t_ms - q.t_ms > 60_000:
                continue
            if p["max_spread_pips"] and q.spread_pips > p["max_spread_pips"]:
                continue
            f = ctx.features(sym)                                  # computed from this symbol's view up to ctx.t_ms only
            if f.get("last_m1_range_pips", 0.0) < p["min_range_pips"]:
                continue
            up, down = f.get("m1_closed_above_prev_high") == 1.0, f.get("m1_closed_below_prev_low") == 1.0
            if up == down:
                continue
            side = "buy" if up else "sell"
            if p["reverse"]:
                side = "sell" if side == "buy" else "buy"
            out.append(Open(sym, side, p["volume"], sl_pips=p["sl_pips"] or None, tp_pips=p["tp_pips"] or None, comment="breakout"))
        return out
