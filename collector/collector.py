"""Tick collector: MT5 (or the simulated feed) -> SQLite, UTC milliseconds.

Run:  python -m collector.collector            (real MT5, Windows)
      python -m collector.collector --sim      (simulated feed, any OS)

Design notes
- The resume cursor is kept in broker SERVER time, so a DST change in the offset can
  never make a restart skip or repeat data.
- Each poll re-requests from the floor-second of the cursor. Overlap is harmless:
  ticks are keyed (symbol, t_ms, seq) with seq = position inside that millisecond, and
  inserted with INSERT OR IGNORE.
- Right after the market (re)opens, fetching is held until the server-time offset has
  been re-verified from a fresh tick, so a weekend DST switch never mis-stamps ticks.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import threading
import time
from typing import Callable, Dict, List, Optional

from collector.market_hours import market_open
from collector.mt5_client import BrokerClient, Tick
from collector.time_offset import MS_PER_HOUR, OffsetTracker, server_to_utc_ms, utc_to_server_ms
from core import db
from core.config import Config, load_config

log = logging.getLogger("collector")


def system_clock_ms() -> int:
    return int(time.time() * 1000)


class Collector:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, client: BrokerClient,
                 clock: Callable[[], int] = system_clock_ms):
        self.cfg, self.conn, self.client, self.clock = cfg, conn, client, clock
        self.symbols: List[str] = cfg.broker_symbols
        self.offset = OffsetTracker(cfg.collector.offset_recheck_seconds)
        self.cursor: Dict[str, int] = {}          # last server-time ms seen, per symbol
        self.last_activity_ms: Dict[str, int] = {}  # UTC ms of newest tick (or start/reopen)
        self.gap_logged: Dict[str, bool] = {s: False for s in self.symbols}
        self.backfilled = False
        self._was_open: Optional[bool] = None
        self._offset_verified = True               # False while waiting after a reopen
        self._warned_unverified = False

    # ------------------------------------------------------------------ startup
    def start(self) -> None:
        now = self.clock()
        self.client.connect(self.symbols)
        for s in self.symbols:
            db.set_state(self.conn, f"digits:{s}", str(self.client.digits(s)))
            v = db.get_state(self.conn, f"cursor:{s}")
            if v is not None:
                self.cursor[s] = int(v)
            last = db.last_tick_ms(self.conn, s)
            self.last_activity_ms[s] = max(last or 0, now)  # grace: measure gaps from start
        persisted = db.get_state(self.conn, "offset_hours")
        if persisted is not None:
            self.offset.offset_hours = int(persisted)
        self._try_update_offset(now)
        log.info("collector started; offset=%s symbols=%s", self.offset.offset_hours, self.symbols)

    # ------------------------------------------------------------------ offset
    def _freshest_server_ms(self) -> Optional[int]:
        best = None
        for s in self.symbols:
            t = self.client.last_tick_server_ms(s)
            if t is not None and (best is None or t > best):
                best = t
        return best

    def _try_update_offset(self, now: int) -> bool:
        """Re-estimate the offset from the freshest tick across all symbols."""
        is_open = market_open(now)
        if not is_open:
            return False
        latest = self._freshest_server_ms()
        if latest is None:
            return False
        changed, old, new = self.offset.update(latest, now, is_open)
        ok = self.offset.last_check_ms == now
        if ok:
            db.set_state(self.conn, "offset_hours", str(new))
        if changed:
            db.log_event(self.conn, now, None, "offset_change", f"{old}->{new}")
            log.warning("server UTC offset changed %s -> %s hours", old, new)
        return ok

    # ------------------------------------------------------------------ storing
    def _store(self, symbol: str, ticks: List[Tick]) -> int:
        """Convert to UTC, assign seq, insert. Returns rows newly inserted."""
        if not ticks:
            return 0
        off = self.offset.offset_hours
        assert off is not None
        rows, prev_ms, seq = [], None, 0
        for t in ticks:
            seq = seq + 1 if t.time_msc == prev_ms else 0
            prev_ms = t.time_msc
            if t.bid <= 0 or t.ask <= 0:   # seq is assigned first, so skipping keeps it stable
                continue
            rows.append((symbol, server_to_utc_ms(t.time_msc, off), t.bid, t.ask, t.flags, seq))
        inserted = db.insert_ticks(self.conn, rows)
        newest = max(t.time_msc for t in ticks)
        if newest > self.cursor.get(symbol, -1):
            if newest > self.cursor.get(symbol, -1) and rows:
                self.last_activity_ms[symbol] = max(self.last_activity_ms.get(symbol, 0),
                                                    server_to_utc_ms(newest, off))
                self.gap_logged[symbol] = False
            self.cursor[symbol] = newest
            db.set_state(self.conn, f"cursor:{symbol}", str(newest))  # after the insert
        return inserted

    # ------------------------------------------------------------------ backfill
    def backfill(self) -> int:
        """Fetch history on start / catch up after downtime, in chunks, oldest first."""
        now = self.clock()
        off = self.offset.offset_hours
        assert off is not None
        now_srv = utc_to_server_ms(now, off)
        chunk = self.cfg.collector.backfill_chunk_minutes * 60_000
        total = 0
        for s in self.symbols:
            start = self.cursor.get(s)
            if start is None:
                start = now_srv - int(self.cfg.collector.backfill_hours_on_start * MS_PER_HOUR)
            a = start
            while a <= now_srv:
                b = min(a + chunk - 1, now_srv)
                total += self._store(s, self.client.ticks_range(s, a, b))
                a = b + 1
            log.info("backfill %s: cursor now %s", s, self.cursor.get(s))
        self.backfilled = True
        return total

    # ------------------------------------------------------------------ polling
    def poll_once(self) -> int:
        """One polling pass over all symbols. Returns ticks newly inserted."""
        now = self.clock()
        is_open = market_open(now)

        if is_open and self._was_open is not True:
            # market just (re)opened: restart gap timers, require a fresh offset check
            for s in self.symbols:
                self.last_activity_ms[s] = max(self.last_activity_ms.get(s, 0), now)
                self.gap_logged[s] = False
            self._offset_verified = False
            self._warned_unverified = False
        if not is_open:
            for s in self.symbols:
                self.gap_logged[s] = False
        self._was_open = is_open

        if is_open and (not self._offset_verified or self.offset.due(now)):
            if self._try_update_offset(now):
                self._offset_verified = True
        if self.offset.offset_hours is None:
            return 0                         # cannot stamp ticks in UTC yet
        if is_open and not self._offset_verified:
            if not self._warned_unverified:
                log.info("waiting for a fresh tick to verify the server offset")
                self._warned_unverified = True
            return 0

        if not self.backfilled:
            inserted = self.backfill()
        else:
            inserted = 0
            batch = self.cfg.collector.batch_size
            for s in self.symbols:
                while True:
                    cur = self.cursor.get(s)
                    if cur is None:     # symbol never had ticks: start from the backfill window
                        off = self.offset.offset_hours
                        cur = utc_to_server_ms(now, off) - int(
                            self.cfg.collector.backfill_hours_on_start * MS_PER_HOUR)
                    ticks = self.client.ticks_from(s, cur, batch)
                    inserted += self._store(s, ticks)
                    if len(ticks) < batch or self.cursor.get(s, 0) // 1000 <= cur // 1000:
                        break

        if is_open:
            self._check_gaps(now)
        return inserted

    def _check_gaps(self, now: int) -> None:
        limit = self.cfg.collector.gap_seconds * 1000
        for s in self.symbols:
            idle = now - self.last_activity_ms.get(s, now)
            if idle > limit and not self.gap_logged[s]:
                self.gap_logged[s] = True
                db.log_event(self.conn, now, s, "gap", f"no ticks for {idle // 1000}s")
                log.warning("gap on %s: no ticks for %ss", s, idle // 1000)

    # ------------------------------------------------------------------ main loop
    def run(self, stop: threading.Event, sleep: Callable[[float], None] | None = None) -> None:
        sleep = sleep or stop.wait
        backoff = 1.0
        started = False
        while not stop.is_set():
            try:
                if not started:
                    self.start()
                    started = True
                self.poll_once()
                backoff = 1.0
                ms = (self.cfg.collector.poll_ms if market_open(self.clock())
                      else self.cfg.collector.idle_poll_ms)
                sleep(ms / 1000.0)
            except Exception as e:  # noqa: BLE001 - any failure -> reconnect with backoff
                log.exception("poll failed, reconnecting")
                try:
                    db.log_event(self.conn, self.clock(), None, "reconnect", repr(e)[:300])
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.client.shutdown()
                except Exception:  # noqa: BLE001
                    pass
                sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                started = False
        try:
            self.client.shutdown()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Tick collector")
    ap.add_argument("--config", default=None)
    ap.add_argument("--sim", action="store_true", help="realistic simulated market (collector/market_model.py; no MT5 needed)")
    ap.add_argument("--sim-simple", action="store_true", help="the old toy feed with sine waves (tests only)")
    ap.add_argument("--seed", type=int, default=1, help="--sim seed (must match tools.make_history)")
    ap.add_argument("--offset", type=int, default=3, help="--sim broker server UTC offset hours")
    ap.add_argument("--db", default=None, help="override db path")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    conn = db.connect(args.db or cfg.resolve_db_path())
    if args.sim:
        from collector.market_model import MarketSimClient
        client: BrokerClient = MarketSimClient(system_clock_ms, args.offset, args.seed)
    elif args.sim_simple:
        from collector.sim_client import SimClient
        client = SimClient(system_clock_ms)
    else:
        from collector.mt5_client import MT5Client
        login = os.environ.get("MT5_LOGIN")
        client = MT5Client(terminal_path=os.environ.get("MT5_PATH"),
                           login=int(login) if login else None,
                           password=os.environ.get("MT5_PASSWORD"),
                           server=os.environ.get("MT5_SERVER"))
    stop = threading.Event()
    try:
        Collector(cfg, conn, client).run(stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
