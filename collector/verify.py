"""Phase 1 "Done when" checks: compare the stored ticks with the broker's own history.

Run on the Windows machine with MT5 open, after the collector has covered the window:

    python -m collector.verify --hours 1            # the last full hour, all symbols
    python -m collector.verify --from 2025-03-04T13:00 --to 2025-03-04T14:00   # UTC

Checks (per symbol):
  count   stored count within 1% of MT5's count for the same window
  spot    10 random stored ticks match MT5 exactly (time, bid, ask)
  keys    no stored tick missing from MT5's window and none extra
          (run this after a stop/restart of the collector to prove "no gaps, no dupes")
"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from collector.mt5_client import BrokerClient
from collector.time_offset import MS_PER_HOUR
from core import db
from core.config import load_config


@dataclass
class SymbolReport:
    symbol: str
    stored: int = 0
    broker: int = 0
    count_ok: bool = False
    spot_checked: int = 0
    spot_mismatches: List[str] = field(default_factory=list)
    missing_from_db: int = 0
    extra_in_db: int = 0
    @property
    def ok(self) -> bool:
        return (self.count_ok and not self.spot_mismatches and self.spot_checked > 0
                and self.missing_from_db == 0 and self.extra_in_db == 0)


def _keyed(ticks, offset_h: int) -> Dict[Tuple[int, int], Tuple[float, float]]:
    out, prev, seq = {}, None, 0
    for t in ticks:
        seq = seq + 1 if t.time_msc == prev else 0
        prev = t.time_msc
        if t.bid > 0 and t.ask > 0:
            out[(t.time_msc - offset_h * MS_PER_HOUR, seq)] = (t.bid, t.ask)
    return out


def verify_symbol(conn: sqlite3.Connection, client: BrokerClient, symbol: str,
                  from_utc_ms: int, to_utc_ms: int, offset_h: int,
                  tolerance: float = 0.01, n_spot: int = 10, seed: Optional[int] = None
                  ) -> SymbolReport:
    rep = SymbolReport(symbol)
    stored = {(t, s): (b, a) for t, s, b, a in conn.execute(
        "SELECT t_ms, seq, bid, ask FROM ticks WHERE symbol=? AND t_ms BETWEEN ? AND ?",
        (symbol, from_utc_ms, to_utc_ms))}
    broker = _keyed(client.ticks_range(symbol, from_utc_ms + offset_h * MS_PER_HOUR,
                                       to_utc_ms + offset_h * MS_PER_HOUR), offset_h)
    rep.stored, rep.broker = len(stored), len(broker)
    rep.count_ok = rep.broker > 0 and abs(rep.stored - rep.broker) <= tolerance * rep.broker

    rng = random.Random(seed)
    sample = rng.sample(sorted(stored), min(n_spot, len(stored)))
    for key in sample:
        rep.spot_checked += 1
        b = broker.get(key)
        if b is None:
            rep.spot_mismatches.append(f"{key}: not in MT5 history")
        elif (b[0], b[1]) != stored[key]:
            rep.spot_mismatches.append(f"{key}: db={stored[key]} mt5={b}")
    rep.missing_from_db = len(broker.keys() - stored.keys())
    rep.extra_in_db = len(stored.keys() - broker.keys())
    return rep


def format_report(r: SymbolReport) -> str:
    return (f"{r.symbol}: {'PASS' if r.ok else 'FAIL'}  stored={r.stored} mt5={r.broker} "
            f"count_ok={r.count_ok} spot={r.spot_checked - len(r.spot_mismatches)}/{r.spot_checked} "
            f"missing={r.missing_from_db} extra={r.extra_in_db}"
            + "".join(f"\n    ! {m}" for m in r.spot_mismatches[:5]))


def _parse(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify stored ticks against MT5 history")
    ap.add_argument("--config"); ap.add_argument("--db")
    ap.add_argument("--hours", type=float, default=1.0, help="window = last N full hours")
    ap.add_argument("--from", dest="t_from"); ap.add_argument("--to", dest="t_to")
    args = ap.parse_args()
    cfg = load_config(args.config)
    conn = db.connect(args.db or cfg.resolve_db_path())
    off = db.get_state(conn, "offset_hours")
    if off is None:
        raise SystemExit("no offset_hours in db - has the collector run?")
    if args.t_from and args.t_to:
        a, b = _parse(args.t_from), _parse(args.t_to)
    else:
        end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        a = int((end - timedelta(hours=args.hours)).timestamp() * 1000)
        b = int(end.timestamp() * 1000) - 1

    from collector.mt5_client import MT5Client
    login = os.environ.get("MT5_LOGIN")
    client = MT5Client(terminal_path=os.environ.get("MT5_PATH"),
                       login=int(login) if login else None,
                       password=os.environ.get("MT5_PASSWORD"), server=os.environ.get("MT5_SERVER"))
    client.connect(cfg.broker_symbols)
    try:
        reports = [verify_symbol(conn, client, s, a, b, int(off)) for s in cfg.broker_symbols]
    finally:
        client.shutdown()
    for r in reports:
        print(format_report(r))
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
