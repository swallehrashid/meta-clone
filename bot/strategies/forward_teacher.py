"""TEACHER bot: trades with perfect knowledge of the next minute(s). It is an answer key, not a strategy.

Needs forward data (`forward_s` > 0 on the run): at every decision it looks at the ticks that come next - the minute the
delayed chart has not drawn yet - and takes the better side if that side would have paid more than `min_edge_pips` after the
spread. The stop sits just beyond the worst excursion inside the holding window (so it is never hit) and the take-profit at
a fraction of the best one. It therefore makes money by construction; what it is for is to
  * show what the trading looks like when the next candle is known (watch it on the terminal),
  * produce the examples a blind bot is trained on (`bot.train` replays `teacher_signal` over history),
  * give an upper bound: a blind bot can never beat this.
Every order of a forward run is tagged `peeked`, so none of this is ever mistaken for clean performance.
"""
from __future__ import annotations

from typing import List, Optional

from bot.interface import Action, Bot, Close, Context, ForwardView, Open, ParamSpec, Quote


def teacher_signal(q: Quote, fwd: ForwardView, pip: float, hold_s: float, min_edge_pips: float, tp_fraction: float,
                   sl_buffer_pips: float) -> Optional[dict]:
    """The teacher's decision from the quote now and the ticks that follow. A pure function: also used to build training data."""
    if fwd is None or not fwd.complete:
        return None
    ex = fwd.extremes(hold_s)
    end = fwd.quote_at(hold_s)
    if ex is None or end is None:
        return None
    lo_bid, hi_bid, lo_ask, hi_ask = ex
    end_bid, end_ask = end
    buy_net = (end_bid - q.ask) / pip                    # buy at the ask now, close at the bid at the end of the window
    sell_net = (q.bid - end_ask) / pip
    if max(buy_net, sell_net) < min_edge_pips:
        return None
    if buy_net >= sell_net:
        side, net, mfe, mae = "buy", buy_net, (hi_bid - q.ask) / pip, (q.ask - lo_bid) / pip
    else:
        side, net, mfe, mae = "sell", sell_net, (q.bid - lo_ask) / pip, (hi_ask - q.bid) / pip
    return {"side": side, "net_pips": net, "sl_pips": max(1.0, mae + sl_buffer_pips),
            "tp_pips": max(0.5, mfe * tp_fraction) if tp_fraction > 0 else None}


class ForwardTeacherBot(Bot):
    name = "forward_teacher"
    needs_forward = True
    description = ("TEACHER: sees the next minute(s) and takes the profitable side. Needs forward data; its trades are tagged "
                   "'peeked'. An upper bound and the source of training examples - not a strategy.")
    PARAMS = {
        "volume": ParamSpec("float", 0.1, min=0.01, max=5.0, help="lots per trade"),
        "hold_s": ParamSpec("int", 60, min=10, max=3600, help="look this far ahead, and close after this long (<= forward_s)"),
        "min_edge_pips": ParamSpec("float", 1.0, min=0.0, max=100.0, help="only trade when the move, after spread, is at least this"),
        "tp_fraction": ParamSpec("float", 0.8, min=0.0, max=1.0, help="take profit at this share of the best move in the window (0 = none)"),
        "sl_buffer_pips": ParamSpec("float", 1.0, min=0.0, max=50.0, help="stop = worst move in the window + this"),
        "max_spread_pips": ParamSpec("float", 0.0, min=0.0, max=50.0, help="skip when the spread is wider (0 = no limit)"),
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
            q, fwd = ctx.quote(sym), ctx.forward(sym)
            if q is None or fwd is None or ctx.t_ms - q.t_ms > 60_000:
                continue
            if p["max_spread_pips"] and q.spread_pips > p["max_spread_pips"]:
                continue
            sig = teacher_signal(q, fwd, ctx.pip(sym), min(p["hold_s"], fwd.horizon_s), p["min_edge_pips"], p["tp_fraction"], p["sl_buffer_pips"])
            if sig:
                out.append(Open(sym, sig["side"], p["volume"], sl_pips=sig["sl_pips"], tp_pips=sig["tp_pips"],
                                comment=f"teacher {sig['net_pips']:+.1f}p"))
        return out
