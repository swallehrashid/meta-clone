"""Replay and delayed-live are two drivers of the SAME step: they must trade identically. Plus the live manager and API."""
import shutil
import time
from datetime import date

import pytest
from starlette.testclient import TestClient

from bot.live import BotManager
from bot.runner import RiskLimits, run_replay
from bot.strategies import make_bot
from collector.market_model import DAY_MS, MarketModel, SPECS, seq_within_ms
from core import db
from core.config import load_config
from engine.account import Engine
from server.app import create_app
from tests.conftest import FakeClock
from tools.sandbox import open_sandbox

D = lambda y, m, d: (date(y, m, d) - date(1970, 1, 1)).days
SYMS = ["EURUSD", "GBPUSD"]
LO = D(2026, 3, 4) * DAY_MS + 14 * 3_600_000                # 09:00 New York
MIN = 60_000


def cfg_():
    c = load_config()
    c.symbols = SYMS
    return c


@pytest.fixture(scope="module")
def mkt(tmp_path_factory):
    path = tmp_path_factory.mktemp("botlive") / "ticks.db"
    conn = db.connect(path)
    m = MarketModel(41)
    for sym in SYMS:
        t, b, a = m.day_ticks(sym, D(2026, 3, 4))
        db.insert_ticks(conn, list(zip([sym] * len(t), t.tolist(), b.tolist(), a.tolist(), [0] * len(t), seq_within_ms(t).tolist())))
        db.set_state(conn, f"digits:{sym}", str(SPECS[sym].digits))
    conn.close()
    return path


def key(deals):
    return [(d["symbol"], d["side"], d["volume"], d["open_ms"], d["close_ms"], round(d["open_price"], 9), round(d["close_price"], 9),
             d["reason"], round(d["net"], 9)) for d in deals]


BOTS = [("random_entry", {"p_enter": 0.25, "hold_s": 240, "sl_pips": 5, "tp_pips": 7, "seed": 3}),
        ("momentum_breakout", {"hold_s": 180, "max_spread_pips": 3.0, "sl_pips": 4, "tp_pips": 5})]


@pytest.mark.parametrize("name,params", BOTS)
def test_live_mode_takes_exactly_the_decisions_replay_takes(mkt, tmp_path, name, params):
    cfg = cfg_()
    hi = LO + 100 * MIN
    # --- replay: jumps from decision to decision
    rep = run_replay(cfg, open_sandbox(str(mkt)), make_bot(name, params), SYMS, LO, hi)
    assert len(rep.deals) >= 5

    # --- live: a private copy of the database, a wall clock that ticks every 5 seconds, delay 300 s
    live_db = tmp_path / "live.db"
    shutil.copy(mkt, live_db)
    conn = db.connect(live_db, check_same_thread=False)
    eng = Engine(cfg, conn)
    eng.reset()
    eng.set_delay(300)
    mgr = BotManager(cfg, eng)
    start = LO - 1                                             # chart time when we switch the bot on: the first decision is at LO, as in the replay
    eng.advance_to(start)
    mgr.start(name, params, SYMS)
    assert mgr.status()["running"]
    now = start + 300_000
    while now - 300_000 <= hi:
        mgr.heartbeat(now)
        now += 5_000
    if eng.positions:
        eng.close_all(None, at_ms=eng.t_engine)
    live = list(reversed(eng.history(100_000)))
    cut = hi - 10 * MIN                                        # trades opened early enough to have finished on their own in both runs
    rep_c = [d for d in rep.deals if d["open_ms"] <= cut]
    live_c = [d for d in live if d["open_ms"] <= cut]
    assert len(live_c) >= 4
    assert key(live_c) == key(rep_c)


@pytest.mark.parametrize("name,params", BOTS)
def test_decisions_do_not_depend_on_who_advances_the_account_clock(mkt, tmp_path, name, params):
    """The UI polls the account (which advances its clock) and humans place orders, between server heartbeats. Whoever
    moves the clock, the bot must still decide at every grid time with the engine exactly there - the same trades as a replay."""
    import random
    cfg = cfg_()
    hi = LO + 60 * MIN
    rep = run_replay(cfg, open_sandbox(str(mkt)), make_bot(name, params), SYMS, LO, hi)
    shutil.copy(mkt, tmp_path / "poll.db")
    conn = db.connect(tmp_path / "poll.db", check_same_thread=False)
    eng = Engine(cfg, conn); eng.reset(); eng.set_delay(300)
    mgr = BotManager(cfg, eng)
    eng.advance_to(LO - 1)
    mgr.start(name, params, SYMS)
    rng = random.Random(8)
    now = LO - 1 + 300_000
    while now - 300_000 <= hi:
        now += rng.choice([300, 700, 1_300, 4_900, 17_000])             # irregular beats ...
        eng.advance_to(now - 300_000)                                  # ... each one a "UI poll" straight to the chart time
        if rng.random() < 0.2:
            mgr.heartbeat(now)
        if rng.random() < 0.05:
            try:                                                       # a human trade mid-minute also moves the clock
                eng.place_order("GBPUSD", "buy", volume=0.01, at_ms=now - 300_000, source="manual")
            except Exception:
                pass
    if eng.positions:
        eng.close_all(None, at_ms=eng.t_engine)
    cut = hi - 10 * MIN
    live = [d for d in reversed(eng.history(100_000)) if d["source"] == "bot" and d["open_ms"] <= cut]
    ref = [d for d in rep.deals if d["open_ms"] <= cut]
    assert len(live) >= 3 and key(live) == key(ref)


def test_catch_up_after_downtime_only_acts_on_the_last_decision_times(mkt, tmp_path):
    cfg = cfg_()
    shutil.copy(mkt, tmp_path / "c.db")
    conn = db.connect(tmp_path / "c.db", check_same_thread=False)
    eng = Engine(cfg, conn); eng.reset(); eng.set_delay(300)
    mgr = BotManager(cfg, eng)
    eng.advance_to(LO)
    mgr.start("random_entry", {"p_enter": 1.0, "hold_s": 3600, "seed": 1}, ["EURUSD"])
    mgr.heartbeat(LO + 300_000 + 2 * MIN)                       # normal
    n_normal = mgr.runner.stats.steps
    mgr.heartbeat(LO + 300_000 + 60 * MIN)                      # the server was "down" for ~58 minutes
    assert mgr.runner.stats.steps - n_normal <= 2               # it did NOT replay the missed hour of decisions
    assert eng.t_engine == LO + 60 * MIN


def test_manager_state_survives_a_restart_and_a_stopped_bot_stays_stopped(mkt, tmp_path):
    cfg = cfg_()
    shutil.copy(mkt, tmp_path / "r.db")
    conn = db.connect(tmp_path / "r.db", check_same_thread=False)
    eng = Engine(cfg, conn); eng.reset(); eng.set_delay(300)
    m1 = BotManager(cfg, eng)
    m1.start("momentum_breakout", {"hold_s": 90}, ["EURUSD", "GBPUSD"], {"max_open_positions": 2})
    m2 = BotManager(cfg, Engine(cfg, conn))                     # "restart": new objects, same database
    st = m2.status()
    assert st["running"] and st["bot"]["name"] == "momentum_breakout" and st["bot"]["params"]["hold_s"] == 90
    assert st["bot"]["symbols"] == ["EURUSD", "GBPUSD"] and st["limits"]["max_open_positions"] == 2
    m2.stop()
    m3 = BotManager(cfg, Engine(cfg, conn))
    assert not m3.status()["running"] and m3.status()["bot"] is None


def test_start_validates_everything_before_changing_anything(mkt, tmp_path):
    cfg = cfg_()
    shutil.copy(mkt, tmp_path / "v.db")
    conn = db.connect(tmp_path / "v.db", check_same_thread=False)
    mgr = BotManager(cfg, Engine(cfg, conn))
    for args in (("nope", {}, SYMS, {}), ("random_entry", {"p_enter": 5}, SYMS, {}), ("random_entry", {}, ["XXXYYY"], {}), ("random_entry", {}, SYMS, {"bad": 1})):
        with pytest.raises(ValueError):
            mgr.start(*args)
    assert not mgr.status()["running"] and conn.execute("SELECT COUNT(*) FROM bot_state").fetchone()[0] == 0
    mgr.start("random_entry", {}, SYMS)
    with pytest.raises(ValueError, match="already running"):
        mgr.start("random_entry", {}, SYMS)


def test_stop_can_flatten_the_bots_positions_but_never_a_humans(mkt, tmp_path):
    cfg = cfg_()
    shutil.copy(mkt, tmp_path / "s.db")
    conn = db.connect(tmp_path / "s.db", check_same_thread=False)
    eng = Engine(cfg, conn); eng.reset(); eng.set_delay(300)
    mgr = BotManager(cfg, eng)
    eng.advance_to(LO)
    human = eng.place_order("EURUSD", "buy", volume=0.1, at_ms=LO, source="manual")["id"]
    mgr.start("random_entry", {"p_enter": 1.0, "hold_s": 3600, "seed": 2}, ["GBPUSD"])
    for k in range(1, 6):
        mgr.heartbeat(LO + 300_000 + k * MIN)
    assert any(p["source"] == "bot" for p in eng.positions_view())
    mgr.stop(close_positions=True)
    assert [p["id"] for p in eng.positions_view()] == [human] and not mgr.status()["running"]


# ============================================================================== the HTTP API
@pytest.fixture
def api(mkt, tmp_path):
    shutil.copy(mkt, tmp_path / "a.db")
    clock = FakeClock(LO + 300_000)
    with TestClient(create_app(cfg_(), tmp_path / "a.db", clock, web_dist=None, clock_interval=3600)) as c:
        yield c, clock, tmp_path / "a.db"


def test_bot_api_lifecycle(api):
    c, clock, _ = api
    j = c.get("/api/bot").json()
    assert not j["running"] and {b["name"] for b in j["available"]} == {"random_entry", "momentum_breakout", "forward_teacher", "learned"} and j["log"] == []
    assert c.post("/api/bot/start", json={"name": "nope"}).status_code == 400
    assert c.post("/api/bot/start", json={"name": "random_entry", "params": {"p_enter": 7}}).status_code == 400
    assert c.post("/api/bot/start", json={"name": "random_entry", "symbols": ["XXXYYY"]}).status_code == 400
    assert c.post("/api/bot/start", json={"name": "random_entry", "limits": {"max_everything": 1}}).status_code == 400
    r = c.post("/api/bot/start", json={"name": "random_entry", "params": {"p_enter": 1.0, "hold_s": 120}, "symbols": ["EURUSD"]})
    assert r.status_code == 200 and r.json()["running"]
    assert set(r.json()) == set(c.get("/api/bot").json())                                       # every endpoint answers in the same shape (the UI relies on it)
    assert r.json()["log"] and r.json()["log"][0]["kind"] == "info"
    assert r.json()["log"][0]["t_ms"] == clock.t - 300_000                                     # stamped with the chart time, not the epoch
    assert c.post("/api/bot/start", json={"name": "random_entry"}).status_code == 409           # already running
    st = c.get("/api/trade/state", params={"delay": 300}).json()                                  # (sets the account delay)
    mgr = c.app.state.bots
    for k in range(1, 8):                                                                         # the market moves on 7 minutes
        clock.t += MIN
        mgr.heartbeat(clock.t)
    j = c.get("/api/bot").json()
    assert j["stats"]["steps"] >= 6 and j["stats"]["opened"] >= 2
    st = c.get("/api/trade/state", params={"delay": 300}).json()
    assert all(p["source"] == "bot" for p in st["positions"])           # (there may be none at this exact instant: a close decision just ran)
    assert any(e["kind"] == "open" for e in j["log"])
    deals = c.get("/api/trade/history").json()["deals"]
    assert deals and all(d["source"] == "bot" and d["peeked"] == 0 for d in deals)               # clean, never peeked
    s = c.post("/api/bot/stop", json={"close_positions": True}).json()
    assert set(s) == set(c.get("/api/bot").json()) and not s["running"] and c.get("/api/trade/state", params={"delay": 300}).json()["positions"] == []


def test_a_bot_started_through_the_api_resumes_after_a_server_restart(mkt, tmp_path):
    shutil.copy(mkt, tmp_path / "x.db")
    clock = FakeClock(LO + 300_000)
    with TestClient(create_app(cfg_(), tmp_path / "x.db", clock, web_dist=None, clock_interval=3600)) as c:
        c.post("/api/bot/start", json={"name": "momentum_breakout", "params": {"hold_s": 77}, "symbols": ["GBPUSD"]})
    with TestClient(create_app(cfg_(), tmp_path / "x.db", clock, web_dist=None, clock_interval=3600)) as c2:
        j = c2.get("/api/bot").json()
        assert j["running"] and j["bot"]["params"]["hold_s"] == 77 and j["bot"]["symbols"] == ["GBPUSD"]


def test_the_background_clock_runs_the_bot_with_no_client_connected(mkt, tmp_path):
    shutil.copy(mkt, tmp_path / "bg.db")
    clock = FakeClock(LO + 300_000)
    with TestClient(create_app(cfg_(), tmp_path / "bg.db", clock, web_dist=None, clock_interval=0.02)) as c:
        c.get("/api/trade/state", params={"delay": 300})
        c.post("/api/bot/start", json={"name": "random_entry", "params": {"p_enter": 1.0, "hold_s": 60}, "symbols": ["EURUSD"]})
        for _ in range(6):
            clock.t += MIN
            time.sleep(0.3)
        assert c.get("/api/bot").json()["stats"]["opened"] >= 3
