"""Replay a bot over stored history, then grade it against the labels and the oracle.

    python -m bot.run --bot random_entry --from 2026-10-05 --to 2026-10-09
    python -m bot.run --bot momentum_breakout --params '{"hold_s": 60}' --symbols EURUSD,GBPUSD --from 2026-10-05 --to 2026-10-16 --out run.html

The history is opened READ-ONLY and the paper account lives in memory, so a replay never touches your ticks or your real
account. Label the history first (`python -m grader.labeler_worker --once`) to get the oracle comparison.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from analysis.data import date_range_ms
from bot.evaluate import evaluate, render_html
from bot.runner import RiskLimits, run_replay
from bot.strategies import available, make_bot
from core.config import load_config
from tools.sandbox import open_sandbox


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Replay a bot over history")
    ap.add_argument("--config"); ap.add_argument("--db")
    ap.add_argument("--bot", help="bot name"); ap.add_argument("--list", action="store_true", help="list the available bots")
    ap.add_argument("--params", default="{}", help='JSON, e.g. \'{"hold_s": 60}\'')
    ap.add_argument("--limits", default="{}", help='JSON risk limits, e.g. \'{"max_open_positions": 2}\'')
    ap.add_argument("--symbols", help="comma list (default: all configured)")
    ap.add_argument("--from", dest="d_from", required=False); ap.add_argument("--to", dest="d_to", required=False)
    ap.add_argument("--utc", action="store_true")
    ap.add_argument("--forward", type=int, default=0, metavar="SECONDS",
                    help="TRAINING / ANALYSIS run: let the bot see the next SECONDS of ticks (all its orders are then tagged peeked). Default 0 = blind")
    ap.add_argument("--balance", type=float, help="starting paper balance (default from config; a large value avoids ruin truncating a run)")
    ap.add_argument("--horizon", type=int, help="label horizon (30/60/120/300 s) for the entry-quality metrics; default analysis.horizon_s")
    ap.add_argument("--out", help="HTML report"); ap.add_argument("--json", dest="json_out")
    a = ap.parse_args(argv)
    if a.list:
        for b in available():
            print(f"{b['name']}: {b['description']}")
            for k, v in b["params"].items():
                print(f"    {k} = {v['default']}   {v['help']}")
        return 0
    if not (a.bot and a.d_from and a.d_to):
        ap.error("--bot, --from and --to are required")
    cfg = load_config(a.config)
    try:
        bot = make_bot(a.bot, json.loads(a.params))
        limits = RiskLimits.from_dict(json.loads(a.limits))
        syms = [s.strip() + ("" if s.strip() in cfg.broker_symbols else cfg.symbol_suffix) for s in a.symbols.split(",")] if a.symbols else cfg.broker_symbols
        lo, hi = date_range_ms(a.d_from, a.d_to, "UTC" if a.utc else cfg.display_timezone)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}")
        return 2
    path = str(Path(a.db or cfg.resolve_db_path()).resolve())
    conn = open_sandbox(path)
    t0 = time.time()
    try:
        res = run_replay(cfg, conn, bot, syms, lo, hi, limits, a.balance, forward_s=a.forward,
                         progress=lambda n, tot: print(f"\r  {n:,} decision steps", end="", flush=True))
    except ValueError as e:
        print(f"error: {e}")
        return 2
    try:
        ev = evaluate(conn, cfg, res, a.horizon)
    except ValueError as e:
        print(f"error: {e}")
        return 2
    s = ev["summary"]
    if a.forward:
        print(f"\n*** FORWARD-FED RUN: the bot saw the next {a.forward}s; every trade is tagged peeked. This is hindsight, not performance. ***", end="")
    print(f"\n{bot.name} on {', '.join(syms)}: {s['trades']} trades in {time.time() - t0:.0f}s | net {s['net_usd']:+,.2f} USD | "
          f"avg {s['avg_net_pips'] if s['avg_net_pips'] is None else round(s['avg_net_pips'], 2)} net pips/trade | "
          f"win {s['win_pct'] if s['win_pct'] is None else round(s['win_pct'], 1)}% | exits {s['exit_reasons']}"
          + (f" | HALTED: {s['halt_reason']}" if s["halted"] else ""))
    if "selection_edge_pips" in ev:
        e1, e2, o, de = ev["selection_edge_pips"], ev["selection_edge_ex_spread_pips"], ev["oracle"], ev["direction_edge_pips"]
        print(f"  selection edge {e1['mean']:+.2f} pips (z {e1['z']:+.1f}); ex-spread {e2['mean']:+.2f} (z {e2['z']:+.1f}); "
              f"oracle lift {o['lift']:.2f} ({o['bot_rate_pct']:.1f}% vs {o['base_rate_pct']:.1f}% base, z {o['z']:+.1f}); "
              f"DIRECTION edge {de['mean']:+.2f} pips (z {de['z']:+.1f})   [cluster-robust z; direction edge = bot's side vs the opposite side of the same entry]")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(render_html(ev), encoding="utf-8")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(ev, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
