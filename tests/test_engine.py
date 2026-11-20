"""Paper-trading engine on hand-made tick sequences: every expected number is computed by hand."""
import pytest

from core import db
from core.config import load_config
from engine.account import Engine
from engine.orders import UNSET, EngineError
from tests.conftest import T0

S = 1000
PIP = 1e-4


def cfg_(**over):
    c = load_config()
    c.symbols = ["EURUSD", "EURJPY", "GBPUSD", "GBPJPY"]
    for k, v in over.items():
        sect, key = k.split("__")
        setattr(getattr(c, sect), key, v)
    return c


def make(ticks, cfg=None):
    """ticks: {symbol: [(dt_ms_from_T0, bid, ask), ...]} -> (engine, conn)."""
    conn = db.connect(":memory:")
    rows = []
    for sym, lst in ticks.items():
        db.set_state(conn, f"digits:{sym}", "3" if sym.endswith("JPY") else "5")
        seq, prev = 0, None
        for dt, bid, ask in lst:
            seq = seq + 1 if dt == prev else 0
            prev = dt
            rows.append((sym, T0 + dt, bid, ask, 0, seq))
    db.insert_ticks(conn, rows)
    return Engine(cfg or cfg_(), conn), conn


def flat(sym="EURUSD", bid=1.1000, spread=1.0, n=40, step=10 * S):
    return [(i * step, bid, round(bid + spread * PIP, 5)) for i in range(n)]


def eur(path, spread=1.0):
    """path: [(dt_ms, bid)] -> ticks with a fixed spread."""
    return [(dt, b, round(b + spread * PIP, 5)) for dt, b in path]


# ============================================================================ market orders & P&L
def test_buy_fills_at_ask_and_profit_is_by_hand():
    eng, _ = make({"EURUSD": eur([(0, 1.1000), (10 * S, 1.1000), (20 * S, 1.1010), (30 * S, 1.1050)])})
    r = eng.place_order("EURUSD", "buy", volume=1.0, at_ms=T0 + 10 * S)
    assert r["status"] == "filled" and r["price"] == pytest.approx(1.10010)                 # ask = bid + 1 pip
    eng.advance_to(T0 + 30 * S)
    p = eng.positions_view()[0]
    assert p["price"] == pytest.approx(1.1050) and p["pips"] == pytest.approx(49.0)          # exit side is BID: 1.1050 - 1.1001
    assert p["profit"] == pytest.approx(0.0049 * 100000)                                     # 490 USD
    d = eng.close(r["id"], at_ms=T0 + 30 * S)["deal"]
    assert d["pips"] == pytest.approx(49.0) and d["gross"] == pytest.approx(490.0) and d["net"] == pytest.approx(490.0)
    assert eng.account()["balance"] == pytest.approx(10490.0) and eng.positions_view() == []


def test_sell_fills_at_bid_and_closes_at_ask():
    eng, _ = make({"EURUSD": eur([(0, 1.1000), (10 * S, 1.0980), (20 * S, 1.0980)])})
    r = eng.place_order("EURUSD", "sell", volume=2.0, at_ms=T0)
    assert r["price"] == pytest.approx(1.1000)
    d = eng.close(r["id"], at_ms=T0 + 20 * S)["deal"]
    assert d["close_price"] == pytest.approx(1.09810)                                          # ask = 1.0980 + 1 pip
    assert d["pips"] == pytest.approx(19.0) and d["gross"] == pytest.approx(0.0019 * 200000)   # 380 USD


def test_never_marks_on_mid():
    eng, _ = make({"EURUSD": eur([(0, 1.1000), (10 * S, 1.1000)], spread=4.0)})
    r = eng.place_order("EURUSD", "buy", volume=1.0, at_ms=T0 + 10 * S)
    d = eng.close(r["id"], at_ms=T0 + 10 * S)["deal"]
    assert d["pips"] == pytest.approx(-4.0) and d["gross"] == pytest.approx(-40.0)             # round trip costs the spread


def test_slippage_hits_every_fill_and_commission_is_half_open_half_close():
    cfg = cfg_(costs__extra_slippage_pips=0.5, costs__commission_per_lot_round_trip=7.0)
    eng, _ = make({"EURUSD": eur([(0, 1.1000), (10 * S, 1.1030)])}, cfg)
    r = eng.place_order("EURUSD", "buy", volume=2.0, at_ms=T0)
    assert r["price"] == pytest.approx(1.10010 + 0.5 * PIP)
    assert eng.account()["balance"] == pytest.approx(10000 - 7.0)                              # half of 7 x 2 lots at open
    d = eng.close(r["id"], at_ms=T0 + 10 * S)["deal"]
    assert d["close_price"] == pytest.approx(1.1030 - 0.5 * PIP)
    assert d["pips"] == pytest.approx(29.0 - 1.0 + 0.0, abs=1e-9) or d["pips"] == pytest.approx(28.0)   # 30 - spread 1 - 2 x 0.5 slip... = 28.0
    assert d["gross"] == pytest.approx(28.0 * 10 * 2) and d["commission"] == pytest.approx(14.0)
    assert d["net"] == pytest.approx(560.0 - 14.0)
    assert eng.account()["balance"] == pytest.approx(10000 + 560.0 - 14.0)                     # the open half is not charged twice
    assert d["net_pips"] == pytest.approx(546.0 / 20.0)                                        # net money / (10 USD per pip x 2 lots)


def test_jpy_pair_pnl_is_converted_with_the_cross_rate():
    eng, _ = make({"EURJPY": eur([(0, 162.000), (10 * S, 162.500)], spread=0) if False else [(0, 162.000, 162.010), (10 * S, 162.500, 162.510)],
                   "EURUSD": [(0, 1.1000, 1.1002), (10 * S, 1.1000, 1.1002)]})
    r = eng.place_order("EURJPY", "buy", volume=1.0, at_ms=T0)
    assert r["price"] == pytest.approx(162.010)
    d = eng.close(r["id"], at_ms=T0 + 10 * S)["deal"]
    assert d["pips"] == pytest.approx(49.0)                                                    # (162.500 - 162.010) / 0.01
    # 0.49 JPY x 100000 units = 49,000 JPY; USD per JPY = EURUSD mid / EURJPY mid at the close
    usd_per_jpy = 1.1001 / ((162.500 + 162.510) / 2)
    assert d["gross"] == pytest.approx(49000 * usd_per_jpy)


# ============================================================================ SL / TP
def test_stop_loss_on_a_long_fills_at_the_bid_of_the_triggering_tick():
    eng, _ = make({"EURUSD": eur([(0, 1.1000), (5 * S, 1.0990), (10 * S, 1.0979), (15 * S, 1.1100)])})
    r = eng.place_order("EURUSD", "buy", volume=1.0, sl=1.0980, at_ms=T0)
    eng.advance_to(T0 + 20 * S)
    d = eng.history()[0]
    assert d["reason"] == "sl" and d["close_ms"] == T0 + 10 * S and d["close_price"] == pytest.approx(1.0979)   # a gap: worse than the SL
    assert d["gross"] == pytest.approx((1.0979 - 1.10010) * 100000) and eng.positions_view() == []


def test_take_profit_on_a_short_uses_the_ask():
    eng, _ = make({"EURUSD": eur([(0, 1.1000), (5 * S, 1.0950), (10 * S, 1.0900), (15 * S, 1.0800)])})
    eng.place_order("EURUSD", "sell", volume=1.0, tp=1.0920, at_ms=T0)
    eng.advance_to(T0 + 20 * S)
    d = eng.history()[0]
    # short closes at ASK: 1.0900 + 1 pip = 1.0901 <= tp 1.0920 -> triggered at 10 s (the 5 s ask 1.0951 was above the TP)
    assert d["reason"] == "tp" and d["close_ms"] == T0 + 10 * S and d["close_price"] == pytest.approx(1.0901)


def test_a_longs_stop_watches_the_bid_not_the_ask_and_an_exact_touch_triggers():
    # spread is 3 pips: ask is always 3 pips above bid. The stop sits at 1.0990.
    path = [(0, 1.1000, 1.1003), (5 * S, 1.0995, 1.0998), (10 * S, 1.0990, 1.0993), (15 * S, 1.1050, 1.1053)]
    eng, _ = make({"EURUSD": path})
    eng.place_order("EURUSD", "buy", volume=1.0, sl=1.0990, at_ms=T0)
    eng.advance_to(T0 + 9 * S)
    assert eng.history() == []                                   # the ask (1.0998) is above the stop, the bid (1.0995) too
    eng.advance_to(T0 + 12 * S)
    d = eng.history()[0]
    assert d["reason"] == "sl" and d["close_ms"] == T0 + 10 * S and d["close_price"] == pytest.approx(1.0990)   # bid == SL exactly -> triggered


def test_a_shorts_stop_watches_the_ask():
    path = [(0, 1.1000, 1.1003), (5 * S, 1.1010, 1.1013), (10 * S, 1.1017, 1.1020), (15 * S, 1.1010, 1.1013)]
    eng, _ = make({"EURUSD": path})
    eng.place_order("EURUSD", "sell", volume=1.0, sl=1.1020, at_ms=T0)
    eng.advance_to(T0 + 9 * S)
    assert eng.history() == []                                   # the BID (1.1017) is near the stop but the ask (1.1013) is not at it yet
    eng.advance_to(T0 + 12 * S)
    assert eng.history()[0]["reason"] == "sl" and eng.history()[0]["close_price"] == pytest.approx(1.1020)


def test_sl_and_tp_are_optional_and_positions_can_be_held_with_neither():
    eng, _ = make({"EURUSD": flat(n=30)})
    r = eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0)
    eng.advance_to(T0 + 200 * S)
    p = eng.positions_view()[0]
    assert p["sl"] is None and p["tp"] is None and eng.history() == []


def test_invalid_stops_are_refused_like_mt5():
    eng, _ = make({"EURUSD": flat(n=5)})
    for kw in ({"sl": 1.1010}, {"tp": 1.0990}):                              # buy: SL above / TP below the price
        with pytest.raises(EngineError) as e:
            eng.place_order("EURUSD", "buy", volume=1.0, at_ms=T0, **kw)
        assert e.value.code == "invalid_stops"
    for kw in ({"sl": 1.0990}, {"tp": 1.1010}):                              # sell mirrored
        with pytest.raises(EngineError):
            eng.place_order("EURUSD", "sell", volume=1.0, at_ms=T0, **kw)
    assert eng.positions_view() == [] and eng.account()["balance"] == 10000


def test_minimum_stop_distance_is_enforced_from_config():
    eng, _ = make({"EURUSD": flat(n=5)}, cfg_(engine__min_stop_pips=5.0))
    with pytest.raises(EngineError):
        eng.place_order("EURUSD", "buy", volume=1.0, sl=1.0997, at_ms=T0)    # 3 pips < 5
    eng.place_order("EURUSD", "buy", volume=1.0, sl=1.0990, at_ms=T0)


# ============================================================================ modify / close / pending
def test_modify_position_sl_tp_and_clear():
    eng, _ = make({"EURUSD": flat(n=30)})
    r = eng.place_order("EURUSD", "buy", volume=1.0, sl=1.0950, at_ms=T0)
    p = eng.modify(r["id"], sl=1.0970, tp=1.1100, at_ms=T0 + 20 * S)
    assert (p["sl"], p["tp"]) == (1.0970, 1.1100)
    p = eng.modify(r["id"], tp=None, at_ms=T0 + 30 * S)                       # None clears, UNSET keeps
    assert (p["sl"], p["tp"]) == (1.0970, None)
    with pytest.raises(EngineError):
        eng.modify(r["id"], sl=1.1050, at_ms=T0 + 40 * S)                      # above the price for a long
    with pytest.raises(EngineError):
        eng.modify(999, at_ms=T0 + 40 * S)


def test_moving_the_stop_changes_what_happens():
    path = eur([(0, 1.1000), (10 * S, 1.0960), (20 * S, 1.0940), (30 * S, 1.1000)])
    eng, _ = make({"EURUSD": path})
    r = eng.place_order("EURUSD", "buy", volume=1.0, sl=1.0930, at_ms=T0)
    eng.modify(r["id"], sl=1.0950, at_ms=T0 + 10 * S)                          # tighten: now the 20 s tick stops it
    eng.advance_to(T0 + 40 * S)
    assert eng.history()[0]["reason"] == "sl" and eng.history()[0]["close_ms"] == T0 + 20 * S


def test_partial_close_splits_volume_commission_and_keeps_the_rest_open():
    cfg = cfg_(costs__commission_per_lot_round_trip=6.0)
    eng, _ = make({"EURUSD": eur([(0, 1.1000), (10 * S, 1.1010)])}, cfg)
    r = eng.place_order("EURUSD", "buy", volume=1.0, at_ms=T0)
    assert eng.account()["balance"] == pytest.approx(10000 - 3.0)
    d = eng.close(r["id"], 0.4, at_ms=T0 + 10 * S)["deal"]
    assert d["volume"] == 0.4 and d["pips"] == pytest.approx(9.0) and d["gross"] == pytest.approx(9 * 10 * 0.4)
    assert d["commission"] == pytest.approx(2.4) and d["net"] == pytest.approx(36.0 - 2.4)
    p = eng.positions_view()[0]
    assert p["volume"] == pytest.approx(0.6) and p["id"] == r["id"]
    # balance: 10000 - 3.0 (open half of 1 lot) + 36.0 - 1.2 (close half of 0.4 lots)
    assert eng.account()["balance"] == pytest.approx(10000 - 3.0 + 36.0 - 1.2)
    eng.close(r["id"], at_ms=T0 + 10 * S)
    assert eng.account()["balance"] == pytest.approx(10000 + 9 * 10 * 1.0 - 6.0)              # whole trade: gross 90 - 6 commission
    assert eng.positions_view() == []
    with pytest.raises(EngineError):
        eng.close(r["id"], at_ms=T0 + 10 * S)
    r2 = eng.place_order("EURUSD", "buy", volume=0.5, at_ms=T0 + 10 * S)
    with pytest.raises(EngineError):
        eng.close(r2["id"], 0.6, at_ms=T0 + 10 * S)                              # more than the position holds


def test_close_all_by_symbol_and_everything():
    eng, _ = make({"EURUSD": flat(n=10), "GBPUSD": flat("GBPUSD", 1.2700, n=10)})
    eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0)
    eng.place_order("EURUSD", "sell", volume=0.2, at_ms=T0)
    eng.place_order("GBPUSD", "buy", volume=0.3, at_ms=T0)
    eng.place_order("EURUSD", "buy", "limit", 0.1, price=1.0900, at_ms=T0)
    out = eng.close_all("EURUSD", at_ms=T0 + 10 * S)
    assert len(out["closed"]) == 2 and len(out["cancelled"]) == 1
    assert [p["symbol"] for p in eng.positions_view()] == ["GBPUSD"]
    assert len(eng.close_all(at_ms=T0 + 20 * S)["closed"]) == 1 and eng.positions_view() == []


def test_pending_orders_trigger_on_the_right_side_and_fill_at_the_tick_price():
    path = [(0, 1.1000, 1.1001), (10 * S, 1.0985, 1.0986), (20 * S, 1.0950, 1.0951), (30 * S, 1.1040, 1.1041)]
    eng, _ = make({"EURUSD": path})
    lim = eng.place_order("EURUSD", "buy", "limit", 1.0, price=1.0990, at_ms=T0)          # waits for ask <= 1.0990
    stp = eng.place_order("EURUSD", "buy", "stop", 1.0, price=1.1030, at_ms=T0)           # waits for ask >= 1.1030
    assert lim["status"] == "working" and len(eng.orders_view()) == 2
    eng.advance_to(T0 + 15 * S)
    pos = {p["id"]: p for p in eng.positions_view()}
    assert set(pos) == {lim["id"]} and pos[lim["id"]]["open_price"] == pytest.approx(1.0986)   # ask of the 10 s tick, better than 1.0990
    eng.advance_to(T0 + 40 * S)
    pos = {p["id"]: p for p in eng.positions_view()}
    assert pos[stp["id"]]["open_price"] == pytest.approx(1.1041) and pos[stp["id"]]["open_ms"] == T0 + 30 * S   # stop fills at the (worse) tick
    assert eng.orders_view() == []


def test_pending_orders_are_judged_on_the_correct_side_of_a_wide_spread():
    """With a 3-pip spread a level can lie between the bid and the ask: only the right side may trigger it.
    buy limit / buy stop watch the ASK; sell limit / sell stop watch the BID."""
    path = [(0, 1.1000, 1.1003), (10 * S, 1.0989, 1.0992), (20 * S, 1.0985, 1.0988),
            (30 * S, 1.1021, 1.1024), (40 * S, 1.1032, 1.1035)]
    eng, _ = make({"EURUSD": path})
    eng.place_order("EURUSD", "buy", "limit", 1.0, price=1.0990, at_ms=T0)           # needs ASK <= 1.0990
    eng.advance_to(T0 + 15 * S)
    assert eng.positions_view() == []                          # 10 s: bid 1.0989 is under the level, the ask 1.0992 is not
    eng.advance_to(T0 + 25 * S)
    assert eng.positions_view()[0]["open_ms"] == T0 + 20 * S   # 20 s: ask 1.0988 <= 1.0990
    eng.close_all(at_ms=T0 + 25 * S)

    eng.place_order("EURUSD", "buy", "stop", 1.0, price=1.1022, at_ms=T0 + 25 * S)    # needs ASK >= 1.1022
    eng.place_order("EURUSD", "sell", "limit", 1.0, price=1.1022, at_ms=T0 + 25 * S)  # needs BID >= 1.1022
    eng.advance_to(T0 + 35 * S)
    pos = eng.positions_view()
    assert [(p["side"], p["open_ms"]) for p in pos] == [("buy", T0 + 30 * S)]       # 30 s: ask 1.1024 >= level (buy stop fires); bid 1.1021 < level (sell limit must NOT)
    eng.advance_to(T0 + 45 * S)
    assert sorted((p["side"], p["open_ms"]) for p in eng.positions_view()) == [("buy", T0 + 30 * S), ("sell", T0 + 40 * S)]   # 40 s: bid 1.1032 >= level


def test_sell_limit_and_sell_stop():
    path = [(0, 1.1000, 1.1001), (10 * S, 1.1022, 1.1023), (20 * S, 1.0968, 1.0969)]
    eng, _ = make({"EURUSD": path})
    a = eng.place_order("EURUSD", "sell", "limit", 1.0, price=1.1020, at_ms=T0)          # bid >= 1.1020
    b = eng.place_order("EURUSD", "sell", "stop", 1.0, price=1.0970, at_ms=T0)           # bid <= 1.0970
    eng.advance_to(T0 + 30 * S)
    pos = {p["id"]: p for p in eng.positions_view()}
    assert pos[a["id"]]["open_price"] == pytest.approx(1.1022) and pos[b["id"]]["open_price"] == pytest.approx(1.0968)


def test_pending_order_sl_tp_become_the_positions_and_apply_from_the_fill_tick():
    path = [(0, 1.1000, 1.1001), (10 * S, 1.0985, 1.0986), (20 * S, 1.0960, 1.0961)]
    eng, _ = make({"EURUSD": path})
    eng.place_order("EURUSD", "buy", "limit", 1.0, price=1.0990, sl=1.0970, tp=1.1100, at_ms=T0)
    eng.advance_to(T0 + 30 * S)
    d = eng.history()[0]
    assert d["reason"] == "sl" and d["open_ms"] == T0 + 10 * S and d["close_ms"] == T0 + 20 * S


def test_pending_price_validation_modify_and_cancel():
    eng, _ = make({"EURUSD": flat(n=10)})
    for typ, side, price in (("limit", "buy", 1.1005), ("stop", "buy", 1.0990), ("limit", "sell", 1.0995), ("stop", "sell", 1.1005)):
        with pytest.raises(EngineError) as e:
            eng.place_order("EURUSD", side, typ, 1.0, price=price, at_ms=T0)
        assert e.value.code == "invalid_price"
    o = eng.place_order("EURUSD", "buy", "limit", 1.0, price=1.0950, sl=1.0900, at_ms=T0)
    m = eng.modify(o["id"], kind="order", price=1.0960, tp=1.1100, at_ms=T0 + 10 * S)
    assert (m["price"], m["sl"], m["tp"]) == (1.0960, 1.0900, 1.1100)
    with pytest.raises(EngineError):
        eng.modify(o["id"], kind="order", price=1.2000, at_ms=T0 + 20 * S)            # a limit above the market
    assert eng.cancel_order(o["id"], at_ms=T0 + 20 * S)["status"] == "cancelled" and eng.orders_view() == []
    with pytest.raises(EngineError):
        eng.cancel_order(o["id"], at_ms=T0 + 30 * S)


# ============================================================================ margin, stop-out, validation
def test_margin_and_free_margin_by_hand():
    eng, _ = make({"EURUSD": [(0, 1.1000, 1.1002), (10 * S, 1.1000, 1.1002)], "EURJPY": [(0, 162.0, 162.01), (10 * S, 162.0, 162.01)]})
    eng.place_order("EURUSD", "buy", volume=1.0, at_ms=T0)
    a = eng.account()
    assert a["margin"] == pytest.approx(100000 * 1.1001 / 100)                          # 1100.10: base price in USD / leverage
    eng.place_order("EURJPY", "buy", volume=2.0, at_ms=T0)
    a = eng.account()
    assert a["margin"] == pytest.approx(1100.10 + 2 * 100000 * 1.1001 / 100)             # EUR margin valued in USD via EURUSD
    assert a["free_margin"] == pytest.approx(a["equity"] - a["margin"]) and a["margin_level"] == pytest.approx(a["equity"] / a["margin"] * 100)


def test_an_order_that_needs_more_margin_than_is_free_is_rejected():
    eng, _ = make({"EURUSD": flat(n=5)})
    with pytest.raises(EngineError) as e:
        eng.place_order("EURUSD", "buy", volume=10.0, at_ms=T0)                          # needs ~11,000 USD, only 10,000
    assert e.value.code == "rejected" and "not enough money" in e.value.message
    eng.place_order("EURUSD", "buy", volume=9.0, at_ms=T0)
    with pytest.raises(EngineError):
        eng.place_order("EURUSD", "buy", volume=0.5, at_ms=T0)                           # free margin is ~100 now
    assert len(eng.positions_view()) == 1


def test_stop_out_closes_the_worst_position_when_margin_level_falls_below_the_limit():
    cfg = cfg_(engine__starting_balance=1200.0)
    path = eur([(0, 1.1000), (10 * S, 1.0950), (20 * S, 1.0920), (30 * S, 1.0900), (40 * S, 1.0900)])
    eng, _ = make({"EURUSD": path}, cfg)
    eng.place_order("EURUSD", "buy", volume=1.0, at_ms=T0)                                # margin ~1100, equity 1200
    eng.advance_to(T0 + 10 * S)
    a = eng.account()
    assert eng.positions_view() and a["margin_level"] > 50                                # -51 pips -> equity ~ 690, level ~ 63%
    eng.advance_to(T0 + 40 * S)
    assert eng.positions_view() == []
    d = eng.history()[0]
    assert d["reason"] == "stopout" and d["close_ms"] <= T0 + 30 * S                      # level crossed 50% around 1.0920 or below
    assert eng.account()["balance"] == pytest.approx(1200 + d["net"]) and eng.account()["balance"] > 0


def test_volume_rules():
    eng, _ = make({"EURUSD": flat(n=5)})
    for v in (0, 0.005, 0.015, 60, -1, None):
        with pytest.raises(EngineError) as e:
            eng.place_order("EURUSD", "buy", volume=v or 0.0, at_ms=T0)
        assert e.value.code == "bad_volume"
    eng.place_order("EURUSD", "buy", volume=0.07, at_ms=T0)                              # a valid step
    for kw in ({"symbol": "NOPE"}, {"side": "long"}, {"order_type": "stop-limit"}, {"source": "robot"}):
        with pytest.raises(EngineError):
            eng.place_order(**{**dict(symbol="EURUSD", side="buy", volume=0.1, at_ms=T0), **kw})


# ============================================================================ the clock, latency, market state
def test_latency_fills_with_the_quote_in_force_after_the_delay():
    cfg = cfg_(engine__latency_ms=3000)
    path = eur([(0, 1.1000), (2 * S, 1.1010), (5 * S, 1.1100), (10 * S, 1.1100)])
    eng, _ = make({"EURUSD": path}, cfg)
    r = eng.place_order("EURUSD", "buy", volume=1.0, at_ms=T0)
    assert r["status"] == "working" and eng.positions_view() == []
    eng.advance_to(T0 + 2 * S)
    assert eng.positions_view() == []                                                      # not yet: fills at T0 + 3 s
    eng.advance_to(T0 + 4 * S)
    p = eng.positions_view()[0]
    assert p["open_price"] == pytest.approx(1.1011) and p["open_ms"] == T0 + 3 * S         # ask of the 2 s tick (quote in force at 3 s)
    c = eng.close(p["id"], at_ms=T0 + 4 * S)
    assert c["status"] == "working"
    eng.advance_to(T0 + 10 * S)
    assert eng.history()[0]["close_price"] == pytest.approx(1.1100)                        # quote in force at 7 s = the 5 s tick


def test_the_clock_never_moves_backwards_and_orders_on_a_rewound_chart_are_refused():
    eng, _ = make({"EURUSD": flat(n=30)})
    eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0 + 100 * S)
    assert eng.account()["t_engine_ms"] == T0 + 100 * S
    assert eng.advance_to(T0 + 50 * S) == 0 and eng.account()["t_engine_ms"] == T0 + 100 * S
    with pytest.raises(EngineError) as e:
        eng.place_order("EURUSD", "sell", volume=0.1, at_ms=T0 + 50 * S)                    # the chart was moved back 50 s
    assert e.value.code == "clock_behind"
    eng.place_order("EURUSD", "sell", volume=0.1, at_ms=T0 + 100 * S)                       # same instant is fine


def test_no_fills_while_the_market_is_closed_or_the_quote_is_stale():
    from datetime import datetime, timezone
    sat = int(datetime(2025, 3, 8, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    conn = db.connect(":memory:"); db.set_state(conn, "digits:EURUSD", "5")
    db.insert_ticks(conn, [("EURUSD", sat - 1000, 1.1, 1.1001, 0, 0), ("EURUSD", T0, 1.1, 1.1001, 0, 0)])
    eng = Engine(cfg_(), conn)
    with pytest.raises(EngineError) as e:
        eng.place_order("EURUSD", "buy", volume=0.1, at_ms=sat)
    assert e.value.code == "market_closed"
    with pytest.raises(EngineError) as e:
        eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0 + 600 * S)                    # last quote is 10 minutes old
    assert e.value.code == "no_quote"


# ============================================================================ no lookahead
def test_nothing_after_the_clock_is_ever_used():
    path = eur([(0, 1.1000), (10 * S, 1.1000)])
    poison = [(10 * S + 1, 5.0, 5.0001), (20 * S, 0.01, 0.0101)]                           # absurd prices after the order time
    eng, conn = make({"EURUSD": path + poison})
    eng.place_order("EURUSD", "buy", volume=0.1, sl=1.0900, tp=1.1100, at_ms=T0 + 10 * S)
    eng.advance_to(T0 + 10 * S)                                                              # the clock is exactly at the order
    p = eng.positions_view()[0]
    assert p["open_price"] == pytest.approx(1.1001) and p["price"] == pytest.approx(1.1000) and eng.history() == []
    assert eng.account()["equity"] == pytest.approx(10000 + (1.1000 - 1.1001) * 0.1 * 100000)   # marked on the visible quote only
    eng.advance_to(T0 + 10 * S + 1)                                                          # the +1 ms tick (bid 5.0) is now legitimately past
    assert eng.history()[0]["reason"] == "tp" and eng.history()[0]["close_ms"] == T0 + 10 * S + 1


# ============================================================================ persistence
def test_state_survives_a_restart_and_the_run_is_identical_to_an_uninterrupted_one():
    import random
    rng = random.Random(3)
    path, bid = [], 1.1000
    for i in range(400):
        bid = round(bid + rng.gauss(0, 2.5) * PIP, 5)
        path.append((i * 5 * S, bid))
    ticks = {"EURUSD": eur(path), "GBPUSD": eur([(dt, round(b + 0.17, 5)) for dt, b in path])}
    cfg = cfg_(costs__extra_slippage_pips=0.2, costs__commission_per_lot_round_trip=7.0)

    def drive(eng, lo, hi):
        for k in range(lo, hi):
            t = T0 + k * 25 * S
            eng.advance_to(t)
            if k % 3 == 0:
                side = "buy" if rng2.random() < .5 else "sell"
                sym = "EURUSD" if rng2.random() < .5 else "GBPUSD"
                px = {"EURUSD": path, "GBPUSD": path}[sym]
                ref = [b for dt, b in path if dt <= t - T0][-1] + (0.17 if sym == "GBPUSD" else 0)
                kw = {"sl": round(ref - 6 * PIP, 5), "tp": round(ref + 9 * PIP, 5)} if side == "buy" else {"sl": round(ref + 8 * PIP, 5), "tp": round(ref - 7 * PIP, 5)}
                eng.place_order(sym, side, volume=0.5, at_ms=t, **kw)
            if k % 5 == 0 and eng.positions_view():
                eng.close(eng.positions_view()[0]["id"], 0.2, at_ms=t)
        eng.advance_to(T0 + hi * 25 * S)

    rng2 = random.Random(9)
    whole, _ = make(ticks, cfg)
    drive(whole, 1, 70)
    rng2 = random.Random(9)
    part, conn = make(ticks, cfg)
    drive(part, 1, 35)
    restarted = Engine(cfg, conn)                                                           # "server restart": new object, same database
    assert restarted.account()["balance"] == part.account()["balance"] and restarted.positions_view() == part.positions_view()
    drive(restarted, 35, 70)
    for name in ("account", "positions_view", "orders_view", "history", "equity_curve"):
        a, b = getattr(whole, name)(), getattr(restarted, name)()
        if name == "account":
            a.pop("delay_s", None); b.pop("delay_s", None)
        assert a == b, name
    assert whole.history() and len(whole.history()) > 5


def test_failed_operations_leave_no_trace_in_memory_or_database():
    eng, conn = make({"EURUSD": flat(n=10)})
    before = (eng.account(), eng.positions_view(), conn.execute("SELECT COUNT(*) FROM acct_orders").fetchone())
    with pytest.raises(EngineError):
        eng.place_order("EURUSD", "buy", volume=100.0, at_ms=T0)
    with pytest.raises(EngineError):
        eng.place_order("EURUSD", "buy", volume=0.1, sl=1.2, at_ms=T0)
    assert (eng.account(), eng.positions_view(), conn.execute("SELECT COUNT(*) FROM acct_orders").fetchone()) == before
    assert eng.next_id == 1


# ============================================================================ tags, equity curve, reset
def test_trades_are_tagged_with_source_and_clean_or_peeked():
    eng, _ = make({"EURUSD": flat(n=10)})
    a = eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0, source="manual", peeked=False, session_id="s1")
    b = eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0, source="bot", peeked=True, session_id="s2", comment="x")
    eng.close_all(at_ms=T0 + 10 * S)
    deals = {d["position_id"]: d for d in eng.history()}
    assert (deals[a["id"]]["source"], deals[a["id"]]["peeked"]) == ("manual", 0)
    assert (deals[b["id"]]["source"], deals[b["id"]]["peeked"]) == ("bot", 1)


def test_equity_curve_drawdown_and_reset():
    path = eur([(i * 30 * S, 1.1000 + (0.0005 if i < 4 else -0.0010) * (1 if i else 0)) for i in range(12)])
    eng, _ = make({"EURUSD": path})
    eng.place_order("EURUSD", "buy", volume=1.0, at_ms=T0)
    eng.advance_to(T0 + 330 * S)
    curve = eng.equity_curve()
    assert len(curve) >= 3 and curve[0]["t_ms"] <= curve[-1]["t_ms"]
    a = eng.account()
    assert a["peak_equity"] > a["equity"] and a["drawdown_pct"] > 0
    assert max(p["equity"] for p in curve) == pytest.approx(a["peak_equity"], rel=1e-3) or a["peak_equity"] >= max(p["equity"] for p in curve)
    r = eng.reset(balance=5000)
    assert r["balance"] == 5000 and r["positions"] == 0 and r["t_engine_ms"] == 0 and eng.history() == [] and eng.equity_curve() == []
    eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0 + 10 * S)                          # an earlier chart time is fine after a reset
    assert eng.next_id == 2


def test_the_equity_curve_does_not_grow_with_every_heartbeat():
    eng, _ = make({"EURUSD": flat(n=200, step=10 * S)})
    eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0)
    for k in range(1, 300):                                                                  # a server advancing every second for 5 minutes
        eng.advance_to(T0 + k * S)
    assert len(eng.equity_curve()) <= 8
