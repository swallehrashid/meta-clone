"""Collector behaviour against the deterministic simulated feed."""
import threading
from datetime import datetime, timezone

import pytest

from collector.collector import Collector
from collector.sim_client import SimClient
from collector.time_offset import MS_PER_HOUR
from core import db
from core.config import load_config


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


@pytest.fixture
def cfg():
    c = load_config()
    c.symbols = ["EURUSD", "EURJPY"]
    c.collector.backfill_hours_on_start = 0.05    # 3 min of history keeps tests fast
    c.collector.backfill_chunk_minutes = 1
    return c


def expected_rows(client, symbol, start_srv, end_srv, offset_h):
    """Reference: what the feed holds for [start, end], keyed exactly like the DB."""
    out, prev, seq = {}, None, 0
    for t in client.ticks_range(symbol, start_srv, end_srv):
        seq = seq + 1 if t.time_msc == prev else 0
        prev = t.time_msc
        out[(t.time_msc - offset_h * MS_PER_HOUR, seq)] = (t.bid, t.ask)
    return out


def db_rows(conn, symbol):
    return {(t, s): (b, a) for t, b, a, s in
            conn.execute("SELECT t_ms,bid,ask,seq FROM ticks WHERE symbol=? ORDER BY t_ms,seq", (symbol,))}


def assert_matches_feed(conn, sim, symbol, offset_h):
    """Stored ticks == exactly what the feed holds from the first stored tick to now."""
    got = db_rows(conn, symbol)
    assert got, f"{symbol}: nothing stored"
    first_srv = min(got)[0] + offset_h * MS_PER_HOUR
    want = expected_rows(sim, symbol, first_srv, sim._server_now(), offset_h)
    assert got == want, f"{symbol}: {len(got)} stored vs {len(want)} in feed"
    return got


def run_until(col, clock, end_ms, step_ms=250):
    while clock.t < end_ms:
        clock.t += step_ms
        col.poll_once()


T0 = ms(2025, 3, 4, 14, 0, 0)   # Tuesday, market open


def test_collects_exactly_what_the_feed_has(cfg):
    clock = Clock(T0)
    sim = SimClient(clock, offset_hours=3)
    conn = db.connect(":memory:")
    col = Collector(cfg, conn, sim, clock); col.start()
    run_until(col, clock, T0 + 60_000)

    for sym in cfg.broker_symbols:
        assert len(assert_matches_feed(conn, sim, sym, 3)) > 100
    # ms collisions exist in the data (seq > 0 rows), and nothing was stored with seq gaps
    assert conn.execute("SELECT COUNT(*) FROM ticks WHERE seq>0").fetchone()[0] > 0


def test_ticks_are_stored_in_utc(cfg):
    clock = Clock(T0)
    sim = SimClient(clock, offset_hours=3)
    conn = db.connect(":memory:")
    col = Collector(cfg, conn, sim, clock); col.start()
    run_until(col, clock, T0 + 10_000)
    newest = conn.execute("SELECT MAX(t_ms) FROM ticks").fetchone()[0]
    assert T0 + 8_000 < newest <= clock.t        # UTC, not server time (+3h)


def test_restart_leaves_no_duplicates_and_no_missing(cfg, tmp_path):
    path = tmp_path / "t.db"
    clock = Clock(T0)
    sim = SimClient(clock, offset_hours=3)

    col1 = Collector(cfg, db.connect(path), sim, clock); col1.start()
    run_until(col1, clock, T0 + 40_000)
    n_before = db.connect(path).execute("SELECT COUNT(*) FROM ticks").fetchone()[0]

    clock.t += 25_000                       # collector is down for 25 s, feed keeps ticking
    conn2 = db.connect(path)
    col2 = Collector(cfg, conn2, sim, clock); col2.start()   # fresh process, same db
    run_until(col2, clock, clock.t + 40_000)

    for sym in cfg.broker_symbols:
        assert_matches_feed(conn2, sim, sym, 3)
    total = conn2.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    distinct = conn2.execute("SELECT COUNT(*) FROM (SELECT DISTINCT symbol,t_ms,seq FROM ticks)").fetchone()[0]
    assert total == distinct > n_before


def test_restart_even_if_cursor_state_is_lost(cfg, tmp_path):
    """Dedup must hold on the primary key alone, not only on the saved cursor."""
    path = tmp_path / "t.db"
    clock = Clock(T0)
    sim = SimClient(clock, offset_hours=3)
    col1 = Collector(cfg, db.connect(path), sim, clock); col1.start()
    run_until(col1, clock, T0 + 20_000)
    conn = db.connect(path)
    conn.execute("DELETE FROM collector_state WHERE key LIKE 'cursor:%'"); conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    col2 = Collector(cfg, conn, sim, clock); col2.start()    # re-backfills the same window
    after = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    assert after >= before
    for sym in cfg.broker_symbols:
        assert_matches_feed(conn, sim, sym, 3)


def test_gap_logged_once_when_market_open(cfg):
    clock = Clock(T0)
    sim = SimClient(clock, offset_hours=3)
    conn = db.connect(":memory:")
    col = Collector(cfg, conn, sim, clock); col.start()
    run_until(col, clock, T0 + 5_000)
    sim._max_tps = 0                                  # feed goes silent
    run_until(col, clock, clock.t + 200_000, step_ms=1000)
    gaps = conn.execute("SELECT symbol FROM collector_log WHERE event='gap'").fetchall()
    assert sorted(g[0] for g in gaps) == sorted(cfg.broker_symbols)   # once per symbol


def test_no_gaps_logged_while_market_closed(cfg):
    sat = ms(2025, 3, 8, 12, 0, 0)
    clock = Clock(sat)
    sim = SimClient(clock, offset_hours=3)
    conn = db.connect(":memory:")
    col = Collector(cfg, conn, sim, clock); col.start()
    run_until(col, clock, sat + 600_000, step_ms=5000)
    assert conn.execute("SELECT COUNT(*) FROM collector_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0] == 0


def test_weekend_start_waits_then_collects_after_reopen_with_new_dst_offset(cfg):
    """Broker flips +2 -> +3 over the weekend; first ticks after reopen must use +3."""
    sat = ms(2025, 3, 8, 12, 0, 0)
    reopen = ms(2025, 3, 9, 21, 0, 0)               # Sun 17:00 NY (EDT)
    clock = Clock(sat)
    sim = SimClient(clock, offset_hours=2, offset_hours_fn=lambda u: 2 if u < reopen - 3600_000 else 3)
    conn = db.connect(":memory:")
    db.set_state(conn, "offset_hours", "2")          # what the previous (winter) run left behind
    col = Collector(cfg, conn, sim, clock); col.start()
    run_until(col, clock, reopen - 1000, step_ms=60_000)
    assert conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0] == 0
    clock.t = reopen
    run_until(col, clock, reopen + 30_000)
    assert col.offset.offset_hours == 3
    ev = conn.execute("SELECT event, detail FROM collector_log WHERE event='offset_change'").fetchall()
    assert ev == [("offset_change", "2->3")]
    newest = conn.execute("SELECT MAX(t_ms) FROM ticks").fetchone()[0]
    assert reopen <= newest <= clock.t               # stamped with the corrected offset


def test_reconnects_after_failure_and_loses_nothing(cfg):
    clock = Clock(T0)
    sim = SimClient(clock, offset_hours=3)
    conn = db.connect(":memory:")
    col = Collector(cfg, conn, sim, clock)
    stop = threading.Event()
    state = {"n": 0}

    def fake_sleep(sec):
        clock.t += int(sec * 1000)
        state["n"] += 1
        if state["n"] == 3:
            sim.fail_next = 2                        # next data calls blow up
        if state["n"] > 40:
            stop.set()

    col.run(stop, sleep=fake_sleep)
    assert conn.execute("SELECT COUNT(*) FROM collector_log WHERE event='reconnect'").fetchone()[0] >= 1
    sim.connected = True
    for sym in cfg.broker_symbols:
        assert_matches_feed(conn, sim, sym, 3)


def test_ticks_held_back_until_offset_verified_after_reopen(cfg):
    """If data flows before a fresh tick can verify the offset, nothing is stamped yet.

    Otherwise ticks right after a weekend DST switch would be stored an hour off.
    """
    reopen = ms(2025, 3, 9, 21, 0, 0)
    clock = Clock(reopen - 3600_000)

    class LateVerify(SimClient):
        hide_until = reopen + 20_000
        def last_tick_server_ms(self, symbol):          # offset probe unavailable at first
            return None if self._now() < self.hide_until else super().last_tick_server_ms(symbol)

    sim = LateVerify(clock, offset_hours=3)             # broker already on the new offset
    conn = db.connect(":memory:")
    db.set_state(conn, "offset_hours", "2")             # stale persisted (winter) value
    col = Collector(cfg, conn, sim, clock); col.start()
    run_until(col, clock, reopen - 5_000, step_ms=60_000)    # closed: coarse steps
    run_until(col, clock, reopen + 15_000, step_ms=1000)
    assert conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0] == 0   # held back
    run_until(col, clock, reopen + 40_000, step_ms=1000)
    assert col.offset.offset_hours == 3
    assert conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0] > 0
    newest = conn.execute("SELECT MAX(t_ms) FROM ticks").fetchone()[0]
    assert newest <= clock.t                           # would be ~1h in the future if mis-stamped
    for sym in cfg.broker_symbols:
        assert_matches_feed(conn, sim, sym, 3)
