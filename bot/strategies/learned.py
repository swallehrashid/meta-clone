"""BLIND learned bot: trades from a model trained on the teacher's decisions. It never sees forward data.

The model (see bot/model.py, trained by `python -m bot.train` or the back office) maps the starter features at the decision
time to "would the teacher have bought / sold here?". This bot enters when that probability is high enough and clearly
higher than the other side, then exits on a timer with fixed SL / TP. It is exactly as blind as a human on the delayed chart.
Whether it has any edge is decided only by a held-out, blind evaluation - never by its training fit.
"""
from __future__ import annotations

from typing import List

from bot.interface import Action, Bot, Close, Context, Open, ParamSpec
from bot.model import LinearModel


class LearnedBot(Bot):
    name = "learned"
    description = "Blind bot driven by a model trained on the forward-fed teacher. Sees no forward data. Needs a trained model."
    PARAMS = {
        "model": ParamSpec("str", "", help="name of a trained model (train one first)"),
        "volume": ParamSpec("float", 0.1, min=0.01, max=5.0, help="lots per trade"),
        "threshold": ParamSpec("float", 0.0, min=0.0, max=1.0, help="minimum model probability to enter (0 = the model's own suggested level)"),
        "margin": ParamSpec("float", 0.05, min=0.0, max=1.0, help="the chosen side must beat the other by this much"),
        "hold_s": ParamSpec("int", 60, min=10, max=3600, help="close after this many seconds"),
        "sl_pips": ParamSpec("float", 5.0, min=0.0, max=500.0, help="stop loss in pips (0 = none)"),
        "tp_pips": ParamSpec("float", 3.0, min=0.0, max=500.0, help="take profit in pips (0 = none)"),
        "max_spread_pips": ParamSpec("float", 0.0, min=0.0, max=50.0, help="skip when the spread is wider (0 = no limit)"),
    }

    def __init__(self, **params):
        super().__init__(**params)
        self._model = LinearModel.load(self.params["model"])        # raises a clear error when the model does not exist

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
            thr = p["threshold"] or self._model.meta.get("suggested_threshold", 0.3)
            pb, ps = self._model.predict(ctx.features(sym))
            side, pr, other = ("buy", pb, ps) if pb >= ps else ("sell", ps, pb)
            if pr >= thr and pr - other >= p["margin"]:
                out.append(Open(sym, side, p["volume"], sl_pips=p["sl_pips"] or None, tp_pips=p["tp_pips"] or None,
                                comment=f"learned p={pr:.2f}"))
        return out
