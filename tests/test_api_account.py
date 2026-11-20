import time

import pytest
from starlette.testclient import TestClient

from core import db
from server.app import create_app
from tests.conftest import LIVE_END, T0, FakeClock, make_cfg

MIN = 60_000


def cfg_():
    c = make_cfg(("EURUSD", "EURJPY"))
    c.engine.leverage = 100
    return c


@pytest.fixture
def api(tick_db):
    clock = FakeClock(LIVE_END)
    with TestClient(create_app(cfg_(), tick_db, clock, web_dist=None, clock_interval=3600)) as c:   # heartbeat off: driven by hand
        yield c, clock, tick_db


def order(c, **kw):
    body = {"symbol": "EURUSD", "side": "buy", "volume": 0.5, "delay": 300}
    body.update(kw)
    return c.post("/api/trade/orders", json=body)


def state(c, delay=300):
    return c.get("/api/trade/state", params={"delay": delay}).json()


def test_market_order_opens_a_position_at_the_chart_time(api):
    c, clock, path = api
    r = order(c, sl=None)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "filled" and j["at_ms"] == clock.t - 300_000
    p = j["positions"][0]
    assert p["symbol"] == "EURUSD" and p["side"] == "buy" and p["volume"] == 0.5 and p["open_ms"] == clock.t - 300_000
    # the fill is the ASK of the newest tick at or before the chart time - nothing later
    tick = db.connect(path).execute("SELECT bid, ask FROM ticks WHERE symbol='EURUSD' AND t_ms<=? ORDER BY t_ms DESC, seq DESC LIMIT 1",
                                    (clock.t - 300_000,)).fetchone()
    assert p["open_price"] == tick[1]
    assert j["account"]["t_engine_ms"] == clock.t - 300_000 and j["account"]["positions"] == 1


def test_invalid_requests_get_clear_errors(api):
    c, clock, _ = api
    cases = [({"volume": 0.005}, "bad_volume"), ({"volume": 99}, "bad_volume"), ({"sl": 9.0}, "invalid_stops"),
             ({"type": "limit"}, "bad_order"), ({"type": "limit", "price": 9.0}, "invalid_price"), ({"symbol": "NOPE"}, "bad_symbol"),
             ({"volume": 40}, "rejected")]
    for kw, code in cases:
        r = order(c, **kw)
        assert r.status_code == 400 and r.json()["code"] == code, (kw, r.text)
    assert order(c, side="sideways").status_code == 400
    assert order(c, bogus=1).status_code == 422                                            # unknown field
    assert state(c)["positions"] == []


def test_trading_is_only_possible_on_a_delayed_chart(api):
    c, _, _ = api
    for d in (0, -5, 10**9):
        assert order(c, delay=d).status_code == 400
        assert c.get("/api/trade/state", params={"delay": d}).status_code == 400
    assert order(c, delay=60).status_code == 200


def test_increasing_the_delay_pauses_trading_until_the_chart_catches_up(api):
    c, clock, _ = api
    assert order(c, delay=60).status_code == 200
    r = order(c, delay=900)                                                                 # chart jumps 14 min back: older than the account clock
    assert r.status_code == 409 and r.json()["code"] == "clock_behind"
    clock.t += 14 * MIN                                                                     # ...and a real 14 minutes later it is allowed again... (data ends, so only the check matters)
    assert state(c, 300)["account"]["t_engine_ms"] >= LIVE_END - 60_000


def test_modify_sl_tp_set_clear_and_validate(api):
    c, _, _ = api
    pid = order(c, delay=300).json()["position_id"]
    p = state(c)["positions"][0]
    bid = p["price"]
    r = c.patch(f"/api/trade/positions/{pid}", json={"sl": round(bid - 0.0010, 5), "tp": round(bid + 0.0020, 5), "delay": 300})
    assert r.status_code == 200 and r.json()["positions"][0]["sl"] == round(bid - 0.0010, 5)
    r = c.patch(f"/api/trade/positions/{pid}", json={"tp": None, "delay": 300})                  # null clears, absent keeps
    pos = r.json()["positions"][0]
    assert pos["tp"] is None and pos["sl"] == round(bid - 0.0010, 5)
    bad = c.patch(f"/api/trade/positions/{pid}", json={"sl": round(bid + 0.0050, 5), "delay": 300})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_stops"
    assert c.patch("/api/trade/positions/999", json={"sl": 1.0, "delay": 300}).status_code == 400


def test_pending_order_lifecycle_modify_and_cancel(api):
    c, _, _ = api
    bid = state(c)["account"] and order(c, volume=0.1).json()["positions"][0]["price"]       # a quote to place around
    lim = c.post("/api/trade/orders", json={"symbol": "EURUSD", "side": "buy", "type": "limit", "volume": 0.1,
                                            "price": round(bid - 0.0100, 5), "delay": 300}).json()
    assert lim["status"] == "working" and len(lim["orders"]) == 1
    oid = lim["id"]
    r = c.patch(f"/api/trade/orders/{oid}", json={"price": round(bid - 0.0050, 5), "sl": round(bid - 0.0200, 5), "delay": 300})
    assert r.status_code == 200 and r.json()["orders"][0]["price"] == round(bid - 0.0050, 5)
    r = c.delete(f"/api/trade/orders/{oid}", params={"delay": 300})
    assert r.status_code == 200 and r.json()["orders"] == []
    assert c.delete(f"/api/trade/orders/{oid}", params={"delay": 300}).status_code == 400


def test_partial_close_close_all_history_and_equity(api):
    c, clock, _ = api
    a = order(c, volume=1.0).json()["position_id"]
    order(c, side="sell", volume=0.4)
    r = c.post(f"/api/trade/positions/{a}/close", json={"volume": 0.3, "delay": 300})
    assert r.status_code == 200 and r.json()["deal"]["volume"] == 0.3
    pos = {p["id"]: p for p in r.json()["positions"]}
    assert pos[a]["volume"] == pytest.approx(0.7)
    r = c.post("/api/trade/positions/close_all", json={"delay": 300})
    assert len(r.json()["closed"]) == 2 and r.json()["positions"] == []
    deals = c.get("/api/trade/history").json()["deals"]
    assert len(deals) == 3 and {d["source"] for d in deals} == {"manual"}
    assert all(d["reason"] == "manual" for d in deals)
    assert len(c.get("/api/trade/equity").json()["points"]) >= 1
    acct = state(c)["account"]
    assert acct["deals"] == 3 and acct["positions"] == 0 and acct["balance"] == pytest.approx(10000 + acct["realized"])


def test_trades_are_tagged_clean_or_peeked(api):
    c, clock, path = api
    a = order(c, session_id="s1").json()["position_id"]
    b = order(c, peeked=True).json()["position_id"]
    db.log_peek(db.connect(path), clock.t, "s2", "EURUSD", "open")
    d = order(c, session_id="s2").json()["position_id"]                                      # peek session still open
    db.log_peek(db.connect(path), clock.t, "s2", "EURUSD", "close")
    e = order(c, session_id="s2").json()["position_id"]                                      # closed again -> clean
    got = {p["id"]: p["peeked"] for p in state(c)["positions"]}
    assert got == {a: 0, b: 1, d: 1, e: 0}


def test_reset_needs_confirmation(api):
    c, _, _ = api
    order(c)
    assert c.post("/api/trade/account/reset", json={}).status_code == 400
    r = c.post("/api/trade/account/reset", json={"confirm": True, "balance": 2500, "leverage": 30})
    assert r.status_code == 200 and r.json()["account"]["balance"] == 2500 and r.json()["positions"] == []
    assert r.json()["account"]["leverage"] == 30 and c.get("/api/trade/history").json()["deals"] == []


def test_the_background_clock_triggers_stops_with_no_client_connected(tick_db):
    """Place a trade, then only let the heartbeat run: the account must keep following the delayed chart and
    close the position by itself when SL/TP is touched."""
    clock = FakeClock(T0 + 3 * MIN + 300_000)                                                # chart (delay 300) shows T0 + 3 min
    with TestClient(create_app(cfg_(), tick_db, clock, web_dist=None, clock_interval=0.02)) as c:
        j = order(c).json()
        pid, entry = j["position_id"], j["positions"][0]["price"]
        assert c.patch(f"/api/trade/positions/{pid}", json={"sl": round(entry - 0.0003, 5), "tp": round(entry + 0.0003, 5), "delay": 300}).status_code == 200
        t_before = c.get("/api/trade/history").json()["deals"]
        assert t_before == []
        for _ in range(8):                                                                    # the market moves on 8 minutes; nobody calls the API
            clock.t += MIN
            time.sleep(0.25)
        deals = c.get("/api/trade/history").json()["deals"]
        assert deals and deals[0]["reason"] in ("sl", "tp") and deals[0]["close_ms"] > T0 + 3 * MIN
        assert deals[0]["close_ms"] <= clock.t - 300_000                                      # never past the delayed clock


def test_the_account_survives_a_server_restart(tick_db):
    clock = FakeClock(LIVE_END)
    with TestClient(create_app(cfg_(), tick_db, clock, web_dist=None, clock_interval=3600)) as c:
        j = order(c, volume=0.7, session_id="abc").json()
        pid = j["position_id"]
        c.patch(f"/api/trade/positions/{pid}", json={"sl": round(j["positions"][0]["price"] - 0.0100, 5), "delay": 300})
        closed = order(c, side="sell", volume=0.2).json()["position_id"]
        c.post(f"/api/trade/positions/{closed}/close", json={"delay": 300})
        before = c.get("/api/trade/state", params={"delay": 300}).json()
        hist_before = c.get("/api/trade/history").json()
    with TestClient(create_app(cfg_(), tick_db, clock, web_dist=None, clock_interval=3600)) as c2:         # brand-new server process
        after = c2.get("/api/trade/state", params={"delay": 300}).json()
        assert after["positions"] == before["positions"] and after["orders"] == before["orders"]
        for k in ("balance", "equity", "margin", "realized", "deals", "t_engine_ms", "peak_equity"):
            assert after["account"][k] == pytest.approx(before["account"][k]), k
        assert c2.get("/api/trade/history").json() == hist_before
        assert c2.post(f"/api/trade/positions/{pid}/close", json={"delay": 300}).status_code == 200       # still tradable
