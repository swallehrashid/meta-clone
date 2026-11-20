"""Grade a bot run AFTER the fact against the labels and the oracle.

This module deliberately lives apart from the bot and the runner: it reads outcomes (labels, the oracle) that a bot must
never see while deciding. It answers questions the bot's own P&L cannot:

  * selection edge   do the entries the bot picked have better label outcomes than the average entry of the same period
                     and direction? (and the same after adding back the entry spread, i.e. is it PRICE, not just cheap fills?)
  * oracle lift      how often are the bot's entries among the best-in-hindsight entries, vs the oracle's base rate?
  * exit effect      realised net pips minus the label's time-exit result for the same entry: what the bot's exits added/lost.

A trade is matched to the label of the grid minute it was opened in (same symbol and direction). Entry quality is measured at ONE
fixed horizon for every trade (`horizon_s`, default `analysis.horizon_s`) - never at a horizon derived from the trade's own holding
time. A stopped-out trade's duration depends on how the price path unfolded, so picking the horizon from it biases the measurement
(measured: a momentum rule and its exact mirror both showed a significant "direction edge" until this was fixed). The exit effect
compares the realised result with that same horizon, so it is only meaningful for bots whose hold time is close to it.
"""
from __future__ import annotations

import html
import sqlite3
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from analysis.oracle import oracle_mask
from bot.runner import RunResult
from core.config import Config

HORIZONS = (30, 60, 120, 300)


MIN_BLOCKS = 8          # fewer independent hours than this and the cluster-robust test is not trusted
BLOCK_MS = 3_600_000    # trades in the same market hour are treated as one cluster


def _ms(x: pd.Series, blocks: Optional[pd.Series] = None) -> dict:
    """mean, standard error and z of a sample of per-trade values (null: mean 0).

    `z_naive` assumes independent trades. Trades are NOT independent: volatility clusters in time and the pairs share
    factors, so the naive test overstates certainty (measured: its z scatters with sd ~1.5 on a random bot). `z` is
    cluster-robust - the variance is built from per-hour totals - and is the one to believe. With too few hours it falls
    back to the naive test and says so (`robust: False`).
    """
    ok = x.notna()
    v = x[ok].to_numpy(dtype=float)
    n = len(v)
    if n == 0:
        return {"n": 0, "mean": None, "se": None, "z": None, "z_naive": None, "robust": False}
    mean = float(v.mean())
    se_naive = float(v.std(ddof=1) / np.sqrt(n)) if n > 1 else None
    out = {"n": n, "mean": mean, "se_naive": se_naive, "z_naive": (mean / se_naive) if se_naive else None}
    if blocks is not None and blocks[ok].nunique() >= MIN_BLOCKS:
        resid = pd.Series(v - mean).groupby(blocks[ok].to_numpy()).sum()          # per-hour sum of centred values
        se = float(np.sqrt((resid ** 2).sum()) / n)
        out.update(se=se, z=(mean / se) if se else None, robust=True, clusters=int(resid.size))
    else:
        out.update(se=se_naive, z=out["z_naive"], robust=False)
    return out


def evaluate(conn: sqlite3.Connection, cfg: Config, res: RunResult, horizon_s: Optional[int] = None) -> dict:
    hz = horizon_s or cfg.analysis.horizon_s
    if hz not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}")
    out: dict = {"bot": res.bot, "summary": res.summary(), "horizon_s": hz}
    if not res.deals:
        out["matching"] = {"deals": 0, "matched": 0}
        return out
    step = cfg.grader.step_seconds * 1000
    syms = list(res.symbols)
    q = ("SELECT symbol, entry_t_ms, direction, spread_pips, pnl_30s, pnl_60s, pnl_120s, pnl_300s FROM labels "
         f"WHERE status='final' AND symbol IN ({','.join('?' * len(syms))}) AND entry_t_ms BETWEEN ? AND ?")
    lab = pd.read_sql_query(q, conn, params=syms + [res.t_from_ms - step, res.t_to_ms + step])
    if lab.empty:
        out["matching"] = {"deals": len(res.deals), "matched": 0, "note": "no labels for this period - run the labeler first"}
        return out

    oracle_h = cfg.analysis.oracle.horizon_s
    lab["oracle"] = False
    base: Dict[str, dict] = {}
    for d, g in lab.groupby("direction"):
        mask, _rule = oracle_mask(g.rename(columns={}), cfg.analysis.oracle) if cfg.analysis.oracle.mode == "top_decile" else _oracle_for(g, cfg)
        lab.loc[g.index, "oracle"] = mask.reindex(g.index).fillna(False).to_numpy()
        base[d] = {f"pnl_{h}s": float(g[f"pnl_{h}s"].mean()) for h in HORIZONS} | {"spread": float(g["spread_pips"].mean())}
    base_rate = float(lab["oracle"].mean())
    idx = lab.set_index(["symbol", "entry_t_ms", "direction"])

    rows: List[dict] = []
    for dl in res.deals:
        key = (dl["symbol"], dl["open_ms"] // step * step, dl["side"])
        if key not in idx.index:
            continue
        lr = idx.loc[key]
        h = hz
        opp = (key[0], key[1], "sell" if dl["side"] == "buy" else "buy")
        opp_pnl = float(idx.loc[opp][f"pnl_{h}s"]) if opp in idx.index else float("nan")
        rows.append({"symbol": dl["symbol"], "side": dl["side"], "reason": dl["reason"], "h": h, "net_pips": dl["net_pips"], "opp_pnl": opp_pnl,
                     "label_pnl": float(lr[f"pnl_{h}s"]), "base_pnl": base[dl["side"]][f"pnl_{h}s"],
                     "spread": float(lr["spread_pips"]), "base_spread": base[dl["side"]]["spread"], "oracle": bool(lr["oracle"]),
                     "block": dl["open_ms"] // BLOCK_MS})
    m = pd.DataFrame(rows)
    out["matching"] = {"deals": len(res.deals), "matched": len(m)}
    if m.empty:
        return out
    m["sel"] = m["label_pnl"] - m["base_pnl"]
    m["sel_ex_spread"] = (m["label_pnl"] + m["spread"]) - (m["base_pnl"] + m["base_spread"])
    m["exit"] = m["net_pips"] - m["label_pnl"]
    m["dir"] = m["label_pnl"] - m["opp_pnl"]            # the bot's side vs the OPPOSITE side of the same entry: pure direction
    blk = pd.Series([d["open_ms"] // BLOCK_MS for d in res.deals])
    out["bot_net_pips"] = _ms(pd.Series([d["net_pips"] for d in res.deals]), blk)
    out["selection_edge_pips"] = _ms(m["sel"], m["block"])
    out["selection_edge_ex_spread_pips"] = _ms(m["sel_ex_spread"], m["block"])
    out["exit_effect_pips"] = _ms(m["exit"], m["block"])
    out["direction_edge_pips"] = _ms(m["dir"], m["block"])
    n = len(m)
    hits = int(m["oracle"].sum())
    hit_excess = m["oracle"].astype(float) - base_rate                    # mean 0 under "no selection skill"
    ho = _ms(hit_excess, m["block"])
    # the null variance of a hit rate is known exactly, so the naive z is the binomial one (and the small-sample fallback)
    ho["z_naive"] = (hits - n * base_rate) / np.sqrt(n * base_rate * (1 - base_rate)) if 0 < base_rate < 1 else None
    if not ho["robust"]:
        ho["z"] = ho["z_naive"]
    out["oracle"] = {"rule": f"{cfg.analysis.oracle.mode} @ {oracle_h}s", "base_rate_pct": base_rate * 100,
                     "bot_entries_in_oracle": hits, "bot_rate_pct": hits / n * 100,
                     "lift": (hits / n) / base_rate if base_rate else None,
                     "z": ho["z"], "z_naive": ho["z_naive"], "robust": ho["robust"]}
    out["spread"] = {"bot_entries_avg_pips": float(m["spread"].mean()), "all_entries_avg_pips": float(np.mean([b["spread"] for b in base.values()]))}
    out["by_symbol"] = {s: {"n": int(len(g)), "net_pips": float(g["net_pips"].mean()), "selection_edge": float(g["sel"].mean())} for s, g in m.groupby("symbol")}
    out["by_exit_reason"] = {s: {"n": int(len(g)), "net_pips": float(g["net_pips"].mean())} for s, g in m.groupby("reason")}
    return out


def _oracle_for(g: pd.DataFrame, cfg: Config):
    """Oracle masks that need columns the slim label frame lacks (run / fixed_tp): fall back to top-decile on the horizon."""
    from core.config import OracleCfg
    return oracle_mask(g, OracleCfg(mode="top_decile", horizon_s=cfg.analysis.oracle.horizon_s, quantile=cfg.analysis.oracle.quantile))


def _f(x, d=2, sign=True) -> str:
    return "–" if x is None else (f"{x:+.{d}f}" if sign else f"{x:.{d}f}")


def render_html(ev: dict) -> str:
    s = ev["summary"]
    e = html.escape
    rows = [("Trades", s["trades"]), ("Net P&L", f"{s['net_usd']:+,.2f} USD ({s['return_pct']:+.2f}%)"),
            ("Win rate", _f(s["win_pct"], 1, False) + " %"), ("Avg net pips / trade", _f(s["avg_net_pips"])),
            ("Profit factor", _f(s["profit_factor"], 2, False)), ("Max drawdown", _f(s["max_drawdown_pct"], 2, False) + " %"),
            ("Avg hold", _f(s["avg_hold_s"], 0, False) + " s"), ("Exits", e(str(s["exit_reasons"]))),
            ("Refused / errors", f"{s['rejected']} / {s['errors']}" + (f" — HALTED: {e(s['halt_reason'])}" if s["halted"] else ""))]
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    extra = ""
    if "selection_edge_pips" in ev:
        def line(name, d, note=""):
            z = f" (z {d['z']:+.1f}{'' if d.get('robust') else ', naive'})" if d.get("z") is not None else ""
            return f"<tr><td>{name}</td><td>{_f(d['mean'])} pips{z}, n={d['n']}</td><td class=dim>{note}</td></tr>"
        o = ev["oracle"]
        extra = ("<h2>Against the labels and the oracle</h2><table>"
                 + line("Selection edge", ev["selection_edge_pips"], "label outcome of the bot's entries minus the average entry (net of spread)")
                 + line("… ex-spread", ev["selection_edge_ex_spread_pips"], "same with the entry spread added back: is it PRICE, not cheap fills?")
                 + line("Direction edge", ev["direction_edge_pips"], "bot's side minus the OPPOSITE side of the same entry (= 2x the price move in its favour): 0 for a coin-flip, whatever the volatility or spread")
                 + line("Exit effect", ev["exit_effect_pips"], "realised net pips minus the label's time-exit result")
                 + f"<tr><td>Oracle lift</td><td>{o['bot_rate_pct']:.1f}% vs {o['base_rate_pct']:.1f}% base → lift {_f(o['lift'], 2, False)} (z {_f(o['z'], 1)})</td><td class=dim>{e(o['rule'])}</td></tr>"
                 + f"<tr><td>Entry spread</td><td>{ev['spread']['bot_entries_avg_pips']:.2f} pips vs {ev['spread']['all_entries_avg_pips']:.2f} for all entries</td><td></td></tr></table>")
    elif ev.get("matching", {}).get("note"):
        extra = f"<p class=dim>{e(ev['matching']['note'])}</p>"
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Bot run</title>"
            "<style>body{background:#0b0b0b;color:#d0d0d0;font:14px system-ui;margin:0;padding:14px;max-width:900px}table{border-collapse:collapse;width:100%}"
            "td{padding:5px 8px;border-bottom:1px solid #222}td:first-child{color:#8a8a8a;white-space:nowrap}.dim{color:#777;font-size:12px}h2{font-size:16px;margin-top:22px}</style>"
            f"<h1>Bot run: {e(ev['bot'])}</h1><table>{body}</table>{extra}"
            "<p class=dim>z-scores are cluster-robust (trades in the same market hour count as one cluster): neighbouring trades share volatility, so a naive test overstates certainty.</p>"
            "<p class=dim>Simulated or historical replay. A replay is not evidence of future performance; on simulated data it is only a test of the harness.</p>")
