"""The engine on realistic market data, checked against two independent implementations:
  1. a slow tick-by-tick reference (plain SQL + loops, written separately from the engine), and
  2. the Phase 3 grader (a different module that also computes "what a trade would have returned")."""
import random
from datetime import date

import numpy as np
import pytest

from collector.market_model import DAY_MS, MarketModel, SPECS, seq_within_ms
from core import db
from core.config import load_config
from core.pips import pip_size
from engine.account import Engine
from grader.grader import grade

D = lambda y, m, d: (date(y, m, d) - date(1970, 1, 1)).days
DAYS = [D(2026, 3, 4), D(2026, 3, 5)]                       # Wed + Thu: two full trading days
SYMS = list(SPECS)


def cfg_(slip=0.15, comm=7.0):
    c = load_config()
    c.symbols = SYMS
    c.costs.extra_slippage_pips = slip
    c.costs.commission_per_lot_round_trip = comm
    c.engine.starting_balance = 1e7                         # no margin interference: these tests are about P&L
    c.engine.leverage = 500
    return c


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    path = tmp_path_factory.mktemp("mkt") / "ticks.db"
    conn = db.connect(path)
    model = MarketModel(11)
    for d in DAYS:
        for sym in SYMS:
            t, b, a = model.day_ticks(sym, d)
            db.insert_ticks(conn, list(zip([sym] * len(t), t.tolist(), b.tolist(), a.tolist(), [0] * len(t), seq_within_ms(t).tolist())))
    for sym in SYMS:
        db.set_state(conn, f"digits:{sym}", str(SPECS[sym].digits))
    conn.close()
    return path


@pytest.fixture
def conn(market):
    return db.connect(market)


from tools.engine_reference import (compare, mid, newest, random_trades, reference_trade, run_engine as _run_engine,
                                    usd_per_quote)


def random_trades_for_tests(conn, cfg, n, seed):
    return random_trades(conn, cfg, SYMS, DAYS[0] * DAY_MS + 8 * 3_600_000, DAYS[0] * DAY_MS + 2 * DAY_MS - 3 * 3_600_000 + 40 * 60_000, n, seed)


def run_engine(cfg, conn_path, trades):
    return _run_engine(cfg, db.connect(conn_path), trades)


# ================================================================================== engine vs reference
@pytest.mark.parametrize("slip,comm,n", [(0.0, 0.0, 330), (0.15, 7.0, 220)])
def test_engine_matches_the_independent_reference_on_hundreds_of_random_trades(market, conn, slip, comm, n):
    cfg = cfg_(slip, comm)
    trades = random_trades_for_tests(conn, cfg, n, 5)
    assert len(trades) > 0.75 * n
    eng, pos = run_engine(cfg, market, trades)
    r = compare(eng, pos, conn, cfg, trades)
    assert r["mismatches"] == [], r["mismatches"][:3]
    assert all(v > 8 for v in r["exit_reasons"].values()), r["exit_reasons"]            # every exit path was really exercised
    assert r["balance_matches_reference"] and r["open_positions_left"] == 0 and r["worst_pips_error"] < 1e-6
    assert eng.account()["deals"] == len(trades)


def test_a_buy_and_hold_loses_exactly_the_spread_plus_costs_on_a_martingale_price(market, conn):
    """Sanity on the whole stack: with no edge in the price, round trips cost the spread (+ slippage + commission)."""
    cfg = cfg_(0.15, 7.0)
    rng = random.Random(1)
    trades = []
    for i in range(300):
        sym = "EURUSD"
        at = DAYS[0] * DAY_MS + rng.randrange(9 * 3_600_000, 2 * DAY_MS - 4 * 3_600_000)
        if conn.execute("SELECT 1 FROM ticks WHERE symbol=? AND t_ms BETWEEN ? AND ? LIMIT 1", (sym, at - 30_000, at)).fetchone():
            trades.append(dict(i=i, sym=sym, side="buy" if i % 2 == 0 else "sell", vol=1.0, at=at, close_at=at + 300_000, sl=None, tp=None))
    eng, _ = run_engine(cfg, market, trades)
    d = eng.history(10_000)
    net_pips = np.array([x["net_pips"] for x in d])
    spread = np.mean([(newest(conn, "EURUSD", t["at"])[3] - newest(conn, "EURUSD", t["at"])[2]) / 1e-4 for t in trades])
    # expected mean net pips = -(avg spread + 2 x 0.15 slippage + 0.7 commission) with a sampling error of ~ sd/sqrt(n)
    expected = -(spread + 0.3 + 0.7)
    se = net_pips.std() / np.sqrt(len(net_pips))
    assert abs(net_pips.mean() - expected) < 3.5 * se, (net_pips.mean(), expected, se)
    assert net_pips.mean() < 0


# ================================================================================== engine vs the Phase 3 grader
@pytest.mark.parametrize("sym,comm", [("EURUSD", 7.0), ("GBPUSD", 7.0), ("EURJPY", 0.0), ("GBPJPY", 0.0)])
def test_engine_reproduces_the_graders_net_pips(market, conn, sym, comm):
    """Open at the grader's entry tick, close at fill + h: the engine's net pips must equal the label's."""
    cfg = cfg_(0.2, comm)
    eng = Engine(cfg, db.connect(market))
    rng = random.Random(7)
    checked = 0
    for _ in range(400):
        entry = DAYS[0] * DAY_MS + rng.randrange(9 * 3_600_000, 2 * DAY_MS - 3 * 3_600_000)
        direction = rng.choice(["buy", "sell"])
        h = rng.choice([30, 60, 120, 300])
        first = conn.execute("SELECT t_ms, seq FROM ticks WHERE symbol=? AND t_ms>=? ORDER BY t_ms, seq LIMIT 2", (sym, entry)).fetchall()
        if len(first) < 2 or first[0][0] == first[1][0] or first[0][0] - entry > 5000:
            continue                                                                  # need a unique tick at the fill time
        lab = grade(conn, sym, entry, direction, cfg)
        if lab is None or lab.status != "final":
            continue
        t_e = first[0][0]
        eng.reset(balance=1e7, leverage=500)
        r = eng.place_order(sym, direction, volume=1.0, at_ms=t_e)
        d = eng.close(r["id"], at_ms=t_e + h * 1000)["deal"]
        want = getattr(lab, f"pnl_{h}s")
        slip = cfg.costs.extra_slippage_pips * pip_size(SPECS[sym].digits)             # the label keeps the raw fill tick;
        assert d["open_price"] == pytest.approx(lab.entry_price + (slip if direction == "buy" else -slip), abs=1e-9)   # the engine adds slippage
        if sym.endswith("USD") or comm == 0:
            assert d["net_pips"] == pytest.approx(want, abs=1e-6), (entry, direction, h)
        checked += 1
        if checked >= 60:
            break
    assert checked >= 40, f"only {checked} comparable entries"
