import json
import shutil
from datetime import date

import pytest

from bot.evaluate import evaluate, render_html
from bot.run import main as run_main
from bot.runner import RunResult, RunStats, run_replay
from bot.strategies import make_bot
from collector.market_model import DAY_MS, MarketModel, SPECS, seq_within_ms
from core import db
from core.config import load_config
from grader.labeler_worker import Labeler
from tools.sandbox import open_sandbox

MIN = 60_000
T = 1_772_640_000_000          # a Wednesday, on a minute boundary


def cfg_():
    c = load_config()
    c.symbols = ["EURUSD", "GBPUSD"]
    return c


def put(conn, sym, t, direction, p60, spread=1.0):
    with conn:
        conn.execute("INSERT OR REPLACE INTO labels (symbol, entry_t_ms, direction, entry_price, spread_pips, pnl_30s, pnl_60s, pnl_120s, pnl_300s, "
                     "mfe_pips, mae_pips, t_to_mfe_ms, t_to_mae_ms, status) VALUES (?,?,?,1.1,?,?,?,?,?,1,-1,1,1,'final')",
                     (sym, t, direction, spread, p60, p60, p60, p60))


def deal(sym, side, open_ms, net_pips, dur=60, reason="manual"):
    return dict(symbol=sym, side=side, open_ms=open_ms, close_ms=open_ms + dur * 1000, duration_s=dur, net_pips=net_pips, pips=net_pips,
                net=net_pips, reason=reason, source="bot", peeked=0)


def result(deals, t0=T, t1=T + 200 * MIN):
    return RunResult("hand", {}, ("EURUSD",), t0, t1, RunStats(), deals, [{"t_ms": t0, "equity": 10000.0}], {}, [], 10000.0)


def test_evaluation_numbers_by_hand():
    conn = db.connect(":memory:")
    for i in range(100):
        put(conn, "EURUSD", T + i * MIN, "buy", float(i))             # buys: 0..99  -> baseline 49.5, oracle = top 10% (>= 89.1)
        put(conn, "EURUSD", T + i * MIN, "sell", float(-i))           # sells: 0..-99 -> oracle = i <= 9
    deals = [deal("EURUSD", "buy", T + k * MIN + 7_000, 90.0) for k in (92, 93, 94, 95, 96)]     # opened inside minutes 92..96
    ev = evaluate(conn, cfg_(), result(deals))
    assert ev["matching"] == {"deals": 5, "matched": 5}
    assert ev["selection_edge_pips"]["mean"] == pytest.approx(94 - 49.5)                        # label outcomes of its entries vs the average buy
    assert ev["selection_edge_ex_spread_pips"]["mean"] == pytest.approx(94 - 49.5)              # constant spread cancels
    assert ev["exit_effect_pips"]["mean"] == pytest.approx(90 - 94)                             # realised minus the label's result
    o = ev["oracle"]
    assert o["base_rate_pct"] == pytest.approx(10.0) and o["bot_rate_pct"] == 100.0 and o["lift"] == pytest.approx(10.0)
    assert o["bot_entries_in_oracle"] == 5 and o["z_naive"] == pytest.approx((5 - 0.5) / (5 * 0.1 * 0.9) ** 0.5)
    assert o["robust"] is False and o["z"] == o["z_naive"]                                       # 5 trades in one hour: too few clusters to trust the robust test
    assert ev["bot_net_pips"]["mean"] == 90.0 and ev["by_symbol"]["EURUSD"]["n"] == 5
    assert ev["by_exit_reason"]["manual"]["n"] == 5


def test_direction_edge_compares_the_bots_side_with_the_opposite_side_of_the_same_entry():
    conn = db.connect(":memory:")
    for i in range(30):
        put(conn, "EURUSD", T + i * MIN, "buy", 3.0 + i % 2)          # buys do well ...
        put(conn, "EURUSD", T + i * MIN, "sell", -2.0 - i % 2)        # ... so the sells do badly
    buys = [deal("EURUSD", "buy", T + i * MIN, 3.0) for i in range(0, 30, 2)]
    sells = [deal("EURUSD", "sell", T + i * MIN, -2.0) for i in range(0, 30, 2)]
    assert evaluate(conn, cfg_(), result(buys))["direction_edge_pips"]["mean"] == pytest.approx(3.0 - (-2.0))       # picked the right side
    assert evaluate(conn, cfg_(), result(sells))["direction_edge_pips"]["mean"] == pytest.approx(-2.0 - 3.0)       # picked the wrong side
    mixed = evaluate(conn, cfg_(), result(buys[:7] + sells[:7]))
    assert mixed["direction_edge_pips"]["mean"] == pytest.approx(0.0)                                              # a coin-flip has none


def test_volatility_selection_inflates_oracle_lift_but_not_direction_edge():
    """Entries picked ONLY for being in volatile minutes (either side) look 'skilled' on oracle lift, yet have no direction edge."""
    import numpy as np
    conn = db.connect(":memory:")
    rng = np.random.default_rng(3)
    deals = []
    for i in range(1500):
        vol = 6.0 if i % 3 == 0 else 1.0                              # a third of the minutes are 6x as volatile
        move = rng.normal(scale=vol)
        put(conn, "EURUSD", T + i * MIN, "buy", move - 1.0)           # net of a 1-pip spread
        put(conn, "EURUSD", T + i * MIN, "sell", -move - 1.0)
        if i % 3 == 0:
            deals.append(deal("EURUSD", "buy" if rng.random() < 0.5 else "sell", T + i * MIN, 0.0))      # random direction, volatile minutes only
    ev = evaluate(conn, cfg_(), result(deals, T, T + 1500 * MIN))
    assert ev["oracle"]["lift"] > 1.8                                  # looks like skill...
    assert abs(ev["direction_edge_pips"]["z"]) < 3.5                   # ...but the DIRECTION test sees none


def test_selection_edge_separates_price_from_cheap_fills():
    """The bot only trades minutes with a tight spread. Outcomes are -spread: net pips look better, but price did nothing."""
    conn = db.connect(":memory:")
    for i in range(200):
        spread = 0.5 if i % 2 == 0 else 2.5
        put(conn, "EURUSD", T + i * MIN, "buy", -spread, spread)        # no price movement at all: net = -spread
    deals = [deal("EURUSD", "buy", T + i * MIN + 1000, -0.5) for i in range(0, 200, 2)]
    ev = evaluate(conn, cfg_(), result(deals))
    assert ev["selection_edge_pips"]["mean"] == pytest.approx(-0.5 - (-1.5))                    # +1.0 pips better than the average entry...
    assert ev["selection_edge_ex_spread_pips"]["mean"] == pytest.approx(0.0, abs=1e-9)          # ...and exactly zero once the spread is added back
    assert ev["spread"]["bot_entries_avg_pips"] == pytest.approx(0.5) and ev["spread"]["all_entries_avg_pips"] == pytest.approx(1.5)


def test_the_label_horizon_is_fixed_not_chosen_from_the_trades_own_duration():
    conn = db.connect(":memory:")
    for i in range(20):
        with conn:
            conn.execute("INSERT INTO labels (symbol, entry_t_ms, direction, entry_price, spread_pips, pnl_30s, pnl_60s, pnl_120s, pnl_300s, mfe_pips, mae_pips, "
                         "t_to_mfe_ms, t_to_mae_ms, status) VALUES ('EURUSD', ?, 'buy', 1.1, 1.0, 30, 60, 120, 300, 1, -1, 1, 1, 'final')", (T + i * MIN,))
    quick, slow = deal("EURUSD", "buy", T + 3 * MIN, 0.0, dur=20), deal("EURUSD", "buy", T + 4 * MIN, 0.0, dur=290)
    ev = evaluate(conn, cfg_(), result([quick, slow]))
    assert ev["horizon_s"] == 60 and ev["selection_edge_pips"]["n"] == 2
    assert ev["exit_effect_pips"]["mean"] == pytest.approx(0.0 - 60)                     # BOTH compared at 60 s, whatever their durations
    assert evaluate(conn, cfg_(), result([quick, slow]), horizon_s=300)["exit_effect_pips"]["mean"] == pytest.approx(-300)
    with pytest.raises(ValueError):
        evaluate(conn, cfg_(), result([quick]), horizon_s=45)


def test_stopped_trades_do_not_create_a_phantom_direction_edge():
    """Random sides on a driftless random walk, with trade durations set by a stop (path dependent). A duration-matched horizon
    produced a significant direction edge here; the fixed horizon must show none."""
    import numpy as np
    conn = db.connect(":memory:")
    rng = np.random.default_rng(11)
    deals = []
    for i in range(3000):
        steps = rng.normal(size=300)                                  # one-second increments
        path = np.cumsum(steps) * 0.4
        mv = {h: path[h - 1] for h in (30, 60, 120, 300)}
        for side, sgn in (("buy", 1), ("sell", -1)):
            with conn:
                conn.execute("INSERT INTO labels (symbol, entry_t_ms, direction, entry_price, spread_pips, pnl_30s, pnl_60s, pnl_120s, pnl_300s, mfe_pips, mae_pips, "
                             "t_to_mfe_ms, t_to_mae_ms, status) VALUES ('EURUSD', ?, ?, 1.1, 1.0, ?, ?, ?, ?, 1, -1, 1, 1, 'final')",
                             (T + i * MIN, side, sgn * mv[30] - 1, sgn * mv[60] - 1, sgn * mv[120] - 1, sgn * mv[300] - 1))
        side = "buy" if rng.random() < 0.5 else "sell"                 # direction independent of the path
        sgn = 1 if side == "buy" else -1
        hit = np.flatnonzero(np.abs(path) > 3.0)                       # a stop: exits when the price has moved 3 pips either way
        dur = int(hit[0]) + 1 if len(hit) else 300
        deals.append(deal("EURUSD", side, T + i * MIN, sgn * path[min(dur, 300) - 1] - 1, dur=dur, reason="sl"))
    ev = evaluate(conn, cfg_(), result(deals, T, T + 3000 * MIN))
    assert abs(ev["direction_edge_pips"]["z"]) < 3.5 and abs(ev["direction_edge_pips"]["mean"]) < 0.15


def test_matching_uses_the_symbol_direction_and_grid_minute_and_nearest_horizon():
    conn = db.connect(":memory:")
    for i in range(20):
        put(conn, "EURUSD", T + i * MIN, "buy", float(i))
    deals = [deal("EURUSD", "buy", T + 5 * MIN + 59_999, 1.0),     # last ms of minute 5 -> matched
             deal("EURUSD", "buy", T + 6 * MIN, 1.0),              # first ms of minute 6 -> matched
             deal("EURUSD", "sell", T + 7 * MIN, 1.0),             # wrong direction -> not matched
             deal("GBPUSD", "buy", T + 8 * MIN, 1.0),              # other symbol -> not matched
             deal("EURUSD", "buy", T + 50 * MIN, 1.0)]             # no label that late -> not matched
    ev = evaluate(conn, cfg_(), result(deals))
    assert ev["matching"] == {"deals": 5, "matched": 2}
    assert ev["selection_edge_pips"]["n"] == 2


def test_degenerate_inputs_do_not_crash():
    conn = db.connect(":memory:")
    assert evaluate(conn, cfg_(), result([]))["matching"] == {"deals": 0, "matched": 0}
    ev = evaluate(conn, cfg_(), result([deal("EURUSD", "buy", T, 1.0)]))
    assert ev["matching"]["matched"] == 0 and "run the labeler" in ev["matching"]["note"]
    assert "run the labeler" in render_html(ev)
    put(conn, "EURUSD", T, "buy", 1.0)
    assert evaluate(conn, cfg_(), result([deal("EURUSD", "buy", T + 5, 1.0)]))["matching"]["matched"] == 1


def test_html_report_shows_the_key_numbers():
    conn = db.connect(":memory:")
    for i in range(100):
        put(conn, "EURUSD", T + i * MIN, "buy", float(i))
    ev = evaluate(conn, cfg_(), result([deal("EURUSD", "buy", T + k * MIN, 5.0) for k in range(10, 40)]))
    page = render_html(ev)
    for needle in ("Bot run", "Selection edge", "ex-spread", "Oracle lift", "Exit effect", "Profit factor", "not evidence of future performance"):
        assert needle in page, needle


# ============================================================================== end to end on the realistic simulator
@pytest.fixture(scope="module")
def labeled(tmp_path_factory):
    path = tmp_path_factory.mktemp("evalmkt") / "ticks.db"
    conn = db.connect(path)
    m = MarketModel(51)
    for k in (4, 5):
        d = (date(2026, 3, k) - date(1970, 1, 1)).days
        for sym in ("EURUSD", "GBPUSD"):
            t, b, a = m.day_ticks(sym, d)
            db.insert_ticks(conn, list(zip([sym] * len(t), t.tolist(), b.tolist(), a.tolist(), [0] * len(t), seq_within_ms(t).tolist())))
    for sym in ("EURUSD", "GBPUSD"):
        db.set_state(conn, f"digits:{sym}", str(SPECS[sym].digits))
    Labeler(cfg_(), conn).run_once()
    conn.close()
    return path


LO = (date(2026, 3, 4) - date(1970, 1, 1)).days * DAY_MS + 9 * 3_600_000
HI = LO + 22 * 3_600_000
TWO_DAYS = LO + 38 * 3_600_000          # through Thursday evening: both labelled days


def run(path, bot, hi=HI):
    conn = open_sandbox(str(path))
    res = run_replay(cfg_(), conn, bot, ["EURUSD", "GBPUSD"], LO, hi)
    return res, evaluate(conn, cfg_(), res)


def test_a_random_bot_on_a_market_with_no_edge_shows_no_edge(labeled):
    res, ev = run(labeled, make_bot("random_entry", {"p_enter": 0.12, "hold_s": 120, "seed": 7}))
    s = ev["summary"]
    assert s["trades"] > 150 and s["net_usd"] < 0 and s["avg_net_pips"] < -0.8          # it loses about the spread
    assert ev["matching"]["matched"] > 0.9 * s["trades"]
    for k in ("selection_edge_pips", "selection_edge_ex_spread_pips"):
        assert abs(ev[k]["z"]) < 3.5, (k, ev[k])
    assert abs(ev["oracle"]["z"]) < 3.5 and 0.6 < ev["oracle"]["lift"] < 1.6


def test_cost_control_looks_like_skill_on_net_pips_but_is_not_price_edge(labeled):
    _, plain = run(labeled, make_bot("random_entry", {"p_enter": 0.25, "hold_s": 120, "seed": 7}), TWO_DAYS)
    _, tight = run(labeled, make_bot("random_entry", {"p_enter": 0.25, "hold_s": 120, "seed": 7, "max_spread_pips": 0.85}), TWO_DAYS)
    assert tight["summary"]["avg_net_pips"] > plain["summary"]["avg_net_pips"] + 0.25             # cheaper fills: better net pips
    assert tight["spread"]["bot_entries_avg_pips"] < plain["spread"]["bot_entries_avg_pips"] - 0.3
    assert tight["selection_edge_pips"]["mean"] > 0.25 and tight["selection_edge_pips"]["z_naive"] > 3    # the (naive) net test is fooled...
    assert abs(tight["selection_edge_ex_spread_pips"]["z"]) < 3.5                                   # ...the ex-spread test is not


# ============================================================================== the command line
def test_cli_replays_grades_and_writes_reports(labeled, tmp_path, capsys):
    out, js = tmp_path / "r.html", tmp_path / "r.json"
    assert run_main(["--db", str(labeled), "--bot", "random_entry", "--params", '{"p_enter": 0.1, "seed": 2}', "--from", "2026-03-04", "--to", "2026-03-04",
                     "--symbols", "EURUSD", "--limits", '{"max_open_positions": 1}', "--out", str(out), "--json", str(js)]) == 0
    text = capsys.readouterr().out
    assert "random_entry on EURUSD" in text and "selection edge" in text
    j = json.loads(js.read_text())
    assert j["summary"]["trades"] > 20 and "Bot run" in out.read_text()
    assert run_main(["--list"]) == 0 and "momentum_breakout" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--bot", "nope"], ["--bot", "random_entry", "--params", "{"], ["--bot", "random_entry", "--params", '{"p_enter": 9}'],
                                  ["--bot", "random_entry", "--limits", '{"x": 1}'], ["--bot", "random_entry", "--from", "not-a-date"]])
def test_cli_rejects_bad_input_with_exit_code_2(labeled, capsys, args):
    full = ["--db", str(labeled), "--from", "2026-03-04", "--to", "2026-03-04"] + args if "--from" not in args else ["--db", str(labeled), "--to", "2026-03-04"] + args
    assert run_main(full) == 2 and "error" in capsys.readouterr().out


# ============================================================================== the significance test itself
def test_the_cluster_robust_z_is_calibrated_where_the_naive_z_is_overconfident():
    """Null data (true mean 0) whose values share an hourly shock, like trades sharing a volatility regime."""
    import numpy as np
    import pandas as pd
    from bot.evaluate import _ms
    rng = np.random.default_rng(0)
    hours, per = 60, 20
    blocks = pd.Series(np.repeat(np.arange(hours), per))
    zr, zn = [], []
    for _ in range(300):
        x = pd.Series(np.repeat(rng.normal(size=hours), per) * 0.8 + rng.normal(size=hours * per) * 0.6)
        r = _ms(x, blocks)
        assert r["robust"] and r["clusters"] == hours
        zr.append(r["z"]); zn.append(r["z_naive"])
    assert 0.8 < np.std(zr) < 1.25                                  # calibrated: an honest z for null data
    assert np.std(zn) > 2.5                                         # the naive z would cry wolf constantly
    assert np.mean(np.abs(np.array(zr)) > 1.96) < 0.10              # ~5% false positives, not ~60%


def test_the_robust_test_still_detects_a_real_effect():
    import numpy as np
    import pandas as pd
    from bot.evaluate import _ms
    rng = np.random.default_rng(1)
    blocks = pd.Series(np.repeat(np.arange(80), 15))
    x = pd.Series(0.35 + np.repeat(rng.normal(size=80), 15) * 0.5 + rng.normal(size=1200))
    assert _ms(x, blocks)["z"] > 4


def test_too_few_clusters_fall_back_to_the_naive_test_and_say_so():
    import pandas as pd
    from bot.evaluate import _ms
    r = _ms(pd.Series([1.0, 2.0, 3.0, 4.0]), pd.Series([1, 1, 2, 2]))
    assert r["robust"] is False and r["z"] == r["z_naive"]
    assert _ms(pd.Series([], dtype=float))["z"] is None
