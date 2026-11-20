import random
import shutil

import pytest

from collector.collector import Collector
from collector.sim_client import SimClient
from core import db
from grader.grader import grade
from grader.labeler_worker import Labeler
from tests.conftest import LIVE_END, T0, FakeClock, make_cfg

MIN = 60_000


def rows(conn, sql="SELECT * FROM labels ORDER BY symbol, entry_t_ms, direction"):
    return conn.execute(sql).fetchall()


@pytest.fixture
def labeled(tick_db, cfg):
    conn = db.connect(tick_db)
    stats = Labeler(cfg, conn).run_once()
    return conn, stats


def test_backfill_labels_all_stored_history_on_the_grid(labeled, cfg):
    conn, stats = labeled
    for sym in cfg.broker_symbols:
        first, last = conn.execute("SELECT MIN(t_ms), MAX(t_ms) FROM ticks WHERE symbol=?", (sym,)).fetchone()
        ts = [r[0] for r in conn.execute("SELECT DISTINCT entry_t_ms FROM labels WHERE symbol=? ORDER BY 1", (sym,))]
        assert ts[0] == -(-first // MIN) * MIN and ts[-1] == last // MIN * MIN
        assert all(t % MIN == 0 for t in ts)
        assert [b - a for a, b in zip(ts, ts[1:])] == [MIN] * (len(ts) - 1)       # no holes
        dirs = conn.execute("SELECT entry_t_ms, COUNT(DISTINCT direction) FROM labels WHERE symbol=? "
                            "GROUP BY 1", (sym,)).fetchall()
        assert all(n == 2 for _, n in dirs)                                       # buy AND sell
        # features stored for every graded time
        assert conn.execute("SELECT COUNT(DISTINCT t_ms) FROM features WHERE symbol=?", (sym,)).fetchone()[0] == len(ts)
    assert stats["new"] == conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] > 300


def test_status_is_final_only_when_the_full_horizon_is_stored(labeled, cfg):
    conn, _ = labeled
    for sym in cfg.broker_symbols:
        end = conn.execute("SELECT MAX(t_ms) FROM ticks WHERE symbol=?", (sym,)).fetchone()[0]
        for t, d, status, pnl300 in conn.execute(
                "SELECT entry_t_ms, direction, status, pnl_300s FROM labels WHERE symbol=?", (sym,)):
            t_e = conn.execute("SELECT MIN(t_ms) FROM ticks WHERE symbol=? AND t_ms>=?", (sym, t)).fetchone()[0]
            if t_e + 300_000 <= end:
                assert status == "final" and pnl300 is not None
            else:
                assert status == "pending" and pnl300 is None
    n_pending = conn.execute("SELECT COUNT(*) FROM labels WHERE status='pending'").fetchone()[0]
    assert 0 < n_pending <= 2 * 2 * 6                       # ~5 min of entries per symbol, both sides


def test_second_pass_changes_nothing(labeled, cfg):
    conn, _ = labeled
    before = rows(conn)
    stats = Labeler(cfg, conn).run_once()
    assert (stats["new"], stats["finalised"], stats["dropped"]) == (0, 0, 0)
    assert rows(conn) == before


def test_labels_equal_direct_grade_calls(labeled, cfg):
    conn, _ = labeled
    rng = random.Random(5)
    sample = rng.sample(rows(conn, "SELECT symbol, entry_t_ms, direction FROM labels"), 40)
    for sym, t, d in sample:
        lab = grade(conn, sym, t, d, cfg)
        stored = conn.execute("SELECT * FROM labels WHERE symbol=? AND entry_t_ms=? AND direction=?",
                              (sym, t, d)).fetchone()
        assert tuple(getattr(lab, c) for c in db.LABEL_COLS) == stored


def test_incremental_passes_equal_a_from_scratch_pass(tick_db, tmp_path, cfg):
    """Label early data, add the rest, label again: identical to labeling everything at once."""
    # a db holding only the first part of the data
    early = tmp_path / "early.db"
    shutil.copy(tick_db, early)
    ec = db.connect(early)
    cut = T0 - 20 * MIN
    with ec:
        ec.execute("DELETE FROM ticks WHERE t_ms > ?", (cut,))
    lab = Labeler(cfg, ec)
    s1 = lab.run_once()
    assert s1["pending"] > 0
    # now the remaining ticks arrive
    full = db.connect(tick_db)
    db.insert_ticks(ec, full.execute("SELECT * FROM ticks WHERE t_ms > ?", (cut,)).fetchall())
    s2 = lab.run_once()
    assert s2["finalised"] > 0 and s2["new"] > 0
    scratch = db.connect(tick_db)
    Labeler(cfg, scratch).run_once()
    assert rows(ec) == rows(scratch)
    assert rows(ec, "SELECT * FROM features ORDER BY 1,2,3") == rows(scratch, "SELECT * FROM features ORDER BY 1,2,3")


def test_new_instance_resumes_where_the_old_one_stopped(tick_db, tmp_path, cfg):
    path = tmp_path / "r.db"
    shutil.copy(tick_db, path)
    conn = db.connect(path)
    with conn:
        conn.execute("DELETE FROM ticks WHERE t_ms > ?", (T0 - 30 * MIN,))
    Labeler(cfg, conn).run_once()
    n1 = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
    db.insert_ticks(conn, db.connect(tick_db).execute("SELECT * FROM ticks WHERE t_ms > ?", (T0 - 30 * MIN,)).fetchall())
    Labeler(cfg, db.connect(path)).run_once()          # a brand-new process
    c2 = db.connect(path)
    assert c2.execute("SELECT COUNT(*) FROM labels").fetchone()[0] > n1
    assert c2.execute("SELECT COUNT(*) - COUNT(DISTINCT symbol||entry_t_ms||direction) FROM labels").fetchone()[0] == 0


def test_weekend_gap_produces_no_labels_and_pending_tail_is_dropped(cfg):
    """Friday's last minutes can never get a full window; they must not stay pending forever."""
    from datetime import datetime, timezone
    fri_close = int(datetime(2025, 3, 14, 21, 0, tzinfo=timezone.utc).timestamp() * 1000)   # Fri 17:00 EDT
    sun_open = fri_close + 2 * 86_400_000
    conn = db.connect(":memory:")
    db.set_state(conn, "digits:EURUSD", "5")
    c = make_cfg(("EURUSD",));
    ticks = [("EURUSD", fri_close - 12 * MIN + k * 1000, 1.1 + k * 1e-6, 1.1002 + k * 1e-6, 0, 0)
             for k in range(0, 12 * 60)]
    db.insert_ticks(conn, ticks)
    lab = Labeler(c, conn)
    lab.run_once()
    assert conn.execute("SELECT COUNT(*) FROM labels WHERE status='pending'").fetchone()[0] > 0
    # market reopens; data resumes
    db.insert_ticks(conn, [("EURUSD", sun_open + k * 1000, 1.1, 1.1002, 0, 0) for k in range(0, 10 * 60)])
    s = lab.run_once()
    assert s["dropped"] > 0
    finals = conn.execute("SELECT MAX(entry_t_ms) FROM labels WHERE status='final' AND entry_t_ms < ?",
                          (fri_close,)).fetchone()[0]
    assert finals <= fri_close - 5 * MIN                # only entries with a full pre-close window
    assert conn.execute("SELECT COUNT(*) FROM labels WHERE entry_t_ms > ? AND entry_t_ms < ?",
                        (fri_close, sun_open)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM labels WHERE status='pending' AND entry_t_ms < ?",
                        (fri_close,)).fetchone()[0] == 0


def test_commission_is_converted_to_pips(tick_db):
    cfg = make_cfg()
    cfg.costs.commission_per_lot_round_trip = 7.0          # USD per lot round trip
    conn = db.connect(tick_db)
    t = T0 - 10 * MIN
    plain = grade(conn, "EURUSD", t, "buy", make_cfg())
    with_c = grade(conn, "EURUSD", t, "buy", cfg)
    # EURUSD: pip value = 100000 * 0.0001 = 10 USD/lot -> 7 USD = 0.7 pips
    assert with_c.pnl_60s == pytest.approx(plain.pnl_60s - 0.7)
    # EURJPY: pip value 1000 JPY; USDJPY = EURJPY/EURUSD at the entry
    pj, cj = grade(conn, "EURJPY", t, "buy", make_cfg()), grade(conn, "EURJPY", t, "buy", cfg)
    e_j = conn.execute("SELECT (bid+ask)/2 FROM ticks WHERE symbol='EURJPY' AND t_ms<=? ORDER BY t_ms DESC, seq DESC LIMIT 1", (t,)).fetchone()[0]
    e_u = conn.execute("SELECT (bid+ask)/2 FROM ticks WHERE symbol='EURUSD' AND t_ms<=? ORDER BY t_ms DESC, seq DESC LIMIT 1", (t,)).fetchone()[0]
    expect = 7.0 / (1000 / (e_j / e_u))
    assert pj.pnl_60s - cj.pnl_60s == pytest.approx(expect, rel=1e-6)


# ---------------------------------------------------------------------------------
# Phase 3 "Done when": with delay 300 s and horizon 300 s, labels for a candle are final
# by the time that candle appears on the delayed chart.
def test_labels_become_final_when_the_candle_appears_on_the_delayed_chart(tmp_path):
    cfg = make_cfg(("EURUSD", "EURJPY"))
    cfg.collector.backfill_hours_on_start = 0.01
    clock = FakeClock(T0)
    sim = SimClient(clock, offset_hours=3)
    conn = db.connect(tmp_path / "live.db")
    col = Collector(cfg, conn, sim, clock); col.start()
    lab = Labeler(cfg, conn)
    final_at = {}                                            # (sym, grid T) -> wall ms both sides final
    for _ in range(480):                                     # 8 minutes of 1 s steps
        clock.t += 1000
        col.poll_once()
        lab.run_once()
        for sym, t in conn.execute("SELECT symbol, entry_t_ms FROM labels GROUP BY 1,2 "
                                   "HAVING SUM(status='final') = 2"):
            final_at.setdefault((sym, t), clock.t)
    lags = []
    for (sym, T), wall in final_at.items():
        # the M1 candle opening at T first shows on a delay-300 chart when the delayed clock
        # reaches its first tick (t_e): wall = t_e + 300 s
        t_e = conn.execute("SELECT MIN(t_ms) FROM ticks WHERE symbol=? AND t_ms>=?", (sym, T)).fetchone()[0]
        appear = t_e + 300_000
        lags.append(wall - appear)
    assert len(lags) >= 2 * 2                                # several candles on both symbols
    assert min(lags) >= 0                                    # never final before its window exists
    # final within ~a tick of the candle appearing - and long before that candle even closes
    assert max(lags) <= 3_000, f"worst lag {max(lags)} ms"
    print(f"\nlabel-final lag after candle appears: min {min(lags)} ms, max {max(lags)} ms, n={len(lags)}")
