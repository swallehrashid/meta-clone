"""The bot contract, the runner (risk limits, error handling), no-lookahead, and the example bots."""
import hashlib
import shutil
from datetime import date

import pytest

from bot.interface import (UNSET, Bot, CancelOrder, Close, CloseAll, Context, GuardedView, Modify, Open, ParamSpec,
                           stable_random)
from bot.runner import BotRunner, RiskLimits, decision_times, run_replay
from bot.strategies import REGISTRY, make_bot
from bot.strategies.momentum import MomentumBreakoutBot
from bot.strategies.random_entry import RandomEntryBot
from collector.market_model import DAY_MS, MarketModel, SPECS, seq_within_ms
from core import db
from core.config import load_config
from core.dataview import DataView, LookaheadError
from engine.account import Engine
from tests.conftest import T0
from tools.sandbox import open_sandbox

S = 1000
PIP = 1e-4
D = lambda y, m, d: (date(y, m, d) - date(1970, 1, 1)).days


def cfg_(**over):
    c = load_config()
    c.symbols = ["EURUSD", "GBPUSD"]
    for k, v in over.items():
        sect, key = k.split("__")
        setattr(getattr(c, sect), key, v)
    return c


def hand(ticks, cfg=None):
    """ticks: {symbol: [(dt_ms, bid, ask)]} -> (engine, conn) with the engine at clock 0."""
    conn = db.connect(":memory:")
    rows = []
    for sym, lst in ticks.items():
        db.set_state(conn, f"digits:{sym}", "5")
        seq, prev = 0, None
        for dt, bid, ask in lst:
            seq = seq + 1 if dt == prev else 0
            prev = dt
            rows.append((sym, T0 + dt, bid, ask, 0, seq))
    db.insert_ticks(conn, rows)
    return Engine(cfg or cfg_(), conn), conn


def flat(bid=1.1000, spread=1.0, n=400, step=5 * S):
    return [(i * step, bid, round(bid + spread * PIP, 5)) for i in range(n)]


class Scripted(Bot):
    """A bot that does what a dict says, keyed by decision time (offset from T0)."""
    name = "scripted"
    PARAMS = {}

    def __init__(self, script=None, **kw):
        super().__init__(**kw)
        self.script = script or {}

    def decide(self, ctx):
        r = self.script.get(ctx.t_ms - T0, [])
        return r(ctx) if callable(r) else r


def run_steps(eng, runner, offsets):
    for off in offsets:
        eng.advance_to(T0 + off)
        runner.step(T0 + off)


# ============================================================================== parameters
def test_params_are_validated_and_coerced():
    b = RandomEntryBot(p_enter="0.5", hold_s=90, side="buy")
    assert b.params["p_enter"] == 0.5 and b.params["hold_s"] == 90 and b.params["volume"] == 0.1
    for bad in ({"p_enter": 2}, {"p_enter": "x"}, {"hold_s": 1.5}, {"hold_s": 5}, {"side": "up"}, {"nope": 1}, {"volume": 0}):
        with pytest.raises(ValueError):
            RandomEntryBot(**bad)
    assert MomentumBreakoutBot(reverse="true").params["reverse"] is True
    assert set(REGISTRY) == {"random_entry", "momentum_breakout", "forward_teacher", "learned"}
    with pytest.raises(ValueError):
        make_bot("nope")
    d = RandomEntryBot.describe()
    assert d["name"] == "random_entry" and d["params"]["p_enter"]["max"] == 1.0 and d["interval_ms"] == 60_000


def test_stable_random_is_a_pure_function_of_its_inputs():
    a = [stable_random(1, "EURUSD", t).random() for t in range(0, 600_000, 60_000)]
    assert a == [stable_random(1, "EURUSD", t).random() for t in range(0, 600_000, 60_000)]
    assert a != [stable_random(2, "EURUSD", t).random() for t in range(0, 600_000, 60_000)]
    assert stable_random(1, "EURUSD", 5).random() != stable_random(1, "GBPUSD", 5).random()


# ============================================================================== what a bot can see
def test_a_bot_gets_guarded_read_only_views_that_cannot_look_ahead():
    eng, conn = hand({"EURUSD": flat()})
    seen = {}

    class Probe(Bot):
        name = "probe"
        PARAMS = {}

        def decide(self, ctx):
            v = ctx.view("EURUSD")
            seen["is_guarded"] = isinstance(v, GuardedView)
            seen["cutoff"] = v.cutoff_ms
            seen["no_conn"] = not any(hasattr(v, a) for a in ("conn", "_conn", "execute", "cursor"))
            try:
                v.ticks(ctx.t_ms, ctx.t_ms + 60_000)
            except LookaheadError:
                seen["blocked"] = True
            try:
                v.cutoff_ms = ctx.t_ms + 10**9
            except AttributeError:
                seen["readonly"] = True
            for attack in ("_dv", "cutoff_ms", "newattr"):                   # rebinding the wrapped view would widen the cutoff
                try:
                    setattr(v, attack, object())
                except AttributeError:
                    seen.setdefault("blocked_attrs", []).append(attack)
            seen["last"] = v.last_tick()
            seen["quote"] = ctx.quote("EURUSD")
            return []

    r = BotRunner(cfg_(), eng, Probe(), ["EURUSD"])
    run_steps(eng, r, [60 * S])
    assert seen["is_guarded"] and seen["no_conn"] and seen["blocked"] and seen["readonly"] and seen["cutoff"] == T0 + 60 * S
    assert seen["blocked_attrs"] == ["_dv", "cutoff_ms", "newattr"]
    assert seen["last"][0] <= T0 + 60 * S and seen["quote"].t_ms == seen["last"][0]
    assert seen["quote"].spread_pips == pytest.approx(1.0)
    assert r.stats.errors == 0


def test_a_bot_that_tries_to_read_the_future_gets_an_error_not_a_trade():
    eng, _ = hand({"EURUSD": flat()})

    class Cheat(Bot):
        name = "cheat"
        PARAMS = {}

        def decide(self, ctx):
            future = ctx.view("EURUSD").ticks(ctx.t_ms, ctx.t_ms + 120_000)      # raises LookaheadError
            return [Open("EURUSD", "buy", 0.1)]

    r = BotRunner(cfg_(), eng, Cheat(), ["EURUSD"])
    run_steps(eng, r, [60 * S, 120 * S])
    assert eng.positions_view() == [] and r.stats.errors == 2
    assert all(e.kind == "error" and "LookaheadError" in e.detail for e in r.events)


def test_the_bot_sees_only_its_own_positions():
    eng, _ = hand({"EURUSD": flat(), "GBPUSD": [(i * 5 * S, 1.27, 1.2701) for i in range(400)]})
    eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0 + 30 * S, source="manual")             # a human trade
    got = {}

    class Look(Bot):
        name = "look"
        PARAMS = {}

        def decide(self, ctx):
            got.setdefault(ctx.t_ms - T0, (len(ctx.positions), ctx.account.balance, len(ctx.my_positions("EURUSD"))))
            return [Open("EURUSD", "sell", 0.1)] if ctx.t_ms - T0 == 60 * S else []

    r = BotRunner(cfg_(), eng, Look(), ["EURUSD", "GBPUSD"])
    run_steps(eng, r, [60 * S, 120 * S])
    assert got[60 * S][0] == 0 and got[120 * S][0] == 1                                          # the human's buy is invisible; its own sell is not
    assert [p["source"] for p in eng.positions_view()] == ["manual", "bot"]


# ============================================================================== actions
def test_every_action_type_reaches_the_engine_tagged_as_a_bot_trade():
    eng, _ = hand({"EURUSD": flat(n=600)})
    script = {
        60 * S: [Open("EURUSD", "buy", 0.2, sl_pips=10, tp_pips=20, comment="one")],
        65 * S: [Open("EURUSD", "buy", 0.1, type="limit", price=1.0900)],
        120 * S: lambda ctx: [Modify(ctx.positions[0].id, sl=1.0950, tp=None), CancelOrder(ctx.orders[0].id)],
        180 * S: lambda ctx: [Close(ctx.positions[0].id, 0.1)],
        240 * S: [CloseAll()],
    }
    r = BotRunner(cfg_(), eng, Scripted(script), ["EURUSD"])
    run_steps(eng, r, [60 * S])
    p = eng.positions_view()[0]
    assert p["source"] == "bot" and p["peeked"] == 0 and p["volume"] == 0.2
    assert p["sl"] == pytest.approx(1.1000 - 10 * PIP) and p["tp"] == pytest.approx(1.1000 + 20 * PIP)    # pips are from the exit-side BID
    run_steps(eng, r, [65 * S])
    assert len(eng.orders_view()) == 1 and eng.orders_view()[0]["source"] == "bot"
    run_steps(eng, r, [120 * S])
    p = eng.positions_view()[0]
    assert (p["sl"], p["tp"]) == (1.0950, None) and eng.orders_view() == []
    run_steps(eng, r, [180 * S])
    assert eng.positions_view()[0]["volume"] == pytest.approx(0.1) and eng.history()[0]["volume"] == 0.1
    run_steps(eng, r, [240 * S])
    assert eng.positions_view() == []
    assert {d["source"] for d in eng.history()} == {"bot"}
    assert [e.kind for e in r.events] == ["open", "open", "modify", "cancel", "close", "close"]


def test_sell_levels_in_pips_and_giving_both_price_and_pips_is_refused():
    eng, _ = hand({"EURUSD": flat(n=100)})
    r = BotRunner(cfg_(), eng, Scripted({60 * S: [Open("EURUSD", "sell", 0.1, sl_pips=8, tp_pips=12),
                                                   Open("EURUSD", "sell", 0.1, sl=1.2, sl_pips=5)]}), ["EURUSD"])
    run_steps(eng, r, [60 * S])
    p = eng.positions_view()[0]
    assert p["sl"] == pytest.approx(1.1001 + 8 * PIP) and p["tp"] == pytest.approx(1.1001 - 12 * PIP)      # exit side of a short is the ASK
    assert len(eng.positions_view()) == 1 and r.stats.rejected == 1 and "not both" in r.events[-1].detail


def test_a_bot_cannot_touch_positions_it_does_not_own():
    eng, _ = hand({"EURUSD": flat(n=100)})
    pid = eng.place_order("EURUSD", "buy", volume=0.1, at_ms=T0 + 10 * S, source="manual")["id"]
    r = BotRunner(cfg_(), eng, Scripted({60 * S: [Close(pid), Modify(pid, sl=1.05), CancelOrder(999)]}), ["EURUSD"])
    run_steps(eng, r, [60 * S])
    assert len(eng.positions_view()) == 1 and eng.positions_view()[0]["sl"] is None
    assert r.stats.rejected == 3 and "not one of this bot's" in r.events[0].detail


def test_garbage_actions_and_unknown_symbols_are_refused_not_crashed():
    eng, _ = hand({"EURUSD": flat(n=100)})
    r = BotRunner(cfg_(), eng, Scripted({60 * S: ["buy", 42, None, Open("GBPUSD", "buy", 0.1), Open("EURUSD", "buy", 0.0)]}), ["EURUSD"])
    run_steps(eng, r, [60 * S])
    assert eng.positions_view() == [] and r.stats.rejected == 5 and r.stats.errors == 0


# ============================================================================== risk limits and the kill switch
def test_risk_limits_are_enforced_outside_the_bot():
    eng, _ = hand({"EURUSD": flat(n=600)})
    lim = RiskLimits(max_open_positions=2, max_volume_per_order=0.5, max_total_volume=0.8)
    script = {60 * S: [Open("EURUSD", "buy", 0.9),                   # too big for one order
                       Open("EURUSD", "buy", 0.5), Open("EURUSD", "sell", 0.5),     # second would push total to 1.0 > 0.8
                       Open("EURUSD", "buy", 0.3)],                  # ok: 0.8 total, 2 positions
              120 * S: [Open("EURUSD", "buy", 0.1)]}                 # a third position
    r = BotRunner(cfg_(), eng, Scripted(script), ["EURUSD"], lim)
    run_steps(eng, r, [60 * S, 120 * S])
    assert sorted(p["volume"] for p in eng.positions_view()) == [0.3, 0.5]
    why = [e.detail for e in r.events if e.kind == "reject"]
    assert any("per-order limit" in w for w in why) and any("total open volume" in w for w in why) and any("open-position limit" in w for w in why)


def test_order_rate_limit():
    eng, _ = hand({"EURUSD": flat(n=900)})
    r = BotRunner(cfg_(), eng, Scripted({k * 60 * S: [Open("EURUSD", "buy", 0.01), CloseAll()] for k in range(1, 8)}), ["EURUSD"],
                  RiskLimits(max_orders_per_hour=3))
    run_steps(eng, r, [k * 60 * S for k in range(1, 8)])
    assert r.stats.opened == 3 and sum("rate limit" in e.detail for e in r.events) == 4


def test_daily_loss_limit_closes_everything_and_blocks_new_trades():
    path = [(i * 5 * S, round(1.1000 - i * 0.00004, 5), round(1.1001 - i * 0.00004, 5)) for i in range(400)]    # falls 0.4 pips / tick
    eng, _ = hand({"EURUSD": path})
    script = {k * 60 * S: [Open("EURUSD", "buy", 1.0)] for k in range(1, 7)}
    r = BotRunner(cfg_(), eng, Scripted(script), ["EURUSD"], RiskLimits(max_daily_loss_usd=100.0))
    run_steps(eng, r, [k * 60 * S for k in range(1, 7)])
    assert eng.positions_view() == []                                                         # flattened when the day's loss passed $100
    msgs = [e.detail for e in r.events]
    assert any("daily loss limit reached: closing everything" in m for m in msgs)
    n_opened = r.stats.opened
    run_steps(eng, r, [420 * S])
    assert r.stats.opened == n_opened                                                         # and still blocked afterwards
    assert any("daily loss limit" in m for m in msgs) and eng.account()["realized"] < -90


def test_a_bot_that_keeps_crashing_is_halted_and_stays_halted():
    eng, _ = hand({"EURUSD": flat(n=200)})

    class Boom(Bot):
        name = "boom"
        PARAMS = {}

        def decide(self, ctx):
            raise RuntimeError("bug")

    r = BotRunner(cfg_(), eng, Boom(), ["EURUSD"], RiskLimits(max_consecutive_errors=3))
    run_steps(eng, r, [k * 60 * S for k in range(1, 8)])
    assert r.stats.halted and r.stats.errors == 3 and r.stats.steps == 3 and "3 errors" in r.stats.halt_reason
    assert [e.kind for e in r.events][-1] == "halt" and r.step(T0 + 600 * S) == []             # halted: no further decisions


def test_an_occasional_error_does_not_halt_a_bot():
    eng, _ = hand({"EURUSD": flat(n=200)})
    calls = {"n": 0}

    class Flaky(Bot):
        name = "flaky"
        PARAMS = {}

        def decide(self, ctx):
            calls["n"] += 1
            if calls["n"] % 3 == 0:
                raise ValueError("sometimes")
            return []

    r = BotRunner(cfg_(), eng, Flaky(), ["EURUSD"], RiskLimits(max_consecutive_errors=2))
    run_steps(eng, r, [k * 60 * S for k in range(1, 10)])
    assert r.stats.errors == 3 and not r.stats.halted


def test_too_many_refusals_in_a_row_halt_the_bot():
    eng, _ = hand({"EURUSD": flat(n=200)})
    r = BotRunner(cfg_(), eng, Scripted({k * 60 * S: [Open("EURUSD", "buy", 0.0)] * 3 for k in range(1, 6)}), ["EURUSD"],      # volume 0: the ENGINE refuses
                  RiskLimits(max_rejections_in_a_row=4))
    run_steps(eng, r, [k * 60 * S for k in range(1, 6)])
    assert r.stats.halted and "refused orders in a row" in r.stats.halt_reason


def test_hitting_a_risk_limit_is_not_a_malfunction_and_does_not_halt_the_bot():
    """A bot that keeps asking for more than the limits allow is refused each time - but it is the limits working, so no kill switch."""
    eng, _ = hand({"EURUSD": flat(n=900)})
    script = {k * 60 * S: [Open("EURUSD", "buy", 0.1)] * 3 for k in range(1, 30)}
    r = BotRunner(cfg_(), eng, Scripted(script), ["EURUSD"], RiskLimits(max_open_positions=1, max_rejections_in_a_row=4))
    run_steps(eng, r, [k * 60 * S for k in range(1, 30)])
    assert r.stats.rejected > 50 and not r.stats.halted and len(eng.positions_view()) == 1
    # ...whereas genuine engine refusals (a nonsense order) do trip it
    eng2, _ = hand({"EURUSD": flat(n=200)})
    r2 = BotRunner(cfg_(), eng2, Scripted({k * 60 * S: [Open("EURUSD", "buy", 0.1, type="limit", price=9.0)] for k in range(1, 8)}), ["EURUSD"],
                   RiskLimits(max_rejections_in_a_row=4))
    run_steps(eng2, r2, [k * 60 * S for k in range(1, 8)])
    assert r2.stats.halted


def test_risk_limits_parse_and_reject_unknown_names():
    assert RiskLimits.from_dict({"max_open_positions": "2", "max_daily_loss_usd": 50}).max_open_positions == 2
    with pytest.raises(ValueError):
        RiskLimits.from_dict({"max_leverage": 5})


# ============================================================================== market-based fixtures
@pytest.fixture(scope="module")
def mkt(tmp_path_factory):
    path = tmp_path_factory.mktemp("botmkt") / "ticks.db"
    conn = db.connect(path)
    model = MarketModel(31)
    for d in (D(2026, 3, 4), D(2026, 3, 5)):
        for sym in ("EURUSD", "GBPUSD"):
            t, b, a = model.day_ticks(sym, d)
            db.insert_ticks(conn, list(zip([sym] * len(t), t.tolist(), b.tolist(), a.tolist(), [0] * len(t), seq_within_ms(t).tolist())))
    for sym in ("EURUSD", "GBPUSD"):
        db.set_state(conn, f"digits:{sym}", str(SPECS[sym].digits))
    conn.close()
    return path


LO, HI = D(2026, 3, 4) * DAY_MS + 13 * 3_600_000, D(2026, 3, 4) * DAY_MS + 17 * 3_600_000      # 08:00-12:00 New York


def replay(path, bot, lo=LO, hi=HI, **kw):
    return run_replay(cfg_(), open_sandbox(str(path)), bot, ["EURUSD", "GBPUSD"], lo, hi, **kw)


# ============================================================================== no lookahead
def test_poisoning_the_future_changes_not_one_decision(mkt, tmp_path):
    """Two databases identical up to T; the second has absurd ticks AFTER T. Replaying up to T must give the same
    trades, the same events and the same account - for both example bots."""
    T = LO + 2 * 3_600_000
    poisoned = tmp_path / "poisoned.db"
    shutil.copy(mkt, poisoned)
    c = db.connect(poisoned)
    rows = []
    for sym in ("EURUSD", "GBPUSD"):
        for k in range(1, 400):
            rows.append((sym, T + k * 1500, 9.0 + k, 9.0002 + k, 0, 0))
    db.insert_ticks(c, rows)
    c.close()
    for bot in (RandomEntryBot(p_enter=0.3, hold_s=300, seed=4), MomentumBreakoutBot(hold_s=180, sl_pips=3, tp_pips=4, max_spread_pips=3)):
        a = replay(mkt, bot, LO, T)
        b = replay(poisoned, bot, LO, T)
        assert len(a.deals) > 3, bot.name
        strip = lambda r: ([(d["symbol"], d["side"], d["volume"], d["open_ms"], d["close_ms"], d["open_price"], d["close_price"], d["reason"], d["net"]) for d in r.deals],
                           [(e.t_ms, e.kind, e.detail) for e in r.events], r.account["balance"])
        assert strip(a) == strip(b), bot.name


def test_a_recording_bot_sees_identical_data_with_and_without_the_future(mkt, tmp_path):
    T = LO + 3_600_000
    poisoned = tmp_path / "p2.db"
    shutil.copy(mkt, poisoned)
    c = db.connect(poisoned)
    db.insert_ticks(c, [("EURUSD", T + k * 700, 5.0, 5.0001, 0, 0) for k in range(1, 300)])
    c.close()

    def record(path):
        digests = []

        class Rec(Bot):
            name = "rec"
            PARAMS = {}

            def decide(self, ctx):
                v = ctx.view("EURUSD")
                h = hashlib.sha1(repr((ctx.t_ms, v.last_tick(), v.candles("M1", 30), v.count_ticks(ctx.t_ms - 60_000), ctx.features("EURUSD"),
                                       v.bid_extremes(ctx.t_ms - 900_000))).encode()).hexdigest()
                digests.append(h)
                return []
        replay(path, Rec(), LO, T)
        return digests

    a, b = record(mkt), record(poisoned)
    assert len(a) > 50 and a == b


def test_the_replay_never_modifies_the_history_file(mkt):
    import os
    import sqlite3
    before = (os.path.getsize(mkt), os.path.getmtime(mkt))
    r = replay(mkt, RandomEntryBot(p_enter=0.5, seed=2), LO, LO + 3_600_000)
    assert len(r.deals) > 5                                                     # it really traded
    assert (os.path.getsize(mkt), os.path.getmtime(mkt)) == before              # ...without touching the file
    raw = sqlite3.connect(f"file:{mkt}?mode=ro", uri=True)
    assert raw.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'acct_%' OR name LIKE 'bot_%'").fetchone()[0] == 0   # no account/bot tables leaked in


def test_replay_is_deterministic(mkt):
    bot = RandomEntryBot(p_enter=0.3, seed=9)
    a, b = replay(mkt, bot), replay(mkt, bot)
    assert [(d["open_ms"], d["close_ms"], d["net"]) for d in a.deals] == [(d["open_ms"], d["close_ms"], d["net"]) for d in b.deals] and len(a.deals) > 20


def test_decision_times_skip_closed_markets():
    from datetime import datetime, timezone
    fri = int(datetime(2026, 3, 6, 21, 50, tzinfo=timezone.utc).timestamp() * 1000)        # Friday 16:50 New York (EST)
    ts = list(decision_times(fri, fri + 3 * 3_600_000, 60_000))
    assert ts[0] == fri and ts[-1] == fri + 9 * 60_000 and len(ts) == 10                     # market shuts at 17:00 NY = 22:00 UTC


# ============================================================================== the example bots
def test_random_bot_frequency_direction_hold_and_one_position_per_symbol(mkt):
    bot = RandomEntryBot(p_enter=0.1, hold_s=120, seed=5)
    r = replay(mkt, bot)
    steps = r.stats.steps
    n = len(r.deals)
    assert steps > 200 and n > 40
    assert 0.35 < sum(d["side"] == "buy" for d in r.deals) / n < 0.65
    assert all(110 <= d["duration_s"] <= 190 for d in r.deals)                                 # closed on the first decision after hold_s
    for sym in ("EURUSD", "GBPUSD"):                                                          # never two at once per symbol
        iv = sorted((d["open_ms"], d["close_ms"]) for d in r.deals if d["symbol"] == sym)
        assert all(b[0] >= a[1] for a, b in zip(iv, iv[1:]))
    assert all(d["source"] == "bot" and d["peeked"] == 0 for d in r.deals)
    other = replay(mkt, RandomEntryBot(p_enter=0.1, hold_s=120, seed=6))
    assert [d["open_ms"] for d in other.deals] != [d["open_ms"] for d in r.deals]


def test_random_bot_spread_filter_is_respected(mkt):
    wide = replay(mkt, RandomEntryBot(p_enter=0.3, seed=1))
    tight = replay(mkt, RandomEntryBot(p_enter=0.3, seed=1, max_spread_pips=0.7))
    assert len(tight.deals) < len(wide.deals)
    c = db.connect(mkt)
    for d in tight.deals:
        t = c.execute("SELECT bid, ask FROM ticks WHERE symbol=? AND t_ms<=? ORDER BY t_ms DESC, seq DESC LIMIT 1", (d["symbol"], d["open_ms"])).fetchone()
        assert (t[1] - t[0]) / PIP <= 0.7 + 1e-6 if d["symbol"].endswith("USD") else True


def test_momentum_bot_follows_and_fades_a_breakout_on_hand_made_data():
    # three minutes: 14:00 range 1.1000-1.1010 ; 14:01 closes at 1.1020 (above the previous high) -> BUY at the start of 14:02
    def minute(start, o, h, l, c):
        return [(start + 1000, o, o + 0.0001), (start + 20_000, h, h + 0.0001), (start + 40_000, l, l + 0.0001), (start + 58_000, c, c + 0.0001)]
    ticks = minute(0, 1.1000, 1.1010, 1.0995, 1.1005) + minute(60_000, 1.1005, 1.1022, 1.1004, 1.1020) + [(2 * 60_000 + 3000, 1.1020, 1.1021)]
    for rev, side in ((False, "buy"), (True, "sell")):
        eng, _ = hand({"EURUSD": ticks})
        bot = MomentumBreakoutBot(max_spread_pips=2.0, min_range_pips=3, reverse=rev, sl_pips=0, tp_pips=0)
        r = BotRunner(cfg_(), eng, bot, ["EURUSD"])
        run_steps(eng, r, [2 * 60 * S + 4 * S])
        assert [p["side"] for p in eng.positions_view()] == [side], rev
    eng, _ = hand({"EURUSD": [(a, b, round(b + 0.0005, 5)) for a, b, _ in ticks]})                # spread 5 pips: filtered out
    r = BotRunner(cfg_(), eng, MomentumBreakoutBot(max_spread_pips=1.5), ["EURUSD"])
    run_steps(eng, r, [2 * 60 * S + 4 * S])
    assert eng.positions_view() == []
    # and it exits by the timer
    eng, _ = hand({"EURUSD": ticks + [(5 * 60_000 + i * 5000, 1.1021, 1.1022) for i in range(30)]})
    r = BotRunner(cfg_(), eng, MomentumBreakoutBot(max_spread_pips=2.0, hold_s=120, sl_pips=0, tp_pips=0), ["EURUSD"])
    run_steps(eng, r, [2 * 60 * S + 4 * S, 4 * 60 * S + 4 * S, 6 * 60 * S + 4 * S])
    assert eng.positions_view() == [] and eng.history()[0]["reason"] == "manual"
