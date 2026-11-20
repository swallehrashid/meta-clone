"""The public terminal API, the admin login, the terminal settings and the bot-lab jobs."""
import json
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core import db
from core.dataview import DataView
from server.app import create_app
from server.auth import AdminAuth
from tests.conftest import LIVE_END, T0, FakeClock, make_cfg

PW = "correct-horse-1"


@pytest.fixture
def secured(tick_db, monkeypatch):
    monkeypatch.setenv("LAB_ADMIN_PASSWORD", PW)
    clock = FakeClock(LIVE_END)
    with TestClient(create_app(make_cfg(), tick_db, clock, web_dist=None, clock_interval=3600, require_admin_login=True)) as c:
        yield c, clock, tick_db


@pytest.fixture
def open_api(tick_db):
    clock = FakeClock(LIVE_END)
    with TestClient(create_app(make_cfg(), tick_db, clock, web_dist=None, clock_interval=3600)) as c:
        yield c, clock, tick_db


def login(c, pw=PW):
    return c.post("/api/admin/login", json={"username": "admin", "password": pw})


# ================================================================================================ admin login
ADMIN_ONLY = ["/api/bot", "/api/admin/terminal", "/api/admin/lab/bots", "/api/admin/lab/runs", "/api/admin/lab/live",
              "/labels?symbol=EURUSD&delay=300", "/calls", "/calls/scorecard", "/report"]
PUBLIC = ["/health", "/api/config", "/api/terminal", "/api/terminal/quotes", "/api/trade/state?delay=300", "/api/trade/history"]


def test_everything_behind_the_back_office_needs_a_login_and_the_terminal_does_not(secured):
    c, _, _ = secured
    for u in ADMIN_ONLY:
        assert c.get(u).status_code == 401, u
    for u in PUBLIC:
        assert c.get(u).status_code == 200, u
    assert c.post("/api/trade/account/reset", json={"confirm": True}).status_code == 401      # a phone user cannot wipe the account
    assert c.put("/api/admin/terminal", json={"delay_s": 60}).status_code == 401
    assert c.post("/api/bot/start", json={"name": "random_entry"}).status_code == 401
    assert c.get("/api/admin/me").json() == {"admin": False, "username": None}
    assert login(c).status_code == 200
    for u in ADMIN_ONLY:
        assert c.get(u).status_code != 401, u
    assert c.get("/api/admin/me").json() == {"admin": True, "username": "admin"}
    assert c.post("/api/admin/logout").status_code == 200
    assert c.get("/api/admin/terminal").status_code == 401


def test_wrong_credentials_are_refused_and_repeated_failures_lock_the_client_out(secured):
    c, _, _ = secured
    assert login(c, "nope").status_code == 401
    assert c.post("/api/admin/login", json={"username": "root", "password": PW}).status_code == 401
    for _ in range(5):
        login(c, "nope")
    assert login(c).status_code == 429                         # even the right password, until the window passes


def test_the_session_cookie_is_httponly_strict_and_cannot_be_forged_or_replayed_after_a_password_change(secured, tick_db):
    c, _, path = secured
    r = login(c)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    token = c.cookies.get("lab_admin")
    c.cookies.clear()
    for bad in (token[:-2] + "xx", "garbage", token.split(".")[0] + ".", ""):
        c.cookies.set("lab_admin", bad)
        assert c.get("/api/admin/terminal").status_code == 401
    c.cookies.set("lab_admin", token)
    assert c.get("/api/admin/terminal").status_code == 200
    AdminAuth.set_password(path.parent / "admin.json", "a-brand-new-pass")      # new signing secret: every old session dies
    c.app.state.auth = AdminAuth(path.parent / "admin.json")
    assert c.get("/api/admin/terminal").status_code == 401
    assert c.post("/api/admin/login", json={"username": "admin", "password": "a-brand-new-pass"}).status_code == 200


def test_password_is_stored_hashed_and_a_random_one_is_generated_when_none_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_ADMIN_PASSWORD", raising=False)
    a = AdminAuth(tmp_path / "admin.json")
    assert a.created_password and len(a.created_password) >= 10 and a.check("admin", a.created_password)
    assert a.created_password not in (tmp_path / "admin.json").read_text()
    assert not a.check("admin", "wrong") and not a.check("other", a.created_password)
    assert AdminAuth(tmp_path / "admin.json").created_password is None          # shown once, never again
    with pytest.raises(ValueError):
        AdminAuth.set_password(tmp_path / "admin.json", "short")


def test_peek_is_refused_without_the_admin_cookie_and_allowed_with_it(secured):
    c, _, _ = secured
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/ws/peek?symbols=EURUSD&peeked=1"):
            pass
    assert e.value.code == 4403
    login(c)
    with c.websocket_connect("/ws/peek?symbols=EURUSD&peeked=1") as ws:
        assert ws.receive_json()["type"] == "backfill"
    # the delayed stream stays public: it is what the terminal uses
    with c.websocket_connect("/ws/stream?symbols=EURUSD&delay=300") as ws:
        assert ws.receive_json()["type"] == "backfill"


# ================================================================================================ terminal settings
def test_the_terminal_reads_its_settings_from_the_server_and_only_the_admin_can_change_them(open_api):
    c, _, _ = open_api
    s = c.get("/api/terminal").json()
    assert s["delay_s"] == 300 and s["tf"] == "M1" and s["volume"] == 0.1 and s["show_ask_line"] is True
    assert s["symbols"] == ["EURUSD", "EURJPY"] and s["descriptions"]["EURUSD"] == "Euro vs US Dollar" and s["delay"]["min"] == 1
    r = c.put("/api/admin/terminal", json={"delay_s": 60, "tf": "M5", "volume": 0.25, "show_ask_line": False, "tz": "UTC", "symbols": ["EURUSD"]})
    assert r.status_code == 200, r.text
    s = c.get("/api/terminal").json()
    assert (s["delay_s"], s["tf"], s["volume"], s["show_ask_line"], s["tz"], s["symbols"]) == (60, "M5", 0.25, False, "UTC", ["EURUSD"])
    assert c.app.state.engine.delay_s == 60                                      # the paper account's clock follows the terminal's delay


@pytest.mark.parametrize("bad", [{"delay_s": 0}, {"delay_s": 10**9}, {"delay_s": "x"}, {"tf": "D2"}, {"volume": 0}, {"volume": 1000},
                                 {"symbols": []}, {"symbols": ["XAUUSD"]}, {"tz": "Mars/Base"}, {"nope": 1}])
def test_bad_terminal_settings_are_refused_and_change_nothing(open_api, bad):
    c, _, _ = open_api
    before = c.get("/api/terminal").json()
    assert c.put("/api/admin/terminal", json=bad).status_code == 400
    assert c.get("/api/terminal").json() == before


def test_settings_survive_a_restart(tick_db):
    clock = FakeClock(LIVE_END)
    with TestClient(create_app(make_cfg(), tick_db, clock, web_dist=None, clock_interval=3600)) as c:
        c.put("/api/admin/terminal", json={"delay_s": 90, "volume": 0.3})
    with TestClient(create_app(make_cfg(), tick_db, clock, web_dist=None, clock_interval=3600)) as c2:
        assert c2.get("/api/terminal").json()["delay_s"] == 90 and c2.get("/api/terminal").json()["volume"] == 0.3
        assert c2.app.state.engine.delay_s == 90


# ================================================================================================ quotes
def truth(path, sym, cutoff, tz_day_start):
    conn = db.connect(path)
    dv = DataView(conn, sym, cutoff)
    return dv.last_tick(), dv.bid_extremes(tz_day_start)


def test_quotes_are_the_delayed_market_and_never_newer(open_api):
    c, clock, path = open_api
    r = c.get("/api/terminal/quotes").json()
    cutoff = LIVE_END - 300_000
    assert r["delay_s"] == 300 and r["cutoff_ms"] == cutoff
    day_start = 1741064400000                                                    # 2025-03-04 00:00 America/New_York (EST)
    for q in r["quotes"]:
        last, ext = truth(path, q["symbol"], cutoff, day_start)
        assert q["t_ms"] == last[0] <= cutoff and q["bid"] == last[2] and q["ask"] == last[3]
        assert (q["low"], q["high"]) == ext
        assert q["spread_pts"] == round((q["ask"] - q["bid"]) / 10 ** -q["digits"])
    # a longer delay can only show an older market
    c.put("/api/admin/terminal", json={"delay_s": 900})
    r2 = c.get("/api/terminal/quotes").json()
    assert r2["cutoff_ms"] == LIVE_END - 900_000 and all(a["t_ms"] <= b["t_ms"] for a, b in zip(r2["quotes"], r["quotes"]))
    assert all(q["t_ms"] <= LIVE_END - 900_000 for q in r2["quotes"])


def test_quote_low_high_are_kept_incrementally_without_drifting_from_the_truth(open_api):
    c, clock, path = open_api
    c.put("/api/admin/terminal", json={"delay_s": 60})
    day_start = 1741064400000
    for step in range(4):
        r = c.get("/api/terminal/quotes").json()
        for q in r["quotes"]:
            last, ext = truth(path, q["symbol"], r["cutoff_ms"], day_start)
            assert (q["low"], q["high"]) == ext and q["bid"] == last[2], (step, q["symbol"])
        clock.t += 90_000
    assert c.get("/api/terminal/quotes").json()["quotes"], "still serving"


def test_quote_change_is_measured_from_the_previous_close(open_api):
    c, _, path = open_api
    r = c.get("/api/terminal/quotes").json()["quotes"][0]
    conn = db.connect(path)
    first = DataView(conn, r["symbol"], LIVE_END).ticks_after((1741064400000 - 1, 2 ** 31), 1)         # no earlier day: the day's first tick
    assert first and first[0][0] > 1741064400000 + 3_600_000          # (the data starts mid-morning, so this is the case that was broken)
    ref = first[0][2]
    assert r["change_pts"] != 0
    assert r["change_pts"] == round((r["bid"] - ref) / 10 ** -r["digits"]) and r["change_pct"] == pytest.approx((r["bid"] - ref) / ref * 100)


# ================================================================================================ the bot lab
def wait_done(c, rid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/admin/lab/runs/{rid}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_lab_lists_bots_with_their_forward_need_and_the_data_range(open_api):
    c, _, _ = open_api
    j = c.get("/api/admin/lab/bots").json()
    by = {b["name"]: b for b in j["bots"]}
    assert by["forward_teacher"]["needs_forward"] and not by["random_entry"]["needs_forward"] and by["learned"]["params"]["model"]["type"] == "str"
    assert j["delay_s"] == 300 and set(j["symbols"]) == {"EURUSD", "EURJPY"}
    rng = c.get("/api/admin/lab/range").json()
    assert rng["symbols"]["EURUSD"]["from_ms"] < T0 < rng["symbols"]["EURUSD"]["to_ms"] <= LIVE_END


@pytest.mark.parametrize("body", [
    {"bot": "forward_teacher", "date_from": "2025-03-04", "date_to": "2025-03-04"},                       # teacher without forward data
    {"bot": "nope", "date_from": "2025-03-04", "date_to": "2025-03-04"},
    {"bot": "random_entry", "date_from": "2025-03-05", "date_to": "2025-03-04"},
    {"bot": "random_entry", "date_from": "2025-03-04", "date_to": "2025-03-04", "forward_s": 99999},
    {"bot": "random_entry", "params": {"p_enter": 9}, "date_from": "2025-03-04", "date_to": "2025-03-04"},
    {"bot": "random_entry", "symbols": ["XAUUSD"], "date_from": "2025-03-04", "date_to": "2025-03-04"},
    {"bot": "learned", "params": {"model": "ghost"}, "date_from": "2025-03-04", "date_to": "2025-03-04"},
])
def test_bad_replay_requests_are_refused_up_front(open_api, body):
    assert open_api[0].post("/api/admin/lab/replay", json=body).status_code == 400


def test_a_forward_replay_runs_as_a_job_is_labelled_and_kept(open_api):
    c, _, _ = open_api
    rid = c.post("/api/admin/lab/replay", json={"bot": "forward_teacher", "symbols": ["EURUSD"], "date_from": "2025-03-04", "date_to": "2025-03-04",
                                                "forward_s": 60}).json()["id"]
    r = wait_done(c, rid)
    assert r["status"] == "done", r.get("error")
    res = r["result"]
    assert r["forward_s"] == 60 and res["tagged_peeked"] is True and res["evaluation"]["summary"]["forward_s"] == 60
    assert res["evaluation"]["summary"]["trades"] > 0 and res["evaluation"]["summary"]["net_usd"] > 0          # hindsight pays
    assert all(d["peeked"] == 1 for d in res["deals"])
    # a blind run of another bot is stored as blind, side by side
    rid2 = c.post("/api/admin/lab/replay", json={"bot": "random_entry", "symbols": ["EURUSD"], "date_from": "2025-03-04", "date_to": "2025-03-04"}).json()["id"]
    r2 = wait_done(c, rid2)
    assert r2["forward_s"] == 0 and r2["result"]["tagged_peeked"] is False and all(d["peeked"] == 0 for d in r2["result"]["deals"])
    rows = c.get("/api/admin/lab/runs").json()["runs"]
    assert [x["id"] for x in rows][:2] == [rid2, rid] and rows[1]["summary"]["trades"] > 0 and rows[1]["forward_s"] == 60
    assert c.delete(f"/api/admin/lab/runs/{rid}").status_code == 200 and c.get(f"/api/admin/lab/runs/{rid}").status_code == 404


def test_the_replay_never_touches_the_history_file_or_the_paper_account(open_api):
    c, _, path = open_api
    before = db.connect(path).execute("SELECT COUNT(*), MAX(t_ms) FROM ticks").fetchone()
    acct = c.get("/api/trade/state", params={"delay": 300}).json()["account"]
    rid = c.post("/api/admin/lab/replay", json={"bot": "forward_teacher", "symbols": ["EURUSD"], "date_from": "2025-03-04", "date_to": "2025-03-04", "forward_s": 60}).json()["id"]
    assert wait_done(c, rid)["status"] == "done"
    assert db.connect(path).execute("SELECT COUNT(*), MAX(t_ms) FROM ticks").fetchone() == before
    after = c.get("/api/trade/state", params={"delay": 300}).json()["account"]
    assert (after["balance"], after["deals"], after["positions"]) == (acct["balance"], acct["deals"], acct["positions"])


def test_training_requests_are_validated_and_a_job_without_features_fails_cleanly(open_api):
    c, _, _ = open_api
    base = {"name": "t", "symbols": ["EURUSD"], "train_from": "2025-03-04", "train_to": "2025-03-04"}
    assert c.post("/api/admin/lab/train", json={**base, "name": "../x"}).status_code == 400
    assert c.post("/api/admin/lab/train", json={**base, "test_from": "2025-03-04", "test_to": "2025-03-04"}).status_code == 400     # test not after train
    assert c.post("/api/admin/lab/train", json={**base, "hold_s": 120, "forward_s": 60}).status_code == 400
    rid = c.post("/api/admin/lab/train", json=base).json()["id"]
    r = wait_done(c, rid)
    assert r["status"] == "failed" and "features" in r["error"]
    assert c.get("/api/admin/lab/runs").json()["running"] is None


def test_live_forward_is_capped_by_the_delay_and_trades_are_tagged(open_api):
    c, clock, path = open_api
    over = c.post("/api/admin/lab/live", json={"bot": "forward_teacher", "forward_s": 600})
    assert over.status_code == 400 and "delay" in over.json()["detail"]
    assert c.post("/api/admin/lab/live", json={"bot": "forward_teacher", "forward_s": 0}).status_code == 400
    r = c.post("/api/admin/lab/live", json={"bot": "forward_teacher", "symbols": ["EURUSD", "EURJPY"], "forward_s": 60})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["running"] and j["bot"]["forward_s"] == 60 and any("FORWARD" in x["detail"] for x in j["log"])
    assert c.post("/api/admin/lab/live", json={"bot": "random_entry"}).status_code == 409                        # one bot at a time
    for _ in range(6):                                                                                            # let chart time pass
        clock.t += 30_000
        c.app.state.bots.heartbeat(clock())
    st = c.get("/api/admin/lab/live").json()
    assert st["stats"]["steps"] >= 2
    eng = c.app.state.engine
    mine = [p for p in eng.positions_view() if p["source"] == "bot"] + [d for d in eng.history(100) if d["source"] == "bot"]
    assert all(x["peeked"] == 1 for x in mine)
    assert st["deals_by_tag"]["clean"]["trades"] == 0 and st["deals_by_tag"]["peeked"]["trades"] == len([d for d in eng.history(100) if d["source"] == "bot"])
    assert c.post("/api/bot/stop", json={"close_positions": True}).status_code == 200


def test_a_blind_live_bot_stays_clean(open_api):
    c, clock, _ = open_api
    assert c.post("/api/admin/lab/live", json={"bot": "random_entry", "symbols": ["EURUSD"], "params": {"p_enter": 1.0}}).status_code == 200
    for _ in range(6):
        clock.t += 30_000
        c.app.state.bots.heartbeat(clock())
    eng = c.app.state.engine
    mine = [p for p in eng.positions_view() if p["source"] == "bot"] + [d for d in eng.history(100) if d["source"] == "bot"]
    assert mine and all(x["peeked"] == 0 for x in mine)


# ================================================================================================ the delay cannot be dodged from the public side
def test_a_public_client_cannot_ask_the_stream_for_fresher_data(secured):
    c, clock, _ = secured
    with c.websocket_connect("/ws/stream?symbols=EURUSD&delay=1") as ws:            # asks for 1 s, gets the terminal's 300 s
        m = ws.receive_json()
        assert m["type"] == "backfill" and m["delay"] == 300 and m["cutoff_ms"] == LIVE_END - 300_000
        ws.send_json({"type": "set_delay", "delay": 1})
        seen = []
        for _ in range(6):
            seen.append(ws.receive_json())
            if seen[-1]["type"] == "error":
                break
        assert any(x["type"] == "error" and "set by the server" in x["message"] for x in seen)
        ws.send_json({"type": "set_delay", "delay": 300})                           # restating the server's own value is fine
    login(c)
    with c.websocket_connect("/ws/stream?symbols=EURUSD&delay=60") as ws:           # the back office may choose its own
        assert ws.receive_json()["delay"] == 60


def test_the_terminal_stream_follows_the_delay_the_admin_sets(secured):
    c, clock, _ = secured
    login(c)
    c.put("/api/admin/terminal", json={"delay_s": 120})
    c.cookies.clear()                                                               # now a plain terminal client
    with c.websocket_connect("/ws/stream?symbols=EURUSD&delay=300") as ws:
        assert ws.receive_json()["delay"] == 120
        login(c)
        c.put("/api/admin/terminal", json={"delay_s": 90})
        ws.send_json({"type": "set_delay", "delay": 90})
        got = None
        for _ in range(10):
            m = ws.receive_json()
            if m["type"] == "backfill":
                got = m
                break
        assert got and got["delay"] == 90


def test_a_public_client_cannot_trade_on_fresher_prices(secured):
    c, clock, _ = secured
    body = {"symbol": "EURUSD", "side": "buy", "volume": 0.1, "delay": 1}
    r = c.post("/api/trade/orders", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["at_ms"] == LIVE_END - 300_000 and c.app.state.engine.delay_s == 300      # the 1 s was ignored; nothing was changed
    assert c.get("/api/trade/state", params={"delay": 1}).json()["account"]["delay_s"] == 300
    login(c)
    r2 = c.post("/api/trade/orders", json={**body, "delay": 200})                            # (shorter: the account clock never moves back)
    assert r2.status_code == 200 and r2.json()["at_ms"] == LIVE_END - 200_000                 # the back office can


# ================================================================================================ MT5 order features over HTTP
def test_stop_limit_and_expiration_through_the_api(open_api):
    c, clock, _ = open_api
    q = c.get("/api/terminal/quotes").json()["quotes"][0]
    sym, ask, bid = q["symbol"], q["ask"], q["bid"]
    pt = 10 ** -q["digits"]
    r = c.post("/api/trade/orders", json={"symbol": sym, "side": "buy", "type": "stop_limit", "volume": 0.1, "delay": 300,
                                          "price": round(ask + 200 * pt, 5), "stoplimit": round(ask + 100 * pt, 5), "expiration": "today"})
    assert r.status_code == 200, r.text
    o = r.json()["orders"][0]
    at = r.json()["at_ms"]
    assert o["type"] == "stop_limit" and o["stoplimit"] == round(ask + 100 * pt, 5)
    # today = the end of the chart's day in the terminal's zone (America/New_York): within 24 h, after the order time
    assert at < o["expire_ms"] <= at + 86_400_000
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    end = datetime.fromtimestamp((o["expire_ms"] + 1) / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/New_York"))
    assert (end.hour, end.minute, end.second) == (0, 0, 0)
    spec = c.post("/api/trade/orders", json={"symbol": sym, "side": "sell", "type": "limit", "volume": 0.1, "delay": 300,
                                             "price": round(bid + 200 * pt, 5), "expiration": "specified", "expire_ms": at + 90_000})
    assert spec.status_code == 200 and spec.json()["orders"][-1]["expire_ms"] == at + 90_000
    assert c.post("/api/trade/orders", json={"symbol": sym, "side": "sell", "type": "limit", "volume": 0.1, "delay": 300,
                                             "price": round(bid + 200 * pt, 5), "expiration": "specified"}).status_code == 400
    assert c.post("/api/trade/orders", json={"symbol": sym, "side": "sell", "type": "limit", "volume": 0.1, "delay": 300,
                                             "price": round(bid + 200 * pt, 5), "expiration": "someday"}).status_code == 400
    c.delete(f"/api/trade/orders/{o['id']}", params={"delay": 300})
    h = c.get("/api/trade/orders_history").json()["orders"]
    assert any(x["id"] == o["id"] and x["status"] == "cancelled" and x["done_ms"] for x in h)


def test_an_order_racing_the_heartbeat_is_executed_not_refused(open_api):
    """The account clock can be a few ms ahead of a request's chart time (the heartbeat got the lock first): not a reason to refuse."""
    c, clock, _ = open_api
    eng = c.app.state.engine
    c.get("/api/trade/state", params={"delay": 300})
    eng.advance_to(eng.t_engine + 400)                     # the heartbeat ran 400 ms of chart time ahead of the next request
    sym = c.get("/api/terminal/quotes").json()["quotes"][0]["symbol"]
    r = c.post("/api/trade/orders", json={"symbol": sym, "side": "buy", "volume": 0.1, "delay": 300})
    assert r.status_code == 200, r.text
    assert r.json()["at_ms"] == eng.t_engine
    clock.t += 1000                                         # a REAL lag (seconds) is still refused
    eng.advance_to(eng.t_engine + 30_000)
    r2 = c.post("/api/trade/orders", json={"symbol": sym, "side": "buy", "volume": 0.1, "delay": 300})
    assert r2.status_code == 409 and r2.json()["code"] == "clock_behind"
