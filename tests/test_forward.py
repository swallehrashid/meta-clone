"""Forward data: opt-in, isolated, tagged. The teacher, the learner and the training pipeline built on it."""
import numpy as np
import pytest

from bot.interface import Close, ForwardView, Open, ParamSpec, Quote
from bot.model import LinearModel, fit_logistic, sigmoid, train_linear
from bot.runner import BotRunner, RiskLimits
from bot.strategies import REGISTRY, make_bot
from bot.strategies.forward_teacher import ForwardTeacherBot, teacher_signal
from bot.strategies.learned import LearnedBot
from bot.train import Dataset, auc, build_dataset, day_range, fit
from core import db
from core.dataview import DataView, LookaheadError
from engine.account import Engine
from tests.conftest import LIVE_END, T0, make_cfg
from tools.sandbox import open_sandbox
from tests.test_bot import PIP, S, Scripted, cfg_, flat, hand

M = 60_000


def rising_then_flat():
    """EURUSD: flat 1.1000 for 2 minutes, then +1 pip every 5 s for the next minute, then flat again."""
    out = []
    for i in range(0, 400):
        t = i * 5 * S
        extra = min(max(i * 5 - 120, 0), 60) / 5               # pips gained since the 2-minute mark
        bid = round(1.1000 + extra * PIP, 5)
        out.append((t, bid, round(bid + PIP, 5)))
    return out


# ============================================================================== the view
def test_a_run_is_blind_unless_forward_is_requested():
    eng, conn = hand({"EURUSD": flat()})
    seen = []
    bot = Scripted({2 * M: lambda ctx: seen.append((ctx.forward_s, ctx.forward("EURUSD"))) or []})
    r = BotRunner(cfg_(), eng, bot, ["EURUSD"])
    eng.advance_to(T0 + 2 * M)
    r.step(T0 + 2 * M)
    assert seen == [(0, None)]


def test_forward_view_holds_only_ticks_strictly_after_the_decision_and_inside_the_window():
    eng, conn = hand({"EURUSD": rising_then_flat()})
    box = {}
    bot = Scripted({2 * M: lambda ctx: box.update(ctx=ctx) or []})
    r = BotRunner(cfg_(), eng, bot, ["EURUSD"], forward_s=60)
    eng.advance_to(T0 + 2 * M)
    r.step(T0 + 2 * M)
    ctx = box["ctx"]
    fwd = ctx.forward("EURUSD")
    ts = [row[0] for row in fwd.ticks()]
    assert ts and min(ts) > T0 + 2 * M and max(ts) <= T0 + 3 * M and fwd.complete
    assert fwd.quote_at(60)[0] == pytest.approx(1.1012)            # the market moved 12 pips inside the window
    # the normal (blind) view is still fenced at the decision time even though forward data exists
    with pytest.raises(LookaheadError):
        ctx.view("EURUSD").ticks(T0 + 2 * M, T0 + 2 * M + 10 * S)
    assert max(row[0] for row in ctx.view("EURUSD").ticks(T0, T0 + 2 * M)) <= T0 + 2 * M


def test_forward_view_next_candle_is_the_minute_that_comes_next():
    eng, conn = hand({"EURUSD": rising_then_flat()})
    box = {}
    bot = Scripted({2 * M: lambda ctx: box.update(f=ctx.forward("EURUSD")) or []})
    r = BotRunner(cfg_(), eng, bot, ["EURUSD"], forward_s=60)
    eng.advance_to(T0 + 2 * M)
    r.step(T0 + 2 * M)
    c = box["f"].next_candle("M1")
    assert c is not None and c.t_ms == T0 + 2 * M and c.c > c.o and c.l == pytest.approx(1.1001)
    assert box["f"].next_candle("M5") is None                      # a 5-minute candle does not fit in a 1-minute window


def test_forward_window_is_clamped_at_the_live_edge_and_marked_incomplete():
    eng, conn = hand({"EURUSD": rising_then_flat()})
    box = {}
    bot = Scripted({2 * M: lambda ctx: box.update(f=ctx.forward("EURUSD")) or []})
    r = BotRunner(cfg_(), eng, bot, ["EURUSD"], forward_s=60, forward_limit=lambda: T0 + 2 * M + 20 * S)
    eng.advance_to(T0 + 2 * M)
    r.step(T0 + 2 * M)
    f = box["f"]
    assert not f.complete and max(row[0] for row in f.ticks()) <= T0 + 2 * M + 20 * S


def test_forward_runs_tag_every_order_peeked_and_blind_runs_do_not():
    for fwd, expect in ((60, 1), (0, 0)):
        eng, conn = hand({"EURUSD": flat()})
        bot = Scripted({2 * M: [Open("EURUSD", "buy", 0.1)]})
        r = BotRunner(cfg_(), eng, bot, ["EURUSD"], forward_s=fwd)
        eng.advance_to(T0 + 2 * M)
        r.step(T0 + 2 * M)
        pos = eng.positions_view()
        assert len(pos) == 1 and pos[0]["peeked"] == expect and pos[0]["source"] == "bot"


def test_a_teacher_refuses_to_run_blind_and_forward_must_be_sane():
    eng, conn = hand({"EURUSD": flat()})
    with pytest.raises(ValueError, match="needs forward data"):
        BotRunner(cfg_(), eng, ForwardTeacherBot(), ["EURUSD"], forward_s=0)
    with pytest.raises(ValueError):
        BotRunner(cfg_(), eng, ForwardTeacherBot(), ["EURUSD"], forward_s=99999)
    BotRunner(cfg_(), eng, ForwardTeacherBot(), ["EURUSD"], forward_s=60)        # fine


def test_strategy_modules_register_and_describe_forward_need():
    assert {"forward_teacher", "learned"} <= set(REGISTRY)
    assert REGISTRY["forward_teacher"].describe()["needs_forward"] is True
    assert REGISTRY["random_entry"].describe()["needs_forward"] is False
    assert ParamSpec("str", "").coerce("model", "abc") == "abc"


# ============================================================================== the teacher
def _fwd(prices, complete=True, t=0, horizon=60):
    rows = [(t + (i + 1) * 5 * S, i, b, round(b + PIP, 5)) for i, b in enumerate(prices)]
    return ForwardView(rows, "bid", t, horizon, complete)


def _q(bid=1.1000):
    return Quote(0, bid, round(bid + PIP, 5), 1.0)


def test_teacher_buys_a_rise_sells_a_fall_and_stands_aside_in_a_flat_minute():
    up = [1.1000 + i * PIP for i in range(1, 13)]
    s = teacher_signal(_q(), _fwd(up), PIP, 60, 1.0, 0.8, 1.0)
    assert s["side"] == "buy" and s["net_pips"] == pytest.approx(11.0, abs=0.01)       # 12 pips up, 1 pip spread
    assert s["tp_pips"] == pytest.approx(0.8 * 11.0, abs=0.01)                          # best excursion measured from the ask
    down = [1.1000 - i * PIP for i in range(1, 13)]
    assert teacher_signal(_q(), _fwd(down), PIP, 60, 1.0, 0.8, 1.0)["side"] == "sell"
    assert teacher_signal(_q(), _fwd([1.1000] * 12), PIP, 60, 1.0, 0.8, 1.0) is None
    assert teacher_signal(_q(), _fwd(up, complete=False), PIP, 60, 1.0, 0.8, 1.0) is None
    assert teacher_signal(_q(), None, PIP, 60, 1.0, 0.8, 1.0) is None


def test_the_teacher_stop_sits_beyond_the_worst_move_so_it_is_never_hit():
    path = [1.1000 - 4 * PIP, 1.1000 - 3 * PIP] + [1.1000 + i * PIP for i in range(1, 11)]       # dips 4 pips, then rallies
    s = teacher_signal(_q(), _fwd(path), PIP, 60, 1.0, 0.8, 1.0)
    assert s["side"] == "buy" and s["sl_pips"] >= 5.0 + 1.0 - 1e-6          # worst move 5 pips from the ask (1.1001 -> 1.0996) + buffer


def test_teacher_bot_trades_with_hindsight_and_is_profitable_on_a_clean_rise():
    eng, conn = hand({"EURUSD": rising_then_flat()})
    bot = ForwardTeacherBot(volume=0.1, hold_s=60)
    r = BotRunner(cfg_(), eng, bot, ["EURUSD"], forward_s=60)
    for off in (2 * M, 3 * M):
        eng.advance_to(T0 + off)
        r.step(T0 + off)
    opened = [p for p in eng.positions_view()] + eng.history(10)
    assert opened and opened[0]["side"] == "buy"
    eng.advance_to(T0 + 4 * M)
    assert sum(d["net"] for d in eng.history(10)) > 0
    assert all(d["peeked"] == 1 for d in eng.history(10))


# ============================================================================== the learner
def test_fit_logistic_learns_a_real_signal_and_auc_agrees():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(4000, 3))
    y = (X[:, 0] + 0.3 * rng.normal(size=4000) > 0).astype(float)
    w, b = fit_logistic(X, y, l2=1.0)
    assert w[0] > 1.5 and abs(w[1]) < 0.3 and abs(w[2]) < 0.3
    assert 0.9 < auc(X @ w + b, y) < 1.0
    noise = (rng.normal(size=4000) > 0).astype(float)
    assert abs(auc(X @ w + b, noise) - 0.5) < 0.05                 # no relation -> no skill


def test_model_round_trip_and_the_learned_bot_needs_a_real_model(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_MODELS", str(tmp_path))
    rng = np.random.default_rng(1)
    X = rng.normal(size=(500, 2))
    m = train_linear(X, (X[:, 0] > 0).astype(float), (X[:, 0] < 0).astype(float), ["a", "b"], 1.0, {"suggested_threshold": 0.7})
    m.save("unit")
    loaded = LinearModel.load("unit")
    pb, ps = loaded.predict({"a": 2.0, "b": 0.0})
    assert pb > 0.8 > 0.2 > ps and loaded.predict({"b": 0.0}) == loaded.predict({"a": loaded.mean[0], "b": 0.0})     # a missing feature = its mean
    with pytest.raises(ValueError, match="no trained model"):
        make_bot("learned", {"model": "missing"})
    with pytest.raises(ValueError):
        make_bot("learned", {})
    with pytest.raises(ValueError, match="letters"):
        LinearModel.load("../etc/passwd")
    assert isinstance(make_bot("learned", {"model": "unit"}), LearnedBot)


def test_the_learned_bot_is_blind_and_uses_the_models_own_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_MODELS", str(tmp_path))
    names = ["last_m1_range_pips"]
    m = LinearModel(names, [0.0], [1.0], [0.0], 5.0, [0.0], -5.0, {"suggested_threshold": 0.9})      # always: p_buy ~ 1, p_sell ~ 0
    m.save("always_buy")
    eng, conn = hand({"EURUSD": rising_then_flat()})
    bot = make_bot("learned", {"model": "always_buy", "volume": 0.1})
    seen = []
    orig = bot.decide
    bot.decide = lambda ctx: seen.append(ctx.forward("EURUSD")) or orig(ctx)
    r = BotRunner(cfg_(), eng, bot, ["EURUSD"])
    eng.advance_to(T0 + 2 * M)
    r.step(T0 + 2 * M)
    assert seen == [None]
    pos = eng.positions_view()
    assert len(pos) == 1 and pos[0]["side"] == "buy" and pos[0]["peeked"] == 0


# ============================================================================== training data
def test_day_range_is_a_whole_local_day_inclusive():
    lo, hi = day_range("2026-03-04", "2026-03-05", "America/New_York")
    assert hi - lo + 1 == 2 * 86_400_000 and lo == 1772600400000          # 2026-03-04 00:00 EST
    with pytest.raises(ValueError):
        day_range("2026-03-05", "2026-03-04", "UTC")


def _store_features(conn, cfg, sym, t_from, t_to):
    from grader.features import compute_features, store_features
    digits = db.get_digits(conn, sym)
    for t in range(t_from - t_from % M + M, t_to + 1, M):
        store_features(conn, sym, t, compute_features(DataView(conn, sym, t), cfg, digits))


def test_the_training_set_is_exactly_what_the_forward_fed_teacher_decides(tick_db):
    cfg = make_cfg(("EURUSD",))
    conn = db.connect(tick_db)
    t0, t1 = T0 - 30 * M, T0 + 6 * M
    _store_features(conn, cfg, "EURUSD", t0, t1)
    ds = build_dataset(cfg, conn, ["EURUSD"], t0, t1, 60, 60, 1.0, 0.8, 1.0)
    assert ds.n > 20 and ds.X.shape[1] == len(ds.features) and ds.y_buy.sum() + ds.y_sell.sum() > 0
    assert not (ds.y_buy * ds.y_sell).any()                                 # never both sides at once
    # the same decisions, taken the way the bot takes them: from a Context at each minute
    sb = open_sandbox(str(tick_db))
    runner = BotRunner(cfg, Engine(cfg, sb), ForwardTeacherBot(), ["EURUSD"], forward_s=60)
    for t, yb, ys in list(zip(ds.t_ms, ds.y_buy, ds.y_sell))[:60]:
        ctx = runner._context(int(t))
        sig = teacher_signal(ctx.quote("EURUSD"), ctx.forward("EURUSD"), ctx.pip("EURUSD"), 60, 1.0, 0.8, 1.0)
        assert (yb, ys) == ((1, 0) if sig and sig["side"] == "buy" else (0, 1) if sig else (0, 0)), t


def test_fit_reports_a_direction_auc_that_is_near_chance_on_a_structureless_target():
    rng = np.random.default_rng(5)
    n = 3000
    X = rng.normal(size=(n, 4))
    X[:, 0] = np.abs(X[:, 0])                              # a volatility-like feature: positive, and...
    trade = rng.random(n) < np.clip(0.08 + 0.3 * X[:, 0], 0, 1)      # ...volatility IS predictable from it
    side = rng.random(n) < 0.5                             # ...but the direction is a coin flip
    ds = Dataset(["f0", "f1", "f2", "f3"], X, (trade & side).astype(float), (trade & ~side).astype(float),
                 np.arange(n) * M, np.array(["EURUSD"] * n), np.where(trade, 1.5, 0.0))
    model, rep = fit(ds, "x", 5.0, {})
    assert rep["validation_auc_trade"] > 0.6                # it learned when the teacher trades
    assert abs(rep["validation_auc_direction"] - 0.5) < 0.08       # and found NOTHING about which way (the honest headline number)
    assert rep["validation_auc_buy"] > rep["validation_auc_direction"] + 0.05       # the per-side AUCs are inflated by volatility alone
    assert model.meta["suggested_threshold"] > 0
