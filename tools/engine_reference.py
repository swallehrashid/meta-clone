"""An independent, deliberately slow re-implementation of the trading rules (plain SQL + loops), used to check
engine/account.py. It shares no code with the engine except pip_size, and is used by the tests and by
`python -m tools.verify_engine` (months of history)."""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence

from collector.market_hours import market_open
from core import db
from core.config import Config
from core.pips import pip_size
from engine.account import Engine

DAY_MS = 86_400_000


def newest(conn, sym: str, t: int):
    return conn.execute("SELECT t_ms, seq, bid, ask FROM ticks WHERE symbol=? AND t_ms<=? ORDER BY t_ms DESC, seq DESC LIMIT 1",
                        (sym, t)).fetchone()


def mid(conn, sym: str, t: int) -> float:
    r = newest(conn, sym, t)
    return (r[2] + r[3]) / 2


def usd_per_quote(conn, sym: str, t: int) -> float:
    if sym.endswith("USD"):
        return 1.0
    return mid(conn, sym[:3] + "USD", t) / mid(conn, sym, t)                       # JPY crosses: USD per JPY


def reference_trade(conn, cfg: Config, tr: dict) -> dict:
    """Walk the ticks one by one: entry on the quote in force, SL/TP on the exit side, else a manual close."""
    sym, side, vol = tr["sym"], tr["side"], tr["vol"]
    digits = int(db.get_digits(conn, sym))
    pip = pip_size(digits)
    slip = cfg.costs.extra_slippage_pips * pip
    q = newest(conn, sym, tr["at"])
    entry = q[3] + slip if side == "buy" else q[2] - slip                         # buys at ask, sells at bid
    ticks = conn.execute("SELECT t_ms, bid, ask FROM ticks WHERE symbol=? AND (t_ms>? OR (t_ms=? AND seq>?)) AND t_ms<=? "
                         "ORDER BY t_ms, seq", (sym, q[0], q[0], q[1], tr["close_at"])).fetchall()
    sl, tp = tr["sl"], tr["tp"]
    out = None
    for t, bid, ask in ticks:
        if side == "buy":
            hit = "sl" if (sl is not None and bid <= sl) else "tp" if (tp is not None and bid >= tp) else None
            px = bid - slip
        else:
            hit = "sl" if (sl is not None and ask >= sl) else "tp" if (tp is not None and ask <= tp) else None
            px = ask + slip
        if hit:
            out = (hit, t, px)
            break
    if out is None:
        c = newest(conn, sym, tr["close_at"])
        out = ("manual", tr["close_at"], (c[2] - slip) if side == "buy" else (c[3] + slip))
    reason, t_close, px = out
    diff = (px - entry) if side == "buy" else (entry - px)
    return {"reason": reason, "close_ms": t_close, "close_price": px, "pips": diff / pip,
            "gross": diff * vol * cfg.costs.contract_size * usd_per_quote(conn, sym, t_close), "entry": entry}


def random_trades(conn, cfg: Config, symbols: Sequence[str], lo_ms: int, hi_ms: int, n: int, seed: int,
                  hold_ms=(60_000, 40 * 60_000)) -> List[dict]:
    """Random trades inside [lo, hi): any symbol/side, volume 0.1-2 lots, optional SL/TP 3-20 pips away."""
    rng = random.Random(seed)
    trades: List[dict] = []
    for i in range(n):
        sym = rng.choice(list(symbols))
        side = rng.choice(["buy", "sell"])
        at = rng.randrange(lo_ms, hi_ms - hold_ms[1])
        close_at = at + rng.randrange(*hold_ms)
        if not (market_open(at) and market_open(close_at)):
            continue                                                                  # orders cannot be placed or closed on a closed market
        if conn.execute("SELECT 1 FROM ticks WHERE symbol=? AND t_ms BETWEEN ? AND ? LIMIT 1", (sym, at - 60_000, at)).fetchone() is None:
            continue                                                                  # no recent quote: market closed / quiet
        digits = int(db.get_digits(conn, sym))
        pip = pip_size(digits)
        q = newest(conn, sym, at)
        exit_px = q[2] if side == "buy" else q[3]
        sgn = 1 if side == "buy" else -1
        sl = None if rng.random() < 0.25 else round(exit_px - sgn * rng.uniform(3, 15) * pip, digits)
        tp = None if rng.random() < 0.25 else round(exit_px + sgn * rng.uniform(3, 20) * pip, digits)
        trades.append(dict(i=i, sym=sym, side=side, vol=rng.choice([0.1, 0.5, 1.0, 2.0]), at=at,
                           close_at=close_at, sl=sl, tp=tp))
    return trades


def run_engine(cfg: Config, conn, trades: List[dict]):
    """Drive one engine through all trades in chronological order (positions overlap freely)."""
    eng = Engine(cfg, conn)
    eng.reset(balance=cfg.engine.starting_balance, leverage=cfg.engine.leverage)
    events = sorted([(t["at"], 0, t["i"]) for t in trades] + [(t["close_at"], 1, t["i"]) for t in trades])
    by_i = {t["i"]: t for t in trades}
    pos: Dict[int, int] = {}
    for when, kind, i in events:
        if kind == 0:
            t = by_i[i]
            pos[i] = eng.place_order(t["sym"], t["side"], volume=t["vol"], sl=t["sl"], tp=t["tp"], at_ms=when, comment=str(i))["id"]
        else:
            eng.advance_to(when)
            if pos[i] in eng.positions:
                eng.close(pos[i], at_ms=when)
    eng.advance_to(max(t["close_at"] for t in trades))
    return eng, pos


def compare(eng: Engine, pos: Dict[int, int], conn, cfg: Config, trades: List[dict], rel: float = 1e-9) -> dict:
    """Check every engine deal against the reference. Returns a summary; `mismatches` lists any differences."""
    deals = {d["position_id"]: d for d in eng.history(1_000_000)}
    comm = cfg.costs.commission_per_lot_round_trip
    mism, reasons, net_total, worst_pips = [], {"sl": 0, "tp": 0, "manual": 0}, 0.0, 0.0
    for t in trades:
        want = reference_trade(conn, cfg, t)
        got = deals.get(pos[t["i"]])
        if got is None:
            mism.append((t["i"], "no deal"))
            continue
        bad = []
        if got["reason"] != want["reason"]: bad.append(f"reason {got['reason']}!={want['reason']}")
        if got["close_ms"] != want["close_ms"]: bad.append(f"close_ms {got['close_ms']}!={want['close_ms']}")
        if abs(got["close_price"] - want["close_price"]) > 1e-9: bad.append("close_price")
        dp = abs(got["pips"] - want["pips"])
        worst_pips = max(worst_pips, dp)
        if dp > 1e-6: bad.append(f"pips {got['pips']}!={want['pips']}")
        if abs(got["gross"] - want["gross"]) > rel * max(1.0, abs(want["gross"])) + 1e-6: bad.append("gross")
        if abs(got["net"] - (want["gross"] - comm * t["vol"])) > 1e-6 + rel * abs(want["gross"]): bad.append("net")
        if bad:
            mism.append((t["i"], "; ".join(bad)))
        reasons[want["reason"]] += 1
        net_total += want["gross"] - comm * t["vol"]
    acct = eng.account()
    balance_ok = abs(acct["balance"] - (cfg.engine.starting_balance + net_total)) < 1e-6 * max(1.0, abs(net_total)) + 1e-6
    return {"trades": len(trades), "mismatches": mism, "exit_reasons": reasons, "worst_pips_error": worst_pips,
            "balance_matches_reference": balance_ok, "ref_net_total_usd": net_total, "engine_balance": acct["balance"],
            "open_positions_left": acct["positions"]}
