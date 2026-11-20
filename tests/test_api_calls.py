import sqlite3

import pytest
from starlette.testclient import TestClient

from collector.collector import Collector
from collector.sim_client import SimClient
from core import db
from grader.grader import grade
from grader.labeler_worker import Labeler
from server.app import create_app
from tests.conftest import LIVE_END, T0, FakeClock, make_cfg

MIN = 60_000


@pytest.fixture
def env(tick_db, cfg):
    clock = FakeClock(LIVE_END)
    with TestClient(create_app(cfg, tick_db, clock, web_dist=None)) as c:
        yield c, clock, tick_db, cfg


def call(c, **kw):
    body = {"symbol": "EURUSD", "direction": "buy", "delay": 300}
    body.update(kw)
    return c.post("/calls", json=body)


def test_call_is_stored_at_the_delayed_time_and_cannot_be_backdated(env):
    c, clock, path, _ = env
    r = call(c).json()
    assert r["t_ms"] == clock.t - 300_000 and r["status"] == "pending" and r["peeked"] == 0
    assert (r["symbol"], r["direction"], r["delay_s"], r["created_ms"]) == ("EURUSD", "buy", 300, clock.t)
    assert call(c, delay=60).json()["t_ms"] == clock.t - 60_000
    # the client does not get to choose the time
    assert call(c, t_ms=123).status_code == 422
    row = db.connect(path).execute("SELECT symbol, t_ms, direction, peeked FROM calls WHERE id=?", (r["id"],)).fetchone()
    assert row == ("EURUSD", clock.t - 300_000, "buy", 0)


@pytest.mark.parametrize("body,code", [({"direction": "up"}, 400), ({"delay": 0}, 400),
                                       ({"delay": -3}, 400), ({"delay": 10**9}, 400),
                                       ({"symbol": "NOPE"}, 404)])
def test_bad_calls_are_refused(env, body, code):
    c, *_ = env
    assert call(c, **body).status_code == code
    assert c.get("/calls").json()["calls"] == []


def test_peeked_flag_explicit_or_from_an_open_peek_session(env):
    c, clock, path, _ = env
    assert call(c, peeked=True).json()["peeked"] == 1
    conn = db.connect(path)
    db.log_peek(conn, clock.t, "s1", "EURUSD", "open")
    assert call(c, session_id="s1").json()["peeked"] == 1        # open peek session => flagged
    assert call(c, session_id="s2").json()["peeked"] == 0        # unrelated session
    assert call(c, session_id="s1", peeked=False).json()["peeked"] == 1   # cannot un-peek by omission
    db.log_peek(conn, clock.t, "s1", "EURUSD", "close")
    assert call(c, session_id="s1").json()["peeked"] == 0        # peek closed -> clean again


def test_labeler_grades_calls_and_they_match_direct_grading(env):
    c, clock, path, cfg = env
    old = call(c, delay=400, direction="sell").json()             # window long finished
    new = call(c, delay=60).json()                                # outcome not stored yet
    Labeler(cfg, db.connect(path)).run_once()
    calls = {x["id"]: x for x in c.get("/calls").json()["calls"]}
    assert calls[old["id"]]["status"] == "final" and calls[new["id"]]["status"] == "pending"
    lab = grade(db.connect(path), "EURUSD", old["t_ms"], "sell", cfg)
    got = calls[old["id"]]
    for col in ("entry_price", "spread_pips", "pnl_30s", "pnl_60s", "pnl_120s", "pnl_300s", "mfe_pips", "mae_pips"):
        assert got[col] == getattr(lab, col)
    assert calls[new["id"]]["pnl_300s"] is None                   # no partial numbers while pending


def test_call_while_market_is_closed_is_ungradable_not_stuck_pending(tick_db, cfg):
    sat = 1741440000000 + 3_600_000                               # Sat 2025-03-08 ~13:20 UTC
    clock = FakeClock(sat)
    with TestClient(create_app(cfg, tick_db, clock, web_dist=None)) as c:
        r = call(c).json()
        Labeler(cfg, db.connect(tick_db)).run_once()
        assert c.get("/calls").json()["calls"][0]["status"] == "ungradable"
        assert c.get("/calls/scorecard").json()["all"]["ungradable"] == 1


def test_scorecard_numbers_by_hand(env):
    c, clock, path, _ = env
    ids = [call(c, direction=d, peeked=p).json()["id"] for d, p in
           [("buy", False), ("sell", False), ("buy", False), ("buy", True), ("sell", True)]]
    conn = db.connect(path)
    results = {  # id -> (pnl_30, pnl_60, pnl_120, pnl_300, mfe, mae, spread)
        ids[0]: (2.0, 4.0, 6.0, -1.0, 7.0, -2.0, 1.0),
        ids[1]: (-1.0, -3.0, 0.0, 2.0, 3.0, -4.0, 1.4),
        ids[3]: (5.0, 5.0, 5.0, 5.0, 6.0, -1.0, 0.8),
    }
    with conn:
        for cid, (a, b, cc, d, mfe, mae, sp) in results.items():
            conn.execute("INSERT INTO call_results (call_id, pnl_30s, pnl_60s, pnl_120s, pnl_300s, mfe_pips, "
                         "mae_pips, spread_pips, status) VALUES (?,?,?,?,?,?,?,?, 'final')",
                         (cid, a, b, cc, d, mfe, mae, sp))
    sc = c.get("/calls/scorecard").json()
    clean, peeked, allc = sc["clean"], sc["peeked"], sc["all"]
    assert (clean["calls"], clean["graded"], clean["pending"]) == (3, 2, 1)
    assert clean["avg_pips"]["pnl_30s"] == pytest.approx(0.5)      # (2 + -1) / 2
    assert clean["avg_pips"]["pnl_300s"] == pytest.approx(0.5)     # (-1 + 2) / 2
    assert clean["win_rate"]["pnl_60s"] == 50.0 and clean["win_rate"]["pnl_120s"] == 50.0  # 0.0 is not a win
    assert clean["avg_mfe_pips"] == pytest.approx(5.0) and clean["avg_mae_pips"] == pytest.approx(-3.0)
    assert (peeked["calls"], peeked["graded"], peeked["pending"]) == (2, 1, 1)
    assert peeked["avg_pips"]["pnl_60s"] == 5.0 and peeked["win_rate"]["pnl_60s"] == 100.0
    assert (allc["calls"], allc["graded"]) == (5, 3)
    assert c.get("/calls/scorecard", params={"symbol": "EURJPY"}).json()["all"]["calls"] == 0


def test_old_database_gets_the_new_call_columns_in_place(tmp_path):
    p = tmp_path / "old.db"
    old = sqlite3.connect(p)
    old.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY, symbol TEXT, t_ms INTEGER, direction TEXT, "
                "note TEXT, peeked INTEGER DEFAULT 0)")
    old.execute("INSERT INTO calls (symbol, t_ms, direction, note) VALUES ('EURUSD', 5, 'buy', 'kept')")
    old.commit(); old.close()
    conn = db.connect(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(calls)")}
    assert {"session_id", "created_ms", "delay_s"} <= cols
    assert conn.execute("SELECT note FROM calls").fetchone() == ("kept",)     # data preserved
    db.connect(p)                                                              # idempotent


def test_stream_backfill_carries_the_last_tick(env):
    c, clock, path, _ = env
    with c.websocket_connect("/ws/stream?symbols=EURUSD&delay=300") as ws:
        m = ws.receive_json()
        t, bid, ask = m["last_tick"]
        assert t <= m["cutoff_ms"] and ask > bid
        assert m["candles"][-1]["close"] == bid          # the chart is bid-based: close == last bid


def test_built_web_app_is_served_without_shadowing_the_api(tick_db, cfg, tmp_path):
    (tmp_path / "index.html").write_text("<html>LAB</html>")
    with TestClient(create_app(cfg, tick_db, FakeClock(LIVE_END), web_dist=tmp_path)) as c:
        assert "LAB" in c.get("/").text
        assert c.get("/health").json()["ok"] is True
        assert c.get("/api/config").json()["timeframes"]
        assert c.get("/calls").status_code == 200


# --------------------------------------------------------------------------------------
# Phase 4 "Done when": a practice call shows its graded result as soon as the outcome is final
def test_result_appears_as_soon_as_the_outcome_is_final(tmp_path):
    cfg = make_cfg(("EURUSD",))
    cfg.collector.backfill_hours_on_start = 0.1
    clock = FakeClock(T0)
    sim = SimClient(clock, offset_hours=3)
    path = tmp_path / "live.db"
    conn = db.connect(path)
    col = Collector(cfg, conn, sim, clock); col.start()
    lab = Labeler(cfg, conn)
    with TestClient(create_app(cfg, path, clock, web_dist=None)) as c:
        for _ in range(30):
            clock.t += 1000; col.poll_once()
        cid = call(c, delay=300).json()["id"]
        t_call = clock.t
        first_final = None
        for _ in range(330):                                       # 5.5 minutes in 1 s steps
            clock.t += 1000
            col.poll_once()
            lab.run_once()                                          # production runs this every 2 s
            st = next(x for x in c.get("/calls").json()["calls"] if x["id"] == cid)
            if st["status"] == "final" and first_final is None:
                first_final = clock.t
            if first_final is None:
                assert st["pnl_300s"] is None                       # never a partial result
        assert first_final is not None
        entry = db.connect(path).execute("SELECT MIN(t_ms) FROM ticks WHERE symbol='EURUSD' AND t_ms>=?",
                                         (t_call - 300_000,)).fetchone()[0]
        lag = first_final - (entry + 300_000)
        print(f"\nresult shown {lag} ms after its outcome window completed")
        assert 0 <= lag <= 3_000
        # and the call was made on a chart 300 s behind, so it is final within seconds of the call
        assert first_final - t_call <= 3_000
