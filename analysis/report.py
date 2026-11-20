"""Report: for each feature, bucket its values and show win rate / average pips per bucket versus the
baseline, and how often the oracle's entries fall in each bucket. One HTML file + a JSON document.

    python -m analysis.report --from 2025-03-01 --to 2025-03-31                  # dates in the display timezone
    python -m analysis.report --from 2025-03-03 --to 2025-03-07 --session ny --max-spread 1.5 --symbol EURUSD,GBPUSD
    python -m analysis.report --last-days 5 --out reports/week.html --json reports/week.json

Buy and sell are separate populations (a feature like "last candle was up" pushes them in opposite
directions, so pooling would hide it). Per bucket:
    n / % of entries   how many labels, and how much of the population
    avg pips           mean net pips at each horizon;  win %  share with net pips > 0 (primary horizon)
    delta, z           bucket mean minus the population's baseline mean, and delta / standard error
    z ex-spread        the same test after ADDING BACK the entry spread to every outcome. Entry spread varies with
                       liquidity and many features proxy for it, so a bucket can look "bad" merely because it is
                       expensive. This column isolates whether PRICE tends to move your way (it still includes the
                       exit half-spread and slippage/commission, which are the same for every bucket)
    oracle %           share of ALL oracle entries that fall in this bucket
    lift               oracle % / bucket %   (> 1: the best entries over-populate this bucket)
Buckets with fewer than `analysis.min_bucket_n` samples are flagged unreliable.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from analysis.baseline import baseline, per_horizon, stats
from analysis.data import (FEATURE_PREFIX, HORIZON_COLS, Filters, date_range_ms, feature_columns,
                           load_frame, parse_session)
from analysis.oracle import oracle_mask
from core import db
from core.config import Config, OracleCfg, load_config

DISCRETE_MAX = 6      # features with <= this many distinct values are bucketed by value


# ====================================================================================== building
def _clean(o):
    """Make numpy / NaN values JSON-safe."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _fmt_num(x: float) -> str:
    return f"{x:.4g}"


def _assign_buckets(col: pd.Series, n_buckets: int):
    """Return (kind, list of (label, lo, hi, boolean mask)) for a feature column."""
    vals = col.dropna()
    uniq = np.unique(vals.to_numpy())
    buckets = []
    if len(uniq) <= DISCRETE_MAX:
        for u in uniq:
            lab = str(int(u)) if float(u).is_integer() else _fmt_num(u)
            buckets.append((lab, float(u), float(u), col == u))
        kind = "discrete"
    else:
        edges = np.unique(np.quantile(vals.to_numpy(), np.linspace(0, 1, n_buckets + 1)))
        if len(edges) < 2:
            edges = np.array([uniq[0], uniq[-1]])
        k = len(edges) - 1
        idx = np.searchsorted(edges[1:-1], col.to_numpy(dtype=float), side="right")
        for i in range(k):
            lo, hi = float(edges[i]), float(edges[i + 1])
            mask = pd.Series((idx == i) & col.notna().to_numpy(), index=col.index)
            lab = f"[{_fmt_num(lo)}, {_fmt_num(hi)}{']' if i == k - 1 else ')'}"
            buckets.append((lab, lo, hi, mask))
        kind = "continuous"
    miss = col.isna()
    if miss.any():
        buckets.append(("missing", None, None, miss))
    return kind, buckets


def feature_table(pop: pd.DataFrame, column: str, omask: pd.Series, horizons: List[int], primary: int,
                  n_buckets: int, min_n: int, base: Dict[str, dict]) -> dict:
    name = column[len(FEATURE_PREFIX):]
    if pop[column].notna().sum() == 0:
        return {"name": name, "kind": "empty", "buckets": []}
    kind, buckets = _assign_buckets(pop[column], n_buckets)
    n_pop, n_or = len(pop), int(omask.sum())
    pcol = HORIZON_COLS[primary]
    adj = pop[pcol] + pop["spread_pips"]                       # outcome with the entry spread added back
    base_adj = float(adj.mean())
    rows = []
    for label, lo, hi, m in buckets:
        sub = pop[m]
        n = int(len(sub))
        if n == 0:
            continue
        share = n / n_pop * 100
        o_in = int(omask[m].sum())
        row = {"label": label, "lo": lo, "hi": hi, "n": n, "share_pct": share, "unreliable": n < min_n,
               "oracle_n": o_in, "oracle_pct": (o_in / n_or * 100) if n_or else None,
               "oracle_rate_pct": o_in / n * 100, "lift": ((o_in / n_or) / (n / n_pop)) if n_or else None,
               "horizons": {}}
        for h in horizons:
            s = stats(sub[HORIZON_COLS[h]])
            b = base.get(str(h), {}).get("mean")
            s["delta"] = None if s["mean"] is None or b is None else s["mean"] - b
            s["z"] = None if s["delta"] is None or not s["se"] else s["delta"] / s["se"]
            row["horizons"][str(h)] = s
        sa = stats(adj[m])
        row["adj_delta"] = None if sa["mean"] is None else sa["mean"] - base_adj
        row["adj_z"] = None if not sa["se"] or row["adj_delta"] is None else row["adj_delta"] / sa["se"]
        row["win_pct"] = row["horizons"][str(primary)]["win_pct"]
        row["delta"] = row["horizons"][str(primary)]["delta"]
        row["z"] = row["horizons"][str(primary)]["z"]
        rows.append(row)
    return {"name": name, "kind": kind, "n_missing": int(pop[column].isna().sum()), "buckets": rows}


def _share_table(pop: pd.DataFrame, omask: pd.Series, key: str) -> dict:
    """Where the oracle entries are vs where all entries are (by symbol or by local hour)."""
    n_or = int(omask.sum())
    out = {}
    for k, g in pop.groupby(key):
        o = int(omask[g.index].sum())
        pct_pop = len(g) / len(pop) * 100
        pct_or = (o / n_or * 100) if n_or else None
        out[str(k)] = {"n": int(len(g)), "pop_pct": pct_pop, "oracle_n": o, "oracle_pct": pct_or,
                       "lift": (pct_or / pct_pop) if pct_or is not None and pct_pop else None}
    return out


def build_report(conn: sqlite3.Connection, cfg: Config, f: Filters, horizon_s: Optional[int] = None,
                 oracle_cfg: Optional[OracleCfg] = None) -> dict:
    a = cfg.analysis
    primary = horizon_s or a.horizon_s
    ocfg = oracle_cfg or a.oracle
    horizons = list(cfg.grader.time_exit_horizons_s)
    if primary not in horizons:
        raise ValueError(f"horizon {primary}s is not one of the graded horizons {horizons}")
    df = load_frame(conn, cfg, f)
    report: dict = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "filters": f.describe(cfg.display_timezone), "display_timezone": cfg.display_timezone,
            "primary_horizon_s": primary, "horizons_s": horizons, "min_bucket_n": a.min_bucket_n,
            "n_buckets": a.n_buckets, "n_labels": int(len(df)), "final_labels_only": True,
            "oracle": ocfg.model_dump(),
            "costs": {"extra_slippage_pips_per_fill": cfg.costs.extra_slippage_pips,
                      "commission_per_lot_round_trip": cfg.costs.commission_per_lot_round_trip,
                      "note": "values as configured NOW; labels were graded with the costs in force at grading time"},
            "coverage": None if df.empty else {
                "first": datetime.fromtimestamp(df.entry_t_ms.min() / 1000, tz=timezone.utc).isoformat(timespec="minutes"),
                "last": datetime.fromtimestamp(df.entry_t_ms.max() / 1000, tz=timezone.utc).isoformat(timespec="minutes"),
                "symbols": sorted(df.symbol.unique())},
        },
        "baseline": None, "populations": {},
    }
    if df.empty:
        report["meta"]["empty"] = True
        return _clean(report)

    report["baseline"] = baseline(df, horizons, primary)
    tested = reliable = hits = hits_adj = 0
    for d in ("buy", "sell"):
        pop = df[df.direction == d]
        if pop.empty:
            continue
        base = per_horizon(pop, horizons)
        omask, rule = oracle_mask(pop, ocfg)
        n_or = int(omask.sum())
        feats = [feature_table(pop, col, omask, horizons, primary, a.n_buckets, a.min_bucket_n, base)
                 for col in feature_columns(pop)]
        for ft in feats:
            for b in ft["buckets"]:
                tested += 1
                if not b["unreliable"]:
                    reliable += 1
                    hits += b["z"] is not None and abs(b["z"]) >= 2
                    hits_adj += b["adj_z"] is not None and abs(b["adj_z"]) >= 2
        report["populations"][d] = {
            "n": int(len(pop)), "baseline": base,
            "oracle": {"rule": rule, "n": n_or, "share_pct": n_or / len(pop) * 100,
                       "horizons": per_horizon(pop[omask], horizons) if n_or else {},
                       "avg_mfe_pips": float(pop.loc[omask, "mfe_pips"].mean()) if n_or else None,
                       "avg_mae_pips": float(pop.loc[omask, "mae_pips"].mean()) if n_or else None,
                       "avg_spread_pips": float(pop.loc[omask, "spread_pips"].mean()) if n_or else None,
                       "by_symbol": _share_table(pop, omask, "symbol") if n_or else {},
                       "by_hour": _share_table(pop, omask, "hour") if n_or else {}},
            "features": feats,
        }
    report["meta"]["multiple_testing"] = {
        "buckets_tested": tested, "reliable_buckets": reliable, "reliable_with_abs_z_ge_2": int(hits),
        "reliable_with_abs_adj_z_ge_2": int(hits_adj), "expected_by_chance": round(reliable * 0.0455, 1)}
    return _clean(report)


# ====================================================================================== HTML
CSS = """
:root{--bg:#0b0b0b;--panel:#141414;--line:#262626;--text:#d0d0d0;--dim:#8a8a8a;--pos:#2ebd6b;--neg:#e5534b;--acc:#4da3ff;--warn:#ff9f0a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:14px}h1{font-size:20px;margin:6px 0}h2{font-size:17px;margin:26px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{font-size:15px;margin:16px 0 4px}.dim{color:var(--dim)}.small{font-size:12px}.pos{color:var(--pos)}.neg{color:var(--neg)}
.chips span{display:inline-block;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1px 9px;margin:2px 4px 2px 0;font-size:12px}
.note{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--acc);border-radius:6px;padding:8px 12px;margin:10px 0;font-size:13px}
.warn{border-left-color:var(--warn)}.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px}th,td{padding:4px 8px;text-align:right;white-space:nowrap}
th{color:var(--dim);font-weight:500;border-bottom:1px solid var(--line);font-size:12px}td:first-child,th:first-child{text-align:left}
tbody tr{border-bottom:1px solid #1b1b1b}tr.unrel td{color:#666}tr.unrel .pos,tr.unrel .neg{opacity:.45}
.tag{font-size:10px;background:var(--warn);color:#000;border-radius:3px;padding:0 5px;margin-left:6px;font-weight:700}
.prim{background:#1a2230}.sig{font-weight:700}.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:10px 0}
.card h4{margin:0 0 4px;font-size:14px}.card .meta{color:var(--dim);font-size:12px}
.bar{display:inline-block;height:9px;vertical-align:middle;border-radius:2px}.barcell{width:34%}
.lift-hi{color:var(--warn);font-weight:700}.lift-lo{color:#6aa}
@media (max-width:640px){.sec{display:none}.wrap{padding:8px}th,td{padding:4px 5px}}
"""


def _p(x, d=2, sign=True) -> str:
    if x is None:
        return "–"
    s = f"{x:+.{d}f}" if sign else f"{x:.{d}f}"
    return s


def _cls(x) -> str:
    return "" if x is None else ("pos" if x > 0 else "neg" if x < 0 else "")


def _h(sec: int) -> str:
    return {30: "30s", 60: "60s", 120: "2m", 300: "5m"}.get(sec, f"{sec}s")


def _num(x, d=1) -> str:
    return "–" if x is None else f"{x:.{d}f}"


def render_html(rep: dict) -> str:
    m = rep["meta"]
    e = html.escape
    primary, horizons, min_n = m["primary_horizon_s"], m["horizons_s"], m["min_bucket_n"]
    f = m["filters"]
    chips = [f"{m['n_labels']:,} final labels", f"primary horizon {_h(primary)}", f"times in {m['display_timezone']}"]
    chips.append("symbols: " + (", ".join(f["symbols"]) if f["symbols"] else "all"))
    if f["from"] or f["to"]:
        chips.append(f"{f['from'] or '…'} → {f['to'] or '…'}")
    if f["session"]:
        chips.append("session " + f["session"])
    if f["max_spread_pips"] is not None:
        chips.append(f"spread ≤ {f['max_spread_pips']:g} pips")
    if f["direction"]:
        chips.append(f["direction"] + " only")
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>Scalping Lab report</title><style>{CSS}</style></head><body><div class=wrap>",
             "<h1>Scalping Lab — baseline, oracle &amp; feature report</h1>",
             f"<div class=chips>{''.join(f'<span>{e(c)}</span>' for c in chips)}</div>",
             f"<div class='dim small'>generated {e(m['generated_at'])} UTC</div>"]
    if m.get("empty"):
        parts.append("<div class='note warn'>No final labels match these filters. Widen the date range or relax the filters; "
                     "labels only exist once the collector and labeler have run.</div></div></body></html>")
        return "".join(parts)

    c = m["costs"]
    parts.append(
        "<div class=note><b>How to read this.</b> Every number is <b>net pips</b> after spread (buys fill at ask, sells at bid), "
        f"extra slippage ({c['extra_slippage_pips_per_fill']:g} pips per fill) and commission. The <b>baseline</b> is what taking "
        "<i>every</i> entry would have returned: any rule has to beat it, not zero. The <b>oracle</b> is an answer key built from "
        "hindsight — not a strategy. Buckets with fewer than "
        f"<b>{min_n}</b> samples are greyed and tagged <span class=tag>unreliable</span>.</div>")
    parts.append(
        "<div class='note warn'><b>Sample-size caution.</b> Entries are graded every minute with windows up to 5 minutes, so neighbouring "
        "entries overlap: the effective number of independent samples is smaller than <i>n</i> and the z-scores here are optimistic.</div>")

    # ---------------------------------------------------------------- baseline
    b = rep["baseline"]
    parts.append("<h2>1 · Baseline — take every entry</h2>")
    parts.append("<div class=tw><table><thead><tr><th>Horizon</th><th>n</th><th>avg pips</th><th>median</th><th>win %</th><th>std</th><th>95% CI of mean</th></tr></thead><tbody>")
    for h in horizons:
        s = b["overall"].get(str(h))
        if not s:
            continue
        ci = f"{s['ci95'][0]:+.2f} … {s['ci95'][1]:+.2f}" if s["ci95"] else "–"
        parts.append(f"<tr class='{'prim' if h == primary else ''}'><td>{_h(h)}</td><td>{s['n']:,}</td><td class='{_cls(s['mean'])}'>{_p(s['mean'])}</td>"
                     f"<td>{_p(s['median'])}</td><td>{_num(s['win_pct'])}</td><td>{_num(s['std'])}</td><td>{ci}</td></tr>")
    parts.append("</tbody></table></div>")

    parts.append("<h3>By direction and symbol — average net pips</h3><div class=tw><table><thead><tr><th></th><th>n</th>"
                 + "".join(f"<th>{_h(h)}</th>" for h in horizons) + "</tr></thead><tbody>")
    for title, grp in (("direction", b["by_direction"]), ("symbol", b["by_symbol"])):
        for k, v in grp.items():
            parts.append(f"<tr><td>{e(k)}</td><td>{v['n']:,}</td>"
                         + "".join(f"<td class='{_cls((v['horizons'].get(str(h)) or {}).get('mean'))} {'prim' if h == primary else ''}'>{_p((v['horizons'].get(str(h)) or {}).get('mean'))}</td>" for h in horizons)
                         + "</tr>")
    parts.append("</tbody></table></div>")

    mm = b["mfe_mae"]
    parts.append("<h3>Best / worst excursion within the window (net pips)</h3><div class=tw><table><thead><tr><th></th><th>mean</th><th>p10</th><th>p25</th><th>median</th><th>p75</th><th>p90</th></tr></thead><tbody>")
    for lab, key in (("MFE (best unrealised)", "mfe_pips"), ("MAE (worst unrealised)", "mae_pips"),
                     ("seconds to MFE", "t_to_mfe_s"), ("seconds to MAE", "t_to_mae_s")):
        d = mm[key]
        if d.get("n"):
            parts.append(f"<tr><td>{lab}</td>" + "".join(f"<td>{_num(d.get(k), 1)}</td>" for k in ("mean", "p10", "p25", "p50", "p75", "p90")) + "</tr>")
    parts.append("</tbody></table></div>")
    if b.get("fixed"):
        fx = b["fixed"]
        parts.append(f"<h3>Fixed SL/TP mode</h3><div class=note>outcomes: "
                     + ", ".join(f"{k} {v:.1f}%" for k, v in sorted(fx["outcomes_pct"].items()))
                     + f" · average {_p(fx['pnl']['mean'])} pips over {fx['n']:,} entries</div>")

    hours = b["by_hour"]
    if hours:
        scale = max(abs(v["mean"]) for v in hours.values() if v["mean"] is not None) or 1
        parts.append(f"<h3>By hour of day ({e(m['display_timezone'])}) — average pips at {_h(primary)}</h3><div class=tw><table><thead><tr><th>hour</th><th>n</th><th>avg</th><th>win %</th><th class=barcell></th></tr></thead><tbody>")
        for hr in sorted(hours, key=int):
            v = hours[hr]
            if v["mean"] is None:
                continue
            w = int(abs(v["mean"]) / scale * 100)
            col = "var(--pos)" if v["mean"] > 0 else "var(--neg)"
            un = " class=unrel" if v["n"] < min_n else ""
            parts.append(f"<tr{un}><td>{int(hr):02d}:00{'<span class=tag>unreliable</span>' if v['n'] < min_n else ''}</td><td>{v['n']:,}</td><td class='{_cls(v['mean'])}'>{_p(v['mean'])}</td>"
                         f"<td>{_num(v['win_pct'])}</td><td class=barcell><span class=bar style='width:{w}%;background:{col}'></span></td></tr>")
        parts.append("</tbody></table></div>")

    # ---------------------------------------------------------------- oracle
    parts.append("<h2>2 · Oracle — the entries that worked best in hindsight</h2>"
                 "<div class='note warn'>An answer key, not a trader. It is defined by outcomes that were unknowable at entry time; it only tells you what the market looked like just before the best entries.</div>")
    for d, pop in rep["populations"].items():
        o = pop["oracle"]
        parts.append(f"<h3>{d.upper()} — {e(o['rule']['text'])}</h3><div class=dim>{o['n']:,} of {pop['n']:,} entries ({o['share_pct']:.1f}%)"
                     f" · avg MFE {_p(o['avg_mfe_pips'])} · avg MAE {_p(o['avg_mae_pips'])} · avg spread {_num(o['avg_spread_pips'], 2)} pips</div>")
        if o["n"]:
            parts.append("<div class=tw><table><thead><tr><th>Oracle entries</th>" + "".join(f"<th>{_h(h)}</th>" for h in horizons) + "</tr></thead><tbody><tr><td>avg net pips</td>"
                         + "".join(f"<td class='{_cls((o['horizons'].get(str(h)) or {}).get('mean'))}'>{_p((o['horizons'].get(str(h)) or {}).get('mean'))}</td>" for h in horizons)
                         + "</tr></tbody></table></div>")
            for title, key in (("symbol", "by_symbol"), ("hour", "by_hour")):
                rows = o[key]
                if len(rows) > 1:
                    items = sorted(rows.items(), key=lambda kv: (int(kv[0]) if key == "by_hour" else kv[0]))
                    parts.append(f"<div class='small dim' style='margin-top:6px'>oracle share by {title} vs share of all entries (lift)</div><div class=tw><table><thead><tr><th>{title}</th><th>all entries %</th><th>oracle %</th><th>lift</th><th>n</th></tr></thead><tbody>")
                    for k, v in items:
                        un = v["n"] < min_n
                        lab = f"{int(k):02d}:00" if key == "by_hour" else e(k)
                        parts.append(f"<tr{' class=unrel' if un else ''}><td>{lab}{'<span class=tag>unreliable</span>' if un else ''}</td><td>{v['pop_pct']:.1f}</td><td>{_num(v['oracle_pct'])}</td>"
                                     f"<td class='{_lift_cls(v['lift'], un)}'>{_num(v['lift'], 2)}</td><td>{v['n']:,}</td></tr>")
                    parts.append("</tbody></table></div>")

    # ---------------------------------------------------------------- features
    parts.append("<h2>3 · Features — each bucket against the baseline and the oracle</h2>")
    parts.append(f"<div class=note>Highlighted column = primary horizon ({_h(primary)}). <b>Δ</b> = bucket average − baseline average; <b>z</b> = Δ / standard error "
                 "(bold when |z| ≥ 2 on a reliable bucket). <b>z ex-spr</b> repeats the test after adding the entry spread back to every outcome: "
                 "spread varies with liquidity and many features proxy for it, so a bucket can look bad merely because it is <i>expensive</i>; "
                 "this column asks whether <i>price</i> tends to move your way. <b>lift</b> &gt; 1 means the oracle's best entries over-populate the bucket.</div>")
    for d, pop in rep["populations"].items():
        base = pop["baseline"]
        pb = base.get(str(primary), {})
        parts.append(f"<h3>{d.upper()} entries — baseline at {_h(primary)}: {_p(pb.get('mean'))} pips, win {_num(pb.get('win_pct'))}% (n={pop['n']:,})</h3>")
        for ft in pop["features"]:
            if ft["kind"] == "empty":
                parts.append(f"<div class=card><h4>{e(ft['name'])}</h4><div class=meta>no values</div></div>")
                continue
            miss = f" · {ft['n_missing']:,} missing" if ft.get("n_missing") else ""
            parts.append(f"<div class=card><h4>{e(ft['name'])}</h4><div class=meta>{ft['kind']}{miss}</div><div class=tw><table><thead><tr><th>bucket</th><th>n</th><th class=sec>% of entries</th>"
                         + "".join(f"<th class='{'prim' if h == primary else 'sec'}'>avg {_h(h)}</th>" for h in horizons)
                         + f"<th class=sec>win %</th><th>Δ vs base</th><th>z</th><th title='z after adding back the entry spread'>z ex-spr</th><th class=sec>oracle %</th><th>lift</th></tr></thead><tbody>")
            for bk in ft["buckets"]:
                un = bk["unreliable"]
                sig = (not un) and bk["z"] is not None and abs(bk["z"]) >= 2
                avg = "".join(f"<td class='{_cls((bk['horizons'].get(str(h)) or {}).get('mean'))} {'prim' if h == primary else 'sec'}'>{_p((bk['horizons'].get(str(h)) or {}).get('mean'))}</td>" for h in horizons)
                parts.append(f"<tr{' class=unrel' if un else ''}><td>{e(bk['label'])}{'<span class=tag>unreliable<span class=sec> · n&lt;' + str(min_n) + '</span></span>' if un else ''}</td>"
                             f"<td>{bk['n']:,}</td><td class=sec>{bk['share_pct']:.1f}</td>{avg}<td class=sec>{_num(bk['win_pct'])}</td>"
                             f"<td class='{_cls(bk['delta'])} {'sig' if sig else ''}'>{_p(bk['delta'])}</td><td class='{'sig' if sig else ''}'>{_num(bk['z'], 1)}</td>"
                             f"<td class='{'sig' if (not un) and bk['adj_z'] is not None and abs(bk['adj_z']) >= 2 else ''}'>{_num(bk['adj_z'], 1)}</td>"
                             f"<td class=sec>{_num(bk['oracle_pct'])}</td><td class='{_lift_cls(bk['lift'], un)}'>{_num(bk['lift'], 2)}</td></tr>")
            parts.append("</tbody></table></div></div>")

    mt = m.get("multiple_testing")
    if mt:
        parts.append(f"<div class='note warn'><b>Multiple comparisons.</b> {mt['buckets_tested']} buckets were examined ({mt['reliable_buckets']} with n ≥ {min_n}); "
                     f"{mt['reliable_with_abs_z_ge_2']} of those reach |z| ≥ 2 on net pips (this includes buckets that are merely more expensive), and "
                     f"<b>{mt['reliable_with_abs_adj_z_ge_2']}</b> reach it once the entry spread is added back, while about {mt['expected_by_chance']} would be expected by chance alone "
                     "if there were no edge. Treat a single bucket that \"looks good\" as a hypothesis to confirm on fresh data, not a finding.</div>")
    parts.append("</div></body></html>")
    return "".join(parts)


def _lift_cls(lift, unreliable) -> str:
    if lift is None or unreliable:
        return ""
    return "lift-hi" if lift >= 1.25 else "lift-lo" if lift <= 0.8 else ""


# ====================================================================================== CLI
def filters_from_args(cfg: Config, symbols: Optional[str], date_from: Optional[str], date_to: Optional[str],
                      session: Optional[str], max_spread: Optional[float], direction: Optional[str],
                      tz: Optional[str] = None) -> Filters:
    lo, hi = date_range_ms(date_from, date_to, tz or cfg.display_timezone)
    syms = None
    if symbols:
        syms = [(s + cfg.symbol_suffix if s in cfg.symbols else s) for s in (x.strip() for x in symbols.split(",") if x.strip())]
        bad = [s for s in syms if s not in cfg.broker_symbols]
        if bad:
            raise ValueError(f"unknown symbol(s): {', '.join(bad)}")
    if direction not in (None, "buy", "sell"):
        raise ValueError("direction must be buy or sell")
    return Filters(symbols=syms, t_from_ms=lo, t_to_ms=hi, session=parse_session(session, cfg),
                   max_spread_pips=max_spread, direction=direction)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the baseline / oracle / feature report")
    ap.add_argument("--config"); ap.add_argument("--db")
    ap.add_argument("--from", dest="date_from", help="first day, YYYY-MM-DD (display timezone unless --utc)")
    ap.add_argument("--to", dest="date_to", help="last day INCLUSIVE, YYYY-MM-DD")
    ap.add_argument("--last-days", type=int, help="shortcut: the last N calendar days up to now")
    ap.add_argument("--utc", action="store_true", help="interpret --from/--to as UTC dates instead of the display timezone")
    ap.add_argument("--symbol", help="comma list, e.g. EURUSD,GBPUSD (default all)")
    ap.add_argument("--session", help="HH:MM-HH:MM in the display timezone, or 'ny' (ny_open..ny_session_end)")
    ap.add_argument("--max-spread", type=float, help="drop entries whose fill-tick spread exceeds this many pips")
    ap.add_argument("--direction", choices=["buy", "sell"])
    ap.add_argument("--horizon", type=int, help="primary horizon in seconds (30/60/120/300)")
    ap.add_argument("--oracle", choices=["top_decile", "run", "fixed_tp"], help="override analysis.oracle.mode")
    ap.add_argument("--out", help="HTML output path (default reports/report_<from>_<to>.html)")
    ap.add_argument("--json", dest="json_out", help="also write the JSON document here")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    conn = db.connect(a.db or cfg.resolve_db_path())
    tz = "UTC" if a.utc else cfg.display_timezone
    d_from, d_to = a.date_from, a.date_to
    if a.last_days:
        today = datetime.now(ZoneInfoShim(tz)).date()
        d_from, d_to = (today - timedelta(days=a.last_days - 1)).isoformat(), today.isoformat()
    try:
        f = filters_from_args(cfg, a.symbol, d_from, d_to, a.session, a.max_spread, a.direction, tz)
        ocfg = cfg.analysis.oracle.model_copy(update={"mode": a.oracle}) if a.oracle else None
        rep = build_report(conn, cfg, f, a.horizon, ocfg)
    except ValueError as e:
        print(f"error: {e}")
        return 2
    out = Path(a.out or f"reports/report_{d_from or 'start'}_{d_to or 'now'}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(rep), encoding="utf-8")
    if a.json_out:
        Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json_out).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    m = rep["meta"]
    print(f"{m['n_labels']:,} final labels -> {out}" + (f", {a.json_out}" if a.json_out else ""))
    if m.get("empty"):
        print("(no labels matched the filters)")
    return 0


def ZoneInfoShim(tz: str):
    from zoneinfo import ZoneInfo
    return ZoneInfo(tz)


if __name__ == "__main__":
    raise SystemExit(main())
