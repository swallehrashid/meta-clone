"""Random trader: the NULL benchmark. Enters at random, exits on a timer (and optional SL/TP).

On a market with no edge it must lose about the round-trip cost - that is exactly what it is for: if a real strategy cannot
beat THIS bot (same costs, same exits), it has no edge. `max_spread_pips` shows what cost control alone does.
"""
from __future__ import annotations

from typing import List

from bot.interface import Action, Bot, Close, Context, Open, ParamSpec, stable_random


class RandomEntryBot(Bot):
    name = "random_entry"
    description = "Enters at random (null benchmark), exits after a fixed time. Expect: lose about the costs."
    PARAMS = {
        "p_enter": ParamSpec("float", 0.02, min=0.0, max=1.0, help="chance of entering per symbol per minute when flat"),
        "volume": ParamSpec("float", 0.1, min=0.01, max=5.0, help="lots per trade"),
        "side": ParamSpec("choice", "both", choices=("both", "buy", "sell"), help="which direction(s) to trade"),
        "hold_s": ParamSpec("int", 120, min=10, max=3600, help="close after this many seconds"),
        "sl_pips": ParamSpec("float", 0.0, min=0.0, max=500.0, help="stop loss in pips (0 = none)"),
        "tp_pips": ParamSpec("float", 0.0, min=0.0, max=500.0, help="take profit in pips (0 = none)"),
        "max_spread_pips": ParamSpec("float", 0.0, min=0.0, max=50.0, help="skip entries when the spread is wider (0 = no limit)"),
        "seed": ParamSpec("int", 1, min=0, max=10**9, help="random seed"),
    }

    def decide(self, ctx: Context) -> List[Action]:
        p = self.params
        out: List[Action] = []
        for sym in ctx.symbols:
            mine = ctx.my_positions(sym)
            for pos in mine:
                if ctx.t_ms - pos.open_ms >= p["hold_s"] * 1000:
                    out.append(Close(pos.id))
            if ctx.has_exposure(sym):
                continue
            q = ctx.quote(sym)
            if q is None or ctx.t_ms - q.t_ms > 60_000:
                continue                                           # no fresh quote
            if p["max_spread_pips"] and q.spread_pips > p["max_spread_pips"]:
                continue
            rng = stable_random(p["seed"], sym, ctx.t_ms)
            if rng.random() >= p["p_enter"]:
                continue
            side = p["side"] if p["side"] != "both" else ("buy" if rng.random() < 0.5 else "sell")
            out.append(Open(sym, side, p["volume"], sl_pips=p["sl_pips"] or None, tp_pips=p["tp_pips"] or None,
                            comment="random"))
        return out
