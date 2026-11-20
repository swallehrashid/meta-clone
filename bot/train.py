"""Teach a blind bot from a forward-fed teacher, then test it blind on days it has never seen.

    python -m bot.train --symbols EURUSD,GBPUSD --train-from 2026-09-21 --train-to 2026-10-09 \
                        --test-from 2026-10-12 --test-to 2026-10-16 --name m1

The procedure (the same one the back office runs):
  1. WATCH   replay the forward-fed teacher over the training days: at every decision minute it looks at the next minute(s)
             and either takes a side or stands aside (`teacher_signal`). Each decision is stored next to the entry-time features.
  2. ANALYSE which features go with the teacher's decisions (correlations, base rates) - reported, never hidden.
  3. LEARN   fit the linear model to predict "would the teacher buy / sell here?" from the features alone, on the first 80% of
             the training days; report how well it ranks the remaining 20% (AUC; 0.5 = no skill). Refit on all, save.
  4. TEST    replay, on the HELD-OUT days: the learned bot BLIND, the random bot (null benchmark, same exits) and the forward-fed
             teacher (upper bound). Judge the blind bot only against the random one, using the direction / ex-spread edges.
On the simulated market (a martingale with no structure) a correct pipeline finds nothing: the blind bot cannot beat random.
That is the proof the procedure is honest; real data is what makes the result mean something.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from bot.evaluate import evaluate
from bot.interface import ForwardView, Quote
from bot.model import LinearModel, sigmoid, train_linear
from bot.runner import RiskLimits, run_replay
from bot.strategies import make_bot
from bot.strategies.forward_teacher import teacher_signal
from collector.market_hours import market_open
from core import db
from core.config import Config, load_config
from core.dataview import DataView
from core.pips import pip_size
from tools.sandbox import open_sandbox

GRID_MS = 60_000
DAY_MS = 86_400_000


def day_range(d_from: str, d_to: str, tz: str) -> Tuple[int, int]:
    """[00:00 of d_from, 24:00 of d_to) as UTC ms, days taken in time zone `tz`."""
    z = ZoneInfo(tz)
    a = datetime.strptime(d_from, "%Y-%m-%d").replace(tzinfo=z)
    b = datetime.strptime(d_to, "%Y-%m-%d").replace(tzinfo=z) + timedelta(days=1)
    if b <= a:
        raise ValueError("the end date is before the start date")
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000) - 1


@dataclass
class Dataset:
    features: List[str]
    X: np.ndarray            # decisions x features (NaN = not computable)
    y_buy: np.ndarray
    y_sell: np.ndarray
    t_ms: np.ndarray
    symbol: np.ndarray
    teacher_net: np.ndarray  # the teacher's net pips when it traded, else 0

    @property
    def n(self) -> int:
        return len(self.t_ms)


def build_dataset(cfg: Config, conn, symbols: Sequence[str], t_from: int, t_to: int, forward_s: int, hold_s: int,
                  min_edge_pips: float, tp_fraction: float, sl_buffer_pips: float,
                  progress: Optional[Callable[[str], None]] = None) -> Dataset:
    """One row per (symbol, decision minute): entry-time features + what the forward-fed teacher would have done."""
    rows_x: List[dict] = []
    ys: List[Tuple[int, int, float]] = []
    keys: List[Tuple[int, str]] = []
    side = cfg.candles.price_side
    for sym in symbols:
        digits = db.get_digits(conn, sym)
        pip = pip_size(digits)
        feats = pd.read_sql_query("SELECT t_ms, name, value FROM features WHERE symbol=? AND t_ms BETWEEN ? AND ?", conn,
                                  params=(sym, t_from, t_to))
        if feats.empty:
            raise ValueError(f"no stored features for {sym} in that period - run the labeler (python -m grader.labeler_worker --once)")
        wide = feats.pivot(index="t_ms", columns="name", values="value")
        day = t_from - t_from % DAY_MS
        while day <= t_to:
            lo, hi = max(day, t_from), min(day + DAY_MS - 1, t_to)
            day += DAY_MS
            if progress:
                progress(f"{sym} {datetime.fromtimestamp(lo / 1000, tz=timezone.utc):%Y-%m-%d}")
            # ticks from a little before the day to the end of the last forward window; read through a view pinned at that end
            end = hi + forward_s * 1000
            view = DataView(conn, sym, end, None, side)
            ticks = view.ticks(lo - 120_000, end)
            if not ticks:
                continue
            tt = [r[0] for r in ticks]
            for t in range(lo - lo % GRID_MS + (GRID_MS if lo % GRID_MS else 0), hi + 1, GRID_MS):
                if t not in wide.index or not market_open(t):
                    continue
                i = bisect.bisect_right(tt, t)                         # ticks[:i] are <= t, ticks[i:] come after
                if i == 0 or t - ticks[i - 1][0] > 60_000:
                    continue                                          # no fresh quote: the bot would skip too
                q = Quote(ticks[i - 1][0], ticks[i - 1][2], ticks[i - 1][3], (ticks[i - 1][3] - ticks[i - 1][2]) / pip)
                j = bisect.bisect_right(tt, t + forward_s * 1000)
                fwd = ForwardView(ticks[i:j], side, t, forward_s, market_open(t + forward_s * 1000))
                sig = teacher_signal(q, fwd, pip, min(hold_s, forward_s), min_edge_pips, tp_fraction, sl_buffer_pips)
                rows_x.append(wide.loc[t].dropna().to_dict())
                ys.append((int(bool(sig) and sig["side"] == "buy"), int(bool(sig) and sig["side"] == "sell"), sig["net_pips"] if sig else 0.0))
                keys.append((t, sym))
    if not rows_x:
        raise ValueError("no decisions in that period (market closed, or no ticks / features stored)")
    names = sorted({k for r in rows_x for k in r})
    X = np.array([[r.get(n, np.nan) for n in names] for r in rows_x], dtype=float)
    y = np.array(ys, dtype=float)
    order = np.argsort([k[0] for k in keys], kind="stable")
    return Dataset(names, X[order], y[order, 0], y[order, 1], np.array([k[0] for k in keys])[order],
                   np.array([k[1] for k in keys])[order], y[order, 2])


def auc(score: np.ndarray, y: np.ndarray) -> Optional[float]:
    """Probability that a random positive outranks a random negative (0.5 = no skill). Rank-based, tie-aware."""
    pos = int(y.sum())
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return None
    r = pd.Series(score).rank(method="average").to_numpy()
    return float((r[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def analyse(ds: Dataset) -> dict:
    """Plain numbers about the teacher's decisions: how often it trades and what each feature says about its choice."""
    signed = ds.y_buy - ds.y_sell
    out = {"decisions": ds.n, "teacher_buy_pct": float(ds.y_buy.mean() * 100), "teacher_sell_pct": float(ds.y_sell.mean() * 100),
           "teacher_avg_net_pips_when_trading": float(ds.teacher_net[(ds.y_buy + ds.y_sell) > 0].mean()) if (ds.y_buy + ds.y_sell).any() else None,
           "features": []}
    trade = ds.y_buy + ds.y_sell
    for k, n in enumerate(ds.features):
        col = ds.X[:, k]
        ok = ~np.isnan(col)
        if ok.sum() < 30 or np.nanstd(col) < 1e-12:
            continue
        c = lambda y: float(np.corrcoef(col[ok], y[ok])[0, 1]) if y[ok].std() > 0 else 0.0
        out["features"].append({"feature": n, "corr_trade": c(trade), "corr_direction": c(signed)})
    out["features"].sort(key=lambda r: -max(abs(r["corr_trade"]), abs(r["corr_direction"])))
    return out


def fit(ds: Dataset, name: str, l2: float, meta_extra: dict) -> Tuple[LinearModel, dict]:
    """Fit on the first 80% (by time), report validation AUC on the last 20%, then refit on everything and return that model."""
    X = np.where(np.isnan(ds.X), np.nanmean(ds.X, axis=0), ds.X)
    cut = int(ds.n * 0.8)
    rep: dict = {"n_train": cut, "n_validation": ds.n - cut}
    if cut < 200 or ds.n - cut < 100:
        raise ValueError(f"only {ds.n} decisions - use a longer training period (a few days at least)")
    m80 = train_linear(X[:cut], ds.y_buy[:cut], ds.y_sell[:cut], ds.features, l2, {})
    Zv = (X[cut:] - np.array(m80.mean)) / np.array(m80.std)
    pb = sigmoid(Zv @ np.array(m80.w_buy) + m80.b_buy)
    ps = sigmoid(Zv @ np.array(m80.w_sell) + m80.b_sell)
    rep["validation_auc_buy"], rep["validation_auc_sell"] = auc(pb, ds.y_buy[cut:]), auc(ps, ds.y_sell[cut:])
    rep["validation_auc_trade"] = auc(pb + ps, (ds.y_buy + ds.y_sell)[cut:])
    # DIRECTION skill: among the minutes where the teacher traded, can the features tell buy from sell? The per-side AUCs above are
    # inflated by volatility (both sides are 'worthy' in a volatile minute, and volatility is predictable), this one is not.
    tr = (ds.y_buy + ds.y_sell)[cut:] > 0
    rep["validation_auc_direction"] = auc((pb - ps)[tr], ds.y_buy[cut:][tr]) if tr.sum() > 50 else None
    pmax = np.maximum(pb, ps)
    thr = float(np.quantile(pmax, 0.8))                              # act on the top fifth of the model's confidence
    meta = {"trained_on": f"{ds.n} decisions", "features": len(ds.features), "l2": l2, "suggested_threshold": round(thr, 4),
            "base_buy": float(ds.y_buy.mean()), "base_sell": float(ds.y_sell.mean()), **rep, **meta_extra}
    model = train_linear(X, ds.y_buy, ds.y_sell, ds.features, l2, meta)
    rep["suggested_threshold"] = meta["suggested_threshold"]
    return model, rep


def compare(cfg: Config, db_path: str, symbols: Sequence[str], t_from: int, t_to: int, model_name: str, forward_s: int,
            hold_s: int, sl_pips: float, tp_pips: float, volume: float,
            progress: Optional[Callable[[str], None]] = None) -> dict:
    """Replay the held-out days for the blind learned bot, the random null benchmark and the forward-fed teacher."""
    out: dict = {}
    limits = RiskLimits(max_open_positions=len(symbols), max_total_volume=100.0, max_volume_per_order=volume + 1,
                        max_orders_per_hour=10_000)
    specs = [
        ("learned_blind", "learned", {"model": model_name, "volume": volume, "hold_s": hold_s, "sl_pips": sl_pips, "tp_pips": tp_pips}, 0),
        ("random_null", "random_entry", {"volume": volume, "hold_s": hold_s, "sl_pips": sl_pips, "tp_pips": tp_pips, "p_enter": 0.05}, 0),
        ("teacher_forward", "forward_teacher", {"volume": volume, "hold_s": min(hold_s, forward_s)}, forward_s),
    ]
    for label, bot_name, params, fwd in specs:
        if progress:
            progress(f"replaying {label}")
        conn = open_sandbox(str(Path(db_path).resolve()))
        try:
            res = run_replay(cfg, conn, make_bot(bot_name, params), list(symbols), t_from, t_to, limits, balance=1_000_000.0, forward_s=fwd)
            ev = evaluate(conn, cfg, res)
        finally:
            conn.close()
        keep = {k: ev.get(k) for k in ("summary", "selection_edge_ex_spread_pips", "direction_edge_pips", "oracle", "bot_net_pips")}
        out[label] = keep | {"forward_s": fwd, "tagged_peeked": fwd > 0}
    return out


def verdict(cmp: dict) -> str:
    b, r = cmp["learned_blind"], cmp["random_null"]
    bs, rs = b["summary"], r["summary"]
    if not bs["trades"]:
        return "The blind bot took no trades on the held-out days (its threshold may be too high)."
    de = b.get("direction_edge_pips") or {}
    z = de.get("z")
    d = (bs["avg_net_pips"] or 0) - (rs["avg_net_pips"] or 0)
    if z is not None and z > 2.5 and d > 0:
        return (f"The blind bot beat the random benchmark by {d:+.2f} net pips/trade with a significant direction edge (z {z:+.1f}). "
                "Re-check on other held-out periods and on real data before believing it.")
    return (f"No evidence of skill: blind {bs['avg_net_pips']:+.2f} vs random {rs['avg_net_pips']:+.2f} net pips/trade "
            f"(direction edge z {('%+.1f' % z) if z is not None else 'n/a'}). The teacher's {cmp['teacher_forward']['summary']['avg_net_pips'] or 0:+.2f} is the "
            "upper bound that forward data gives; none of it carried over to the blind bot.")


def run_pipeline(cfg: Config, db_path: str, symbols: Sequence[str], train_range: Tuple[int, int], test_range: Optional[Tuple[int, int]],
                 name: str, forward_s: int = 60, hold_s: int = 60, min_edge_pips: float = 1.0, tp_fraction: float = 0.8,
                 sl_buffer_pips: float = 1.0, l2: float = 5.0, sl_pips: float = 5.0, tp_pips: float = 3.0, volume: float = 0.1,
                 progress: Optional[Callable[[str], None]] = None) -> dict:
    conn = open_sandbox(str(Path(db_path).resolve()))
    try:
        if progress:
            progress("watching the teacher")
        ds = build_dataset(cfg, conn, symbols, train_range[0], train_range[1], forward_s, hold_s, min_edge_pips, tp_fraction,
                           sl_buffer_pips, progress)
    finally:
        conn.close()
    analysis = analyse(ds)
    if progress:
        progress("fitting the model")
    model, rep = fit(ds, name, l2, {"symbols": list(symbols), "train_from_ms": train_range[0], "train_to_ms": train_range[1],
                                    "forward_s": forward_s, "teacher_hold_s": hold_s, "min_edge_pips": min_edge_pips})
    model.save(name)
    out = {"model": name, "analysis": analysis, "fit": rep, "weights": model.explain()[:12]}
    if test_range:
        out["test"] = compare(cfg, db_path, symbols, test_range[0], test_range[1], name, forward_s, hold_s, sl_pips, tp_pips, volume, progress)
        out["verdict"] = verdict(out["test"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Watch the forward-fed teacher, learn from it, test the blind bot on held-out days")
    ap.add_argument("--config"); ap.add_argument("--db")
    ap.add_argument("--symbols", help="comma list (default: all configured)")
    ap.add_argument("--train-from", required=True); ap.add_argument("--train-to", required=True)
    ap.add_argument("--test-from"); ap.add_argument("--test-to")
    ap.add_argument("--name", required=True, help="name to save the model under")
    ap.add_argument("--forward", type=int, default=60, help="seconds of forward data the teacher sees")
    ap.add_argument("--hold", type=int, default=60); ap.add_argument("--min-edge", type=float, default=1.0)
    ap.add_argument("--l2", type=float, default=5.0)
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    syms = [s.strip() + cfg.symbol_suffix for s in a.symbols.split(",")] if a.symbols else cfg.broker_symbols
    try:
        tr = day_range(a.train_from, a.train_to, cfg.display_timezone)
        te = day_range(a.test_from, a.test_to, cfg.display_timezone) if a.test_from and a.test_to else None
        res = run_pipeline(cfg, str(a.db or cfg.resolve_db_path()), syms, tr, te, a.name, a.forward, a.hold, a.min_edge, l2=a.l2,
                           progress=lambda m: print(f"  {m}", flush=True))
    except ValueError as e:
        print(f"error: {e}")
        return 2
    f = res["fit"]
    print(f"\nteacher traded {res['analysis']['teacher_buy_pct'] + res['analysis']['teacher_sell_pct']:.0f}% of {res['analysis']['decisions']:,} decisions")
    d = f["validation_auc_direction"]
    print(f"validation AUC (0.5 = no skill): trade/no-trade {f['validation_auc_trade']:.3f} (volatility is predictable)  "
          f"buy-vs-sell DIRECTION {('%.3f' % d) if d is not None else 'n/a'}  <- the one that matters")
    for k in ("learned_blind", "random_null", "teacher_forward"):
        if "test" in res:
            s = res["test"][k]["summary"]
            print(f"  {k:16s} trades {s['trades']:6d}  avg net pips {s['avg_net_pips'] if s['avg_net_pips'] is None else round(s['avg_net_pips'], 2)}")
    if "verdict" in res:
        print("\n" + res["verdict"])
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
