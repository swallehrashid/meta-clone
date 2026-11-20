"""Check the paper-trading engine against months of history, safely.

    python -m tools.verify_engine --trades 1500                 # random trades over the whole database
    python -m tools.verify_engine --trades 800 --slip 0.15 --commission 7 --seed 3 --grader 300

What it does (the history database is opened READ-ONLY; the engine's own account tables live in memory, so your
real paper account and the ticks file are never touched):
  1. replays N random trades (any pair, side, size, optional SL/TP, overlapping) through the engine and through an
     independent slow tick-by-tick reference (tools/engine_reference.py) and requires an exact match;
  2. places grader-style entries (open at the fill tick, close at fill + h) and requires the engine's net pips to equal
     the Phase 3 grader's label for the same entry;
  3. holds one position for several trading days with no SL/TP, to exercise a long replay across weekends.
The data may be simulated (tools.make_history) - then this proves the ENGINE is right, not that any strategy works.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

from core import db
from core.config import load_config
from engine.account import Engine
from grader.grader import grade
from tools.sandbox import open_sandbox
from tools.engine_reference import compare, mid, newest, random_trades, run_engine, usd_per_quote

DAY_MS = 86_400_000


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify the engine on history")
    ap.add_argument("--config"); ap.add_argument("--db")
    ap.add_argument("--trades", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--slip", type=float, default=0.15, help="extra slippage pips per fill")
    ap.add_argument("--commission", type=float, default=7.0, help="USD per lot round trip")
    ap.add_argument("--grader", type=int, default=200, help="grader-parity entries per USD pair")
    ap.add_argument("--hold-days", type=int, default=5, help="length of the long-hold check (0 = skip)")
    ap.add_argument("--json", help="write the summary here")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    cfg.costs.extra_slippage_pips, cfg.costs.commission_per_lot_round_trip = a.slip, a.commission
    cfg.engine.starting_balance, cfg.engine.leverage = 1e8, 500            # no margin interference: this is about P&L
    path = str(Path(a.db or cfg.resolve_db_path()).resolve())
    conn = open_sandbox(path)
    syms = cfg.broker_symbols
    lo, hi = conn.execute("SELECT MIN(t_ms), MAX(t_ms) FROM ticks").fetchone()
    n_ticks = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    print(f"history: {n_ticks:,} ticks, {(hi - lo) / DAY_MS:.1f} days, symbols {', '.join(syms)}")
    summary = {"history_days": round((hi - lo) / DAY_MS, 1), "ticks": n_ticks, "ok": True}

    # ---- 1. engine vs independent reference
    t0 = time.time()
    trades = random_trades(conn, cfg, syms, lo + DAY_MS, hi - DAY_MS, a.trades, a.seed)
    eng, pos = run_engine(cfg, conn, trades)
    r = compare(eng, pos, conn, cfg, trades)
    r["seconds"] = round(time.time() - t0, 1)
    summary["reference"] = r
    ok1 = not r["mismatches"] and r["balance_matches_reference"] and r["open_positions_left"] == 0
    print(f"[1] random trades vs independent reference: {r['trades']} trades, exits {r['exit_reasons']}, "
          f"mismatches {len(r['mismatches'])}, worst pip error {r['worst_pips_error']:.2e}, "
          f"balance {'matches' if r['balance_matches_reference'] else 'DIFFERS'} ({r['seconds']}s)  -> {'PASS' if ok1 else 'FAIL'}")
    for m in r["mismatches"][:5]:
        print("     ", m)

    # ---- 2. engine vs the Phase 3 grader
    rng = random.Random(a.seed + 1)
    eng2 = Engine(cfg, conn)
    g_checked = g_bad = 0
    worst = 0.0
    t0 = time.time()
    for sym in [s for s in syms if s.endswith("USD")]:                   # USD quote: pip value is constant, comparison is exact
        done = 0
        for _ in range(a.grader * 6):
            entry = rng.randrange(lo + DAY_MS, hi - DAY_MS)
            direction, h = rng.choice(["buy", "sell"]), rng.choice([30, 60, 120, 300])
            first = conn.execute("SELECT t_ms FROM ticks WHERE symbol=? AND t_ms>=? ORDER BY t_ms, seq LIMIT 2", (sym, entry)).fetchall()
            if len(first) < 2 or first[0][0] == first[1][0] or first[0][0] - entry > 5000:
                continue
            lab = grade(conn, sym, entry, direction, cfg)
            if lab is None or lab.status != "final":
                continue
            t_e = first[0][0]
            eng2.reset(balance=1e8, leverage=500)
            rid = eng2.place_order(sym, direction, volume=1.0, at_ms=t_e)["id"]
            d = eng2.close(rid, at_ms=t_e + h * 1000)["deal"]
            err = abs(d["net_pips"] - getattr(lab, f"pnl_{h}s"))
            worst = max(worst, err)
            g_bad += err > 1e-6
            g_checked += 1
            done += 1
            if done >= a.grader:
                break
    ok2 = g_checked > 0 and g_bad == 0
    summary["grader"] = {"checked": g_checked, "mismatches": g_bad, "worst_net_pips_error": worst, "seconds": round(time.time() - t0, 1)}
    print(f"[2] engine vs grader labels: {g_checked} entries on {', '.join(s for s in syms if s.endswith('USD'))}, "
          f"mismatches {g_bad}, worst net-pips error {worst:.2e}  -> {'PASS' if ok2 else 'FAIL'}")

    # ---- 3. a long hold across weekends
    ok3 = True
    if a.hold_days:
        sym = syms[0]
        eng3 = Engine(cfg, conn)
        eng3.reset(balance=1e8, leverage=500)
        start = lo + 7 * DAY_MS
        while conn.execute("SELECT 1 FROM ticks WHERE symbol=? AND t_ms BETWEEN ? AND ? LIMIT 1", (sym, start - 60_000, start)).fetchone() is None:
            start += 3_600_000
        pid = eng3.place_order(sym, "buy", volume=1.0, at_ms=start)["id"]
        end = start + a.hold_days * DAY_MS
        t0 = time.time()
        n = eng3.advance_to(end)
        dt = time.time() - t0
        m = eng3.positions_view()[0]
        q = newest(conn, sym, end)
        want_px = q[2]                                                      # bid, the exit side of a long
        ok3 = abs(m["price"] - q[2]) < 1e-12 and eng3.account()["t_engine_ms"] == end and len(eng3.equity_curve()) > 20
        summary["long_hold"] = {"days": a.hold_days, "ticks_replayed": n, "seconds": round(dt, 1),
                                "ticks_per_second": round(n / dt) if dt else None, "equity_points": len(eng3.equity_curve())}
        print(f"[3] one position held {a.hold_days} days across a weekend: {n:,} ticks replayed in {dt:.1f}s "
              f"({n / dt:,.0f}/s), marked on the newest bid, {len(eng3.equity_curve())} equity points  -> {'PASS' if ok3 else 'FAIL'}")

    summary["ok"] = bool(ok1 and ok2 and ok3)
    print("\nENGINE VERIFIED on this history" if summary["ok"] else "\nENGINE CHECK FAILED")
    if a.json:
        Path(a.json).write_text(json.dumps(summary, indent=1, default=str))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
