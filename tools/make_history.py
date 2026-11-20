"""Write weeks/months of simulated history straight into the ticks database.

    python -m tools.make_history --weeks 9                 # 9 weeks up to now, into data/ticks.db
    python -m tools.make_history --weeks 9 --end 2026-11-13 --seed 7 --db /path/ticks.db

The ticks come from `collector.market_model` (a martingale price with realistic volatility, spreads and
activity; NO exploitable structure). They are exactly the ticks `python -m collector.collector --sim` would
serve, so the live collector carries on seamlessly from where this stops (same seed + offset required).
It also records digits / server offset / resume cursors in `collector_state`.
This is SIMULATED data: good for exercising the lab, worthless as evidence about real markets.
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np

from collector.market_model import DAY_MS, MarketModel, SPECS, seq_within_ms
from collector.time_offset import MS_PER_HOUR
from core import db
from core.config import load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate simulated tick history")
    ap.add_argument("--config"); ap.add_argument("--db")
    ap.add_argument("--weeks", type=float, default=9)
    ap.add_argument("--end", help="last day, YYYY-MM-DD (UTC, inclusive). Default: now")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--offset", type=int, default=3, help="broker server UTC offset in hours (must match --sim live)")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    symbols = cfg.broker_symbols
    bad = [s for s in symbols if s not in SPECS]
    if bad:
        raise SystemExit(f"no market model for {bad}; supported: {list(SPECS)}")
    conn = db.connect(a.db or cfg.resolve_db_path())
    if a.end:
        end_ms = int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp() * 1000) + DAY_MS - 1
    else:
        end_ms = int(time.time() * 1000)
    start_ms = (end_ms - int(a.weeks * 7 * DAY_MS)) // DAY_MS * DAY_MS
    model = MarketModel(a.seed)
    t0, total = time.time(), 0
    last: dict = {}
    for d in range(start_ms // DAY_MS, end_ms // DAY_MS + 1):
        for sym in symbols:
            t, bid, ask = model.day_ticks(sym, d)
            keep = t <= end_ms
            t, bid, ask = t[keep], bid[keep], ask[keep]
            if not len(t):
                continue
            seq = seq_within_ms(t)
            db.insert_ticks(conn, list(zip([sym] * len(t), t.tolist(), bid.tolist(), ask.tolist(), [0] * len(t), seq.tolist())))
            total += len(t)
            last[sym] = int(t[-1])
        day = (datetime(1970, 1, 1) + timedelta(days=d)).date()
        print(f"\r{day}  {total:>11,} ticks  {time.time() - t0:5.0f}s", end="", flush=True)
    print()
    for sym in symbols:
        db.set_state(conn, f"digits:{sym}", str(SPECS[sym].digits))
        if sym in last:
            db.set_state(conn, f"cursor:{sym}", str(last[sym] + a.offset * MS_PER_HOUR))
    db.set_state(conn, "offset_hours", str(a.offset))
    db.set_state(conn, "history_sim", f"market_model seed={a.seed} offset={a.offset}")
    print(f"done: {total:,} simulated ticks for {', '.join(symbols)} -> {a.db or cfg.resolve_db_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
