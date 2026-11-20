"""MT5 order features the terminal exposes: stop-limit orders, expiration (GTC / today / specified) and the order history."""
import sqlite3

import pytest

from engine.account import Engine, ensure_schema
from engine.orders import EngineError
from tests.conftest import T0
from tests.test_engine import PIP, S, cfg_, make


def path(points, step=10 * S, spread=1.0):
    """EURUSD bid path, one tick every `step`."""
    return {"EURUSD": [(i * step, round(b, 5), round(b + spread * PIP, 5)) for i, b in enumerate(points)]}


UP_THEN_BACK = [1.1000, 1.1005, 1.1010, 1.1015, 1.1020, 1.1015, 1.1010, 1.1005, 1.1000, 1.0995, 1.0995, 1.0995]


def test_buy_stop_limit_becomes_a_buy_limit_when_the_stop_is_reached_and_fills_on_the_pullback():
    eng, _ = make(path(UP_THEN_BACK))
    r = eng.place_order("EURUSD", "buy", "stop_limit", 0.1, price=1.1016, stoplimit=1.1006, at_ms=T0)
    assert r["status"] == "working"
    eng.advance_to(T0 + 40 * S)                                   # ask 1.1021 >= stop 1.1016: now a buy LIMIT at 1.1006
    o = eng.orders_view()[0]
    assert o["type"] == "limit" and o["price"] == 1.1006 and o["stoplimit"] is None and not eng.positions
    eng.advance_to(T0 + 70 * S)                                   # ask comes back to 1.1006 at t=70 s -> filled there
    pos = eng.positions_view()
    assert len(pos) == 1 and pos[0]["open_price"] == pytest.approx(1.1006) and pos[0]["open_ms"] == T0 + 70 * S


def test_sell_stop_limit_mirror():
    pts = [1.1000, 1.0995, 1.0990, 1.0985, 1.0990, 1.0995, 1.1000, 1.1000]
    eng, _ = make(path(pts))
    eng.place_order("EURUSD", "sell", "stop_limit", 0.1, price=1.0986, stoplimit=1.0994, at_ms=T0)
    eng.advance_to(T0 + 70 * S)
    pos = eng.positions_view()
    assert len(pos) == 1 and pos[0]["side"] == "sell" and pos[0]["open_price"] == pytest.approx(1.0995)    # first bid >= 1.0994


def test_a_stop_limit_never_reached_never_trades():
    eng, _ = make(path([1.1000] * 12))
    eng.place_order("EURUSD", "buy", "stop_limit", 0.1, price=1.1016, stoplimit=1.1006, at_ms=T0)
    eng.advance_to(T0 + 110 * S)
    assert not eng.positions and eng.orders_view()[0]["type"] == "stop_limit"


@pytest.mark.parametrize("side,price,stoplimit,why", [
    ("buy", 1.0990, 1.0980, "wrong side of the market"),          # a buy stop must be above the ask
    ("buy", 1.1016, 1.1020, "below the stop price"),              # the limit must be below the stop for a buy
    ("sell", 1.0986, 1.0980, "above the stop price"),
    ("buy", 1.1016, None, "needs a stop limit price"),
])
def test_bad_stop_limits_are_refused(side, price, stoplimit, why):
    eng, _ = make(path([1.1000] * 12))
    with pytest.raises(EngineError, match=why):
        eng.place_order("EURUSD", side, "stop_limit", 0.1, price=price, stoplimit=stoplimit, at_ms=T0)
    with pytest.raises(EngineError, match="only stop limit"):
        eng.place_order("EURUSD", "buy", "limit", 0.1, price=1.0990, stoplimit=1.0980, at_ms=T0)


def test_stop_limit_sl_tp_are_judged_from_the_limit_price():
    eng, _ = make(path([1.1000] * 12))
    with pytest.raises(EngineError):                               # SL above the eventual entry (1.1006) for a buy: refused
        eng.place_order("EURUSD", "buy", "stop_limit", 0.1, price=1.1016, stoplimit=1.1006, sl=1.1010, at_ms=T0)
    eng.place_order("EURUSD", "buy", "stop_limit", 0.1, price=1.1016, stoplimit=1.1006, sl=1.1000, tp=1.1030, at_ms=T0)


def test_pending_orders_expire_and_say_so():
    eng, _ = make(path([1.1000] * 12))
    r = eng.place_order("EURUSD", "buy", "limit", 0.1, price=1.0990, expire_ms=T0 + 35 * S, at_ms=T0)
    eng.advance_to(T0 + 30 * S)
    assert eng.orders_view()
    eng.advance_to(T0 + 60 * S)
    assert not eng.orders_view()
    h = eng.order_history()
    assert h[0]["id"] == r["id"] and h[0]["status"] == "expired" and h[0]["done_ms"] == T0 + 40 * S       # the first tick at/after it
    with pytest.raises(EngineError, match="already passed"):
        eng.place_order("EURUSD", "buy", "limit", 0.1, price=1.0990, expire_ms=T0 + 50 * S, at_ms=T0 + 60 * S)
    with pytest.raises(EngineError, match="does not expire"):
        eng.place_order("EURUSD", "buy", "market", 0.1, expire_ms=T0 + 999 * S, at_ms=T0 + 60 * S)


def test_an_order_on_a_quiet_symbol_still_expires():
    eng, _ = make({**path([1.1000] * 12), "GBPUSD": [(0, 1.3000, 1.3001)]})
    eng.place_order("GBPUSD", "buy", "limit", 0.1, price=1.2900, expire_ms=T0 + 20 * S, at_ms=T0)
    eng.advance_to(T0 + 60 * S)
    assert not eng.orders_view() and eng.order_history()[0]["status"] == "expired"


def test_order_history_lists_filled_cancelled_and_rejected_with_their_times():
    eng, _ = make(path(UP_THEN_BACK))
    a = eng.place_order("EURUSD", "buy", "market", 0.1, at_ms=T0)
    b = eng.place_order("EURUSD", "sell", "limit", 0.1, price=1.1100, at_ms=T0 + 10 * S)
    eng.cancel_order(b["id"], at_ms=T0 + 20 * S)
    h = {o["id"]: o for o in eng.order_history()}
    assert h[a["id"]]["status"] == "filled" and h[a["id"]]["done_ms"] == T0
    assert h[b["id"]]["status"] == "cancelled" and h[b["id"]]["done_ms"] == T0 + 20 * S
    assert all(o["status"] != "working" for o in h.values())


def test_modify_a_stop_limit_keeps_the_rules():
    eng, _ = make(path([1.1000] * 12))
    r = eng.place_order("EURUSD", "buy", "stop_limit", 0.1, price=1.1016, stoplimit=1.1006, at_ms=T0)
    eng.modify(r["id"], kind="order", price=1.1020, stoplimit=1.1010, at_ms=T0 + 10 * S)
    o = eng.orders_view()[0]
    assert (o["price"], o["stoplimit"]) == (1.1020, 1.1010)
    with pytest.raises(EngineError):
        eng.modify(r["id"], kind="order", stoplimit=1.1030, at_ms=T0 + 10 * S)


def test_an_old_database_is_migrated_and_keeps_its_orders(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript("""CREATE TABLE acct_orders (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, type TEXT, volume REAL, price REAL,
      sl REAL, tp REAL, status TEXT, placed_ms INTEGER, fill_at_ms INTEGER, kind TEXT, target_position INTEGER, source TEXT,
      peeked INTEGER, session_id TEXT, comment TEXT, reason TEXT, position_id INTEGER);
      INSERT INTO acct_orders VALUES (7,'EURUSD','buy','limit',0.1,1.09,NULL,NULL,'working',1,NULL,'open',NULL,'manual',0,NULL,'',
      '',NULL);""")
    c.commit()
    ensure_schema(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(acct_orders)")}
    assert {"stoplimit", "expire_ms", "done_ms"} <= cols
    eng = Engine(cfg_(), c)
    assert eng.orders[7].price == 1.09 and eng.orders[7].expire_ms is None
