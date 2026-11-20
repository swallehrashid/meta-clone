import json
import math
from datetime import date, datetime, timezone

import numpy as np
import pytest

from analysis.baseline import stats
from analysis.data import Filters, date_range_ms, load_frame, parse_session
from analysis.oracle import oracle_mask
from analysis.report import build_report, filters_from_args, main as report_main, render_html
from core import db
from core.config import OracleCfg, load_config

MIN = 60_000
H = 3_600_000


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


T0 = utc(2025, 3, 4, 14, 0)          # Tue 09:00 New York (EST)


@pytest.fixture
def cfg():
    c = load_config()
    c.symbols = ["EURUSD", "EURJPY"]
    c.analysis.min_bucket_n = 10
    return c


def add(conn, sym, t, direction, pnl60, *, status="final", spread=1.0, mfe=5.0, mae=-2.0,
        pnl30=None, pnl120=None, pnl300=None, fixed=None, feats=None):
    """Insert one label (+ its features)."""
    p30 = pnl60 if pnl30 is None else pnl30
    p120 = pnl60 if pnl120 is None else pnl120
    p300 = pnl60 if pnl300 is None else pnl300
    fo, fp, fe = (fixed or (None, None, None))
    with conn:
        conn.execute("INSERT OR REPLACE INTO labels (symbol, entry_t_ms, direction, entry_price, spread_pips, pnl_30s, pnl_60s, "
                     "pnl_120s, pnl_300s, mfe_pips, mae_pips, t_to_mfe_ms, t_to_mae_ms, fixed_outcome, fixed_pnl_pips, "
                     "fixed_exit_ms, status) VALUES (?,?,?,1.1,?,?,?,?,?,?,?,1000,2000,?,?,?,?)",
                     (sym, t, direction, spread, p30, pnl60, p120, p300, mfe, mae, fo, fp, fe, status))
        for k, v in (feats or {}).items():
            conn.execute("INSERT OR REPLACE INTO features (symbol, t_ms, name, value) VALUES (?,?,?,?)", (sym, t, k, v))


@pytest.fixture
def conn():
    return db.connect(":memory:")


# ============================================================================== baseline by hand
def test_baseline_numbers_by_hand(conn, cfg):
    pnls = [4.0, -2.0, 1.0, -3.0, 6.0, 0.0]
    for i, p in enumerate(pnls):
        add(conn, "EURUSD", T0 + i * MIN, "buy", p, pnl30=p / 2)
    rep = build_report(conn, cfg, Filters())
    o = rep["baseline"]["overall"]["60"]
    arr = np.array(pnls)
    assert o["n"] == 6 and o["mean"] == pytest.approx(arr.mean()) and o["median"] == pytest.approx(np.median(arr))
    assert o["win_pct"] == pytest.approx(100 * 3 / 6)                       # 0.0 is not a win
    assert o["std"] == pytest.approx(arr.std(ddof=1)) and o["se"] == pytest.approx(arr.std(ddof=1) / math.sqrt(6))
    assert o["ci95"] == [pytest.approx(o["mean"] - 1.96 * o["se"]), pytest.approx(o["mean"] + 1.96 * o["se"])]
    assert rep["baseline"]["overall"]["30"]["mean"] == pytest.approx(arr.mean() / 2)


def test_breakdowns_by_symbol_direction_and_hour(conn, cfg):
    add(conn, "EURUSD", T0, "buy", 2.0)                    # 09:00 NY
    add(conn, "EURUSD", T0, "sell", -4.0)
    add(conn, "EURJPY", T0 + H, "buy", 6.0)                # 10:00 NY
    b = build_report(conn, cfg, Filters())["baseline"]
    assert b["by_symbol"]["EURUSD"]["n"] == 2 and b["by_symbol"]["EURUSD"]["horizons"]["60"]["mean"] == pytest.approx(-1.0)
    assert b["by_direction"]["buy"]["horizons"]["60"]["mean"] == pytest.approx(4.0)
    assert b["by_direction"]["sell"]["n"] == 1
    assert b["by_hour"]["9"]["n"] == 2 and b["by_hour"]["9"]["mean"] == pytest.approx(-1.0)
    assert b["by_hour"]["10"]["mean"] == pytest.approx(6.0)                 # hours are display-timezone hours


def test_mfe_mae_distribution(conn, cfg):
    for i in range(1, 6):
        add(conn, "EURUSD", T0 + i * MIN, "buy", 0.0, mfe=float(i), mae=-float(i))
    mm = build_report(conn, cfg, Filters())["baseline"]["mfe_mae"]
    assert mm["mfe_pips"]["mean"] == 3.0 and mm["mfe_pips"]["p50"] == 3.0 and mm["mae_pips"]["p50"] == -3.0
    assert mm["t_to_mfe_s"]["mean"] == 1.0 and mm["t_to_mae_s"]["mean"] == 2.0       # 1000 ms / 2000 ms


def test_pending_labels_are_excluded(conn, cfg):
    add(conn, "EURUSD", T0, "buy", 1.0)
    add(conn, "EURUSD", T0 + MIN, "buy", 99.0, status="pending")
    rep = build_report(conn, cfg, Filters())
    assert rep["meta"]["n_labels"] == 1 and rep["baseline"]["overall"]["60"]["mean"] == 1.0


def test_fixed_mode_outcomes_reported_only_when_present(conn, cfg):
    add(conn, "EURUSD", T0, "buy", 1.0)
    assert "fixed" not in build_report(conn, cfg, Filters())["baseline"]
    add(conn, "EURUSD", T0 + MIN, "buy", 1.0, fixed=("tp", 4.5, T0 + 9))
    add(conn, "EURUSD", T0 + 2 * MIN, "buy", 1.0, fixed=("sl", -3.0, T0 + 9))
    add(conn, "EURUSD", T0 + 3 * MIN, "buy", 1.0, fixed=("tp", 4.5, T0 + 9))
    fx = build_report(conn, cfg, Filters())["baseline"]["fixed"]
    assert fx["n"] == 3 and fx["outcomes_pct"]["tp"] == pytest.approx(200 / 3) and fx["pnl"]["mean"] == pytest.approx(2.0)


def test_empty_database_gives_an_empty_report_not_a_crash(conn, cfg):
    rep = build_report(conn, cfg, Filters())
    assert rep["meta"]["empty"] and rep["meta"]["n_labels"] == 0
    assert "No final labels match" in render_html(rep)


# ============================================================================== filters
def test_symbol_direction_spread_filters(conn, cfg):
    add(conn, "EURUSD", T0, "buy", 1.0, spread=0.8)
    add(conn, "EURUSD", T0, "sell", 2.0, spread=2.5)
    add(conn, "EURJPY", T0, "buy", 3.0, spread=1.2)
    n = lambda **k: len(load_frame(conn, cfg, Filters(**k)))
    assert n() == 3
    assert n(symbols=["EURUSD"]) == 2 and n(symbols=["EURJPY"]) == 1
    assert n(direction="buy") == 2 and n(direction="sell") == 1
    assert n(max_spread_pips=1.2) == 2 and n(max_spread_pips=0.5) == 0          # boundary is inclusive
    assert n(symbols=["EURUSD"], direction="buy", max_spread_pips=1.0) == 1


def test_date_range_is_in_the_display_timezone_and_end_is_inclusive():
    lo, hi = date_range_ms("2025-03-04", "2025-03-04", "America/New_York")
    assert lo == utc(2025, 3, 4, 5, 0)                    # 00:00 EST
    assert hi == utc(2025, 3, 5, 5, 0) - 1               # 23:59:59.999 EST
    lo_u, hi_u = date_range_ms("2025-03-04", "2025-03-04", "UTC")
    assert (lo_u, hi_u) == (utc(2025, 3, 4), utc(2025, 3, 5) - 1)
    assert date_range_ms(None, None, "UTC") == (None, None)


def test_a_dst_day_is_23_hours_long_not_24():
    lo, hi = date_range_ms("2025-03-09", "2025-03-09", "America/New_York")       # clocks spring forward
    assert (hi + 1 - lo) / H == 23
    lo, hi = date_range_ms("2025-11-02", "2025-11-02", "America/New_York")       # clocks fall back
    assert (hi + 1 - lo) / H == 25


def test_date_filter_boundaries_on_real_rows(conn, cfg):
    add(conn, "EURUSD", utc(2025, 3, 4, 4, 59, 59), "buy", 1.0)    # 23:59:59 on Mar 3 in New York
    add(conn, "EURUSD", utc(2025, 3, 4, 5, 0, 0), "buy", 2.0)      # 00:00:00 on Mar 4
    add(conn, "EURUSD", utc(2025, 3, 5, 4, 59, 59), "buy", 3.0)    # 23:59:59 on Mar 4
    add(conn, "EURUSD", utc(2025, 3, 5, 5, 0, 0), "buy", 4.0)      # 00:00:00 on Mar 5
    f = filters_from_args(cfg, None, "2025-03-04", "2025-03-04", None, None, None)
    assert sorted(load_frame(conn, cfg, f).pnl_60s) == [2.0, 3.0]
    f = filters_from_args(cfg, None, "2025-03-04", None, None, None, None)
    assert sorted(load_frame(conn, cfg, f).pnl_60s) == [2.0, 3.0, 4.0]


def test_session_window_incl_wrapping_past_midnight(conn, cfg):
    for h in (1, 3, 8, 10, 11, 23):                                   # New York hours (EST = UTC-5)
        add(conn, "EURUSD", utc(2025, 3, 4, h + 5, 30) if h + 5 < 24 else utc(2025, 3, 5, h + 5 - 24, 30), "buy", float(h))
    got = lambda s: sorted(load_frame(conn, cfg, Filters(session=parse_session(s, cfg))).pnl_60s.tolist())
    assert got("08:00-11:00") == [8.0, 10.0]                          # 11:30 is outside [08:00, 11:00)
    assert got("ny") == [8.0, 10.0]                                   # sessions.ny_open .. ny_session_end
    assert got("22:00-02:00") == [1.0, 23.0]                          # wraps midnight
    assert got("00:00-24:00") == [1.0, 3.0, 8.0, 10.0, 11.0, 23.0]
    for bad in ("8-11", "08:00", "10:00-10:00", "25:00-26:00"):
        with pytest.raises(ValueError):
            parse_session(bad, cfg)


def test_unknown_symbol_and_direction_rejected(cfg):
    with pytest.raises(ValueError):
        filters_from_args(cfg, "NOPE", None, None, None, None, None)
    with pytest.raises(ValueError):
        filters_from_args(cfg, None, None, None, None, None, "long")
    assert filters_from_args(cfg, "EURUSD, EURJPY", None, None, None, None, None).symbols == ["EURUSD", "EURJPY"]


# ============================================================================== buckets by hand
def planted(conn, n=100, direction="buy"):
    """n entries; feature f = 1..n; pnl_60s deterministic from f."""
    for i in range(1, n + 1):
        add(conn, "EURUSD", T0 + i * MIN, direction, float((i % 7) - 3 + (2 if i > n // 2 else 0)), mfe=3.0 + i / 10,
            feats={"f": float(i)})


def test_quantile_buckets_match_numpy(conn, cfg):
    planted(conn)
    rep = build_report(conn, cfg, Filters())
    pop = rep["populations"]["buy"]
    ft = next(x for x in pop["features"] if x["name"] == "f")
    assert ft["kind"] == "continuous" and len(ft["buckets"]) == 5 and sum(b["n"] for b in ft["buckets"]) == 100
    pnl = np.array([(i % 7) - 3 + (2 if i > 50 else 0) for i in range(1, 101)], dtype=float)
    base = pnl.mean()
    for k, b in enumerate(ft["buckets"]):
        seg = pnl[k * 20:(k + 1) * 20]
        h = b["horizons"]["60"]
        assert b["n"] == 20 and b["share_pct"] == pytest.approx(20.0)
        assert h["mean"] == pytest.approx(seg.mean()) and h["win_pct"] == pytest.approx(100 * (seg > 0).mean())
        assert h["delta"] == pytest.approx(seg.mean() - base)
        assert h["z"] == pytest.approx((seg.mean() - base) / (seg.std(ddof=1) / math.sqrt(20)))
        assert b["lo"] < b["hi"] and (k == 0 or b["lo"] == ft["buckets"][k - 1]["hi"])       # contiguous edges


def test_discrete_features_bucket_by_value_and_missing_is_its_own_row(conn, cfg):
    for i in range(30):
        add(conn, "EURUSD", T0 + i * MIN, "buy", 1.0 if i % 3 == 0 else -1.0,
            feats={"flag": float(i % 3 == 0)} if i < 27 else {})
    ft = next(x for x in build_report(conn, cfg, Filters())["populations"]["buy"]["features"] if x["name"] == "flag")
    assert ft["kind"] == "discrete" and ft["n_missing"] == 3
    by = {b["label"]: b for b in ft["buckets"]}
    assert set(by) == {"0", "1", "missing"} and by["1"]["n"] == 9 and by["0"]["n"] == 18 and by["missing"]["n"] == 3
    assert by["1"]["horizons"]["60"]["mean"] == 1.0 and by["0"]["horizons"]["60"]["mean"] == -1.0


def test_buy_and_sell_are_separate_populations(conn, cfg):
    for i in range(1, 41):
        add(conn, "EURUSD", T0 + i * MIN, "buy", float(i), feats={"f": float(i)})
        add(conn, "EURUSD", T0 + i * MIN, "sell", float(-i), feats={"f": float(i)})
    rep = build_report(conn, cfg, Filters())
    assert set(rep["populations"]) == {"buy", "sell"}
    top = lambda d: rep["populations"][d]["features"][0]["buckets"][-1]["horizons"]["60"]["mean"]
    assert top("buy") > 30 and top("sell") < -30                      # pooling would have averaged to ~0
    assert set(build_report(conn, cfg, Filters(direction="sell"))["populations"]) == {"sell"}


def test_small_buckets_are_flagged_unreliable_at_the_configured_threshold(conn):
    cfg = load_config(); cfg.symbols = ["EURUSD"]                     # default min_bucket_n = 100
    for i in range(520):
        add(conn, "EURUSD", T0 + i * MIN, "buy", 0.0, feats={"f": float(i), "rare": 1.0 if i < 7 else 0.0})
    pop = build_report(conn, cfg, Filters())["populations"]["buy"]
    f = next(x for x in pop["features"] if x["name"] == "f")
    assert all(not b["unreliable"] for b in f["buckets"])             # ~104 each
    rare = {b["label"]: b for b in next(x for x in pop["features"] if x["name"] == "rare")["buckets"]}
    assert rare["1"]["unreliable"] and rare["1"]["n"] == 7 and not rare["0"]["unreliable"]
    assert build_report(conn, cfg, Filters())["meta"]["min_bucket_n"] == 100
    cfg.analysis.min_bucket_n = 110                                    # the threshold really is config
    assert all(b["unreliable"] for b in next(x for x in build_report(conn, cfg, Filters())["populations"]["buy"]["features"] if x["name"] == "f")["buckets"])


def test_a_bucket_of_exactly_the_minimum_size_is_reliable(conn):
    """The plan flags buckets with FEWER than 100 samples; exactly 100 is fine."""
    cfg = load_config(); cfg.symbols = ["EURUSD"]
    for i in range(300):                                   # flag: 100 zeros, 100 ones... exactly min_bucket_n each
        add(conn, "EURUSD", T0 + i * MIN, "buy", 0.0, feats={"flag": float(i % 3), })
    ft = next(x for x in build_report(conn, cfg, Filters())["populations"]["buy"]["features"] if x["name"] == "flag")
    assert [b["n"] for b in ft["buckets"]] == [100, 100, 100] and cfg.analysis.min_bucket_n == 100
    assert [b["unreliable"] for b in ft["buckets"]] == [False, False, False]
    add(conn, "EURUSD", T0 + 999 * MIN, "buy", 0.0, feats={"flag": 0.0})        # now bucket 0 has 101, and bucket 1 still 100
    # (and one fewer than the minimum is flagged)
    conn.execute("DELETE FROM labels WHERE entry_t_ms=?", (T0 + 1 * MIN,)); conn.execute("DELETE FROM features WHERE t_ms=?", (T0 + 1 * MIN,)); conn.commit()
    ft = next(x for x in build_report(conn, cfg, Filters())["populations"]["buy"]["features"] if x["name"] == "flag")
    assert {b["label"]: b["unreliable"] for b in ft["buckets"]} == {"0": False, "1": True, "2": False}


# ============================================================================== oracle
def df_of(conn, cfg):
    return load_frame(conn, cfg, Filters())


def test_top_decile_oracle(conn, cfg):
    for i in range(100):
        add(conn, "EURUSD", T0 + i * MIN, "buy", float(i))
    df = df_of(conn, cfg)
    mask, rule = oracle_mask(df, OracleCfg(mode="top_decile", horizon_s=60, quantile=0.9))
    assert rule["threshold"] == pytest.approx(89.1) and mask.sum() == 10 and df[mask].pnl_60s.min() == 90
    mask, _ = oracle_mask(df, OracleCfg(mode="top_decile", horizon_s=60, quantile=0.5))
    assert mask.sum() == 50


def test_entries_exactly_at_the_threshold_are_in_the_oracle(conn, cfg):
    for i in range(11):                                    # 0..10: the 0.9 quantile is exactly 9.0
        add(conn, "EURUSD", T0 + i * MIN, "buy", float(i))
    df = df_of(conn, cfg)
    mask, rule = oracle_mask(df, OracleCfg(mode="top_decile", horizon_s=60, quantile=0.9))
    assert rule["threshold"] == 9.0 and sorted(df[mask].pnl_60s) == [9.0, 10.0]       # "at or above"


def test_top_decile_uses_the_chosen_horizon(conn, cfg):
    add(conn, "EURUSD", T0, "buy", 0.0, pnl300=9.0)
    add(conn, "EURUSD", T0 + MIN, "buy", 9.0, pnl300=0.0)
    df = df_of(conn, cfg)
    m, _ = oracle_mask(df, OracleCfg(mode="top_decile", horizon_s=300, quantile=0.5))
    assert df[m].entry_t_ms.tolist() == [T0]


def test_run_oracle_is_the_strict_order_independent_test(conn, cfg):
    cases = [(6.0, -1.0), (4.5, -2.9), (4.4, -1.0), (8.0, -3.0), (9.0, -3.5), (5.0, 0.0)]
    for i, (mfe, mae) in enumerate(cases):
        add(conn, "EURUSD", T0 + i * MIN, "buy", 0.0, mfe=mfe, mae=mae)
    df = df_of(conn, cfg)
    mask, rule = oracle_mask(df, OracleCfg(mode="run", run_mfe_pips=4.5, run_mae_pips=3.0))
    # needs mfe >= 4.5 AND mae > -3.0:  yes, yes, no (mfe), no (mae == -3 is touched), no, yes
    assert mask.tolist() == [True, True, False, False, False, True] and "never" in rule["text"]


def test_fixed_tp_oracle(conn, cfg):
    add(conn, "EURUSD", T0, "buy", 0.0, fixed=("tp", 4.5, 1))
    add(conn, "EURUSD", T0 + MIN, "buy", 0.0, fixed=("sl", -3.0, 1))
    add(conn, "EURUSD", T0 + 2 * MIN, "buy", 0.0, fixed=("timeout", 0.2, 1))
    mask, _ = oracle_mask(df_of(conn, cfg), OracleCfg(mode="fixed_tp"))
    assert mask.tolist() == [True, False, False]
    plain = db.connect(":memory:"); add(plain, "EURUSD", T0, "buy", 0.0)
    with pytest.raises(ValueError, match="fixed"):
        oracle_mask(df_of(plain, cfg), OracleCfg(mode="fixed_tp"))


def test_oracle_config_is_validated():
    for bad in ({"mode": "psychic"}, {"horizon_s": 45}, {"quantile": 1.0}, {"quantile": 0.2}):
        with pytest.raises(Exception):
            OracleCfg(**bad)


def test_oracle_share_and_lift_by_hand(conn, cfg):
    # 100 entries, f = 1..100; the 10 best (pnl 90..99) all sit in the top f-bucket (f 81..100)
    for i in range(1, 101):
        add(conn, "EURUSD", T0 + i * MIN, "buy", float(i - 1 if i > 90 else 0), feats={"f": float(i)})
    pop = build_report(conn, cfg, Filters())["populations"]["buy"]
    o = pop["oracle"]
    assert o["n"] == 10 and o["share_pct"] == pytest.approx(10.0)
    b = next(x for x in pop["features"] if x["name"] == "f")["buckets"]
    assert [x["oracle_n"] for x in b] == [0, 0, 0, 0, 10]
    assert b[-1]["oracle_pct"] == pytest.approx(100.0)               # all oracle entries are in this bucket
    assert b[-1]["lift"] == pytest.approx(100.0 / 20.0)              # 100% of oracle in 20% of entries
    assert b[-1]["oracle_rate_pct"] == pytest.approx(50.0) and b[0]["lift"] == 0.0
    assert o["horizons"]["60"]["mean"] == pytest.approx(np.mean(range(90, 100)))
    assert sum(v["oracle_n"] for v in o["by_hour"].values()) == 10


# ============================================================================== does it find real structure?
def test_a_planted_signal_is_found_and_noise_is_not(conn, cfg):
    rng = np.random.default_rng(42)
    n = 4000
    signal, noise = rng.normal(size=n), rng.normal(size=n)
    pnl = -1.0 + 2.0 * signal + rng.normal(scale=2.0, size=n)         # signal drives pnl, noise does not
    for i in range(n):
        add(conn, "EURUSD", T0 + i * MIN, "buy", float(pnl[i]), pnl30=float(pnl[i]), feats={"signal": float(signal[i]), "noise": float(noise[i])})
    pop = build_report(conn, cfg, Filters())["populations"]["buy"]
    sig = next(x for x in pop["features"] if x["name"] == "signal")["buckets"]
    noi = next(x for x in pop["features"] if x["name"] == "noise")["buckets"]
    assert sig[-1]["z"] > 8 and sig[0]["z"] < -8 and sig[-1]["lift"] > 1.8 and sig[0]["lift"] < 0.3   # top bucket owns the oracle
    assert all(abs(b["z"]) < 3.5 for b in noi) and all(0.6 < b["lift"] < 1.5 for b in noi)
    assert [round(b["horizons"]["60"]["mean"], 1) for b in sig] == sorted(round(b["horizons"]["60"]["mean"], 1) for b in sig)
    mt = build_report(conn, cfg, Filters())["meta"]["multiple_testing"]
    assert mt["buckets_tested"] == 10 and mt["reliable_with_abs_z_ge_2"] >= 4


# ============================================================================== cost effects vs price effects
def test_a_pure_cost_effect_is_flagged_by_net_z_but_not_by_the_spread_adjusted_z(conn, cfg):
    """Spread varies with a feature (like liquidity does) and the price has no edge: outcomes are just -spread + noise.
    Net pips then differ hugely between buckets, but the price itself does not move differently."""
    rng = np.random.default_rng(7)
    n = 5000
    f = rng.uniform(0, 10, n)
    spread = 0.6 + f / 5.0                                      # 0.6 .. 2.6 pips, rising with the feature
    pnl = -spread + rng.normal(scale=2.0, size=n)              # price noise only
    for i in range(n):
        add(conn, "EURUSD", T0 + i * MIN, "buy", float(pnl[i]), spread=float(spread[i]), feats={"liq": float(f[i])})
    ft = next(x for x in build_report(conn, cfg, Filters())["populations"]["buy"]["features"] if x["name"] == "liq")
    assert max(abs(b["z"]) for b in ft["buckets"]) > 10          # costs differ: the NET test screams
    assert max(abs(b["adj_z"]) for b in ft["buckets"]) < 3.5     # ...but price movement does not
    assert [round(b["horizons"]["60"]["mean"], 1) for b in ft["buckets"]] == sorted((round(b["horizons"]["60"]["mean"], 1) for b in ft["buckets"]), reverse=True)


def test_a_real_price_signal_is_caught_by_the_spread_adjusted_test_too(conn, cfg):
    rng = np.random.default_rng(8)
    n = 4000
    sig = rng.normal(size=n)
    pnl = -1.0 + 2.0 * sig + rng.normal(scale=2.0, size=n)      # constant spread, price drifts with the signal
    for i in range(n):
        add(conn, "EURUSD", T0 + i * MIN, "buy", float(pnl[i]), spread=1.0, feats={"sig": float(sig[i])})
    ft = next(x for x in build_report(conn, cfg, Filters())["populations"]["buy"]["features"] if x["name"] == "sig")
    assert ft["buckets"][-1]["adj_z"] > 8 and ft["buckets"][0]["adj_z"] < -8
    mt = build_report(conn, cfg, Filters())["meta"]["multiple_testing"]
    assert mt["reliable_with_abs_adj_z_ge_2"] >= 4 and "reliable_with_abs_z_ge_2" in mt


def test_null_market_data_produces_chance_level_findings(tmp_path):
    """End to end on the realistic simulator (a martingale by construction): the baseline is minus the spread at every
    horizon, and only a chance-level number of buckets look 'significant' once the spread is added back."""
    from datetime import date as _d
    from collector.market_model import DAY_MS, MarketModel, SPECS, seq_within_ms
    from grader.labeler_worker import Labeler
    c2 = load_config(); c2.symbols = ["EURUSD", "GBPUSD"]
    c2.analysis.min_bucket_n = 100
    path = tmp_path / "null.db"
    cn = db.connect(path)
    m = MarketModel(21)
    for d in [(_d(2026, 3, k) - _d(1970, 1, 1)).days for k in (3, 4, 5)]:
        for sym in c2.symbols:
            t, b, a = m.day_ticks(sym, d)
            db.insert_ticks(cn, list(zip([sym] * len(t), t.tolist(), b.tolist(), a.tolist(), [0] * len(t), seq_within_ms(t).tolist())))
    for sym in c2.symbols:
        db.set_state(cn, f"digits:{sym}", str(SPECS[sym].digits))
    Labeler(c2, cn).run_once()
    rep = build_report(cn, c2, Filters())
    spread = rep["baseline"]["spread_pips"]["mean"]
    for h in ("30", "60", "120", "300"):
        o = rep["baseline"]["overall"][h]
        assert abs(o["mean"] + spread) < 4 * o["se"] + 0.05, (h, o["mean"], spread)          # net = -spread: no drift to harvest
    mt = rep["meta"]["multiple_testing"]
    assert mt["reliable_buckets"] > 40
    assert mt["reliable_with_abs_adj_z_ge_2"] <= 3 * mt["expected_by_chance"] + 6, mt        # chance level, not dozens of "findings"


# ============================================================================== output formats
def test_json_is_strict_json_with_no_nan(conn, cfg):
    planted(conn, 60)
    add(conn, "EURUSD", T0 + 99 * MIN, "buy", 1.0, mfe=float("nan"))          # NaN in the data
    text = json.dumps(build_report(conn, cfg, Filters()), allow_nan=False)    # raises on NaN / Infinity
    assert json.loads(text)["meta"]["n_labels"] == 61


def test_html_shows_baseline_buckets_sample_sizes_and_unreliable_flags(conn, cfg):
    cfg.analysis.min_bucket_n = 25
    planted(conn, 100)
    for i in range(4):
        add(conn, "EURUSD", T0 + (200 + i) * MIN, "buy", 0.0, feats={"f": 9999.0})
    html_text = render_html(build_report(conn, cfg, Filters()))
    for needle in ("Baseline", "Oracle", "Features", "unreliable", "104 final labels", "Multiple comparisons",
                   "win %", "Δ vs base", "lift", "answer key", "z ex-spr", "entry spread"):
        assert needle in html_text, needle
    assert html_text.count("<span class=tag>unreliable") >= 1
    assert "<script" not in html_text                                  # static page: no JS needed to read it


def test_html_escapes_untrusted_text(conn, cfg):
    cfg2 = cfg.model_copy(deep=True)
    add(conn, "EURUSD", T0, "buy", 1.0, feats={"<b>x</b>": 1.0})
    out = render_html(build_report(conn, cfg2, Filters()))
    assert "<b>x</b>" not in out and "&lt;b&gt;x&lt;/b&gt;" in out


# ============================================================================== against the real pipeline
def test_report_agrees_with_plain_sql_on_labeler_output(tick_db, cfg):
    from grader.labeler_worker import Labeler
    conn = db.connect(tick_db)
    Labeler(cfg, conn).run_once()
    cfg.analysis.min_bucket_n = 20
    rep = build_report(conn, cfg, Filters())
    assert rep["meta"]["n_labels"] == conn.execute("SELECT COUNT(*) FROM labels WHERE status='final'").fetchone()[0] > 300
    for h, col in ((30, "pnl_30s"), (60, "pnl_60s"), (120, "pnl_120s"), (300, "pnl_300s")):
        avg = conn.execute(f"SELECT AVG({col}) FROM labels WHERE status='final'").fetchone()[0]
        assert rep["baseline"]["overall"][str(h)]["mean"] == pytest.approx(avg)
    # one bucket of one feature, recomputed with a hand-written join
    pop = rep["populations"]["buy"]
    ft = next(x for x in pop["features"] if x["name"] == "ticks_last_60s")
    b = ft["buckets"][2]
    last = b is ft["buckets"][-1]
    n, mean = conn.execute(
        "SELECT COUNT(*), AVG(l.pnl_60s) FROM labels l JOIN features f ON f.symbol=l.symbol AND f.t_ms=l.entry_t_ms "
        "WHERE l.status='final' AND l.direction='buy' AND f.name='ticks_last_60s' AND f.value>=? AND f.value" + ("<=?" if last else "<?"),
        (b["lo"], b["hi"])).fetchone()
    assert b["n"] == n and b["horizons"]["60"]["mean"] == pytest.approx(mean)


def test_feature_named_like_a_label_column_does_not_collide(conn, cfg):
    add(conn, "EURUSD", T0, "buy", 1.0, spread=1.5, feats={"spread_pips": 0.7})     # real feature name
    df = load_frame(conn, cfg, Filters())
    assert df.spread_pips.iloc[0] == 1.5 and df["feat:spread_pips"].iloc[0] == 0.7


# ============================================================================== command line + API
def test_one_command_generates_the_report_for_any_date_range(tick_db, cfg, tmp_path, capsys):
    from grader.labeler_worker import Labeler
    Labeler(cfg, db.connect(tick_db)).run_once()
    out = tmp_path / "r.html"
    base = ["--db", str(tick_db), "--out", str(out)]
    assert report_main(base + ["--from", "2025-03-04", "--to", "2025-03-04", "--json", str(tmp_path / "r.json")]) == 0
    assert out.exists() and "Baseline" in out.read_text() and json.loads((tmp_path / "r.json").read_text())["meta"]["n_labels"] > 300
    # the data lives on 2025-03-04 (New York); other days are empty but still produce a valid page
    assert report_main(base + ["--from", "2025-03-05", "--to", "2025-03-06"]) == 0
    assert "No final labels match" in out.read_text()
    assert report_main(base + ["--from", "2025-03-01"]) == 0                       # open-ended range
    assert report_main(base + ["--from", "2025-03-04", "--to", "2025-03-04", "--session", "09:00-09:30", "--symbol", "EURUSD",
                               "--max-spread", "1.2", "--direction", "buy", "--horizon", "120", "--oracle", "run"]) == 0
    capsys.readouterr()
    assert report_main(base + ["--symbol", "NOPE"]) == 2 and "unknown symbol" in capsys.readouterr().out
    assert report_main(base + ["--session", "banana"]) == 2
    assert report_main(base + ["--horizon", "45"]) == 2                              # not a graded horizon


def test_filters_narrow_the_real_report(tick_db, cfg, tmp_path):
    from grader.labeler_worker import Labeler
    conn = db.connect(tick_db); Labeler(cfg, conn).run_once()
    n = lambda **k: build_report(conn, cfg, filters_from_args(cfg, **{**dict(symbols=None, date_from=None, date_to=None, session=None, max_spread=None, direction=None), **k}))["meta"]["n_labels"]
    total = n()
    assert 0 < n(symbols="EURUSD") < total and n(symbols="EURUSD") * 2 == total
    assert 0 < n(session="ny") < total                                              # 08:00-11:00 NY only
    assert n(direction="buy") * 2 == total
    assert n(max_spread=0.0) == 0 and n(session="ny", symbols="EURJPY", direction="sell") > 0


@pytest.fixture
def api(tick_db, cfg):
    from starlette.testclient import TestClient
    from grader.labeler_worker import Labeler
    from server.app import create_app
    from tests.conftest import FakeClock, LIVE_END
    Labeler(cfg, db.connect(tick_db)).run_once()
    clock = FakeClock(LIVE_END)
    with TestClient(create_app(cfg, tick_db, clock, web_dist=None)) as c:
        yield c, clock


def test_api_report_json_and_html(api):
    c, clock = api
    r = c.get("/api/report", params={"date_from": "2025-03-04", "date_to": "2025-03-04"})
    assert r.status_code == 200
    j = r.json()
    assert j["meta"]["n_labels"] > 100 and set(j["populations"]) == {"buy", "sell"} and j["baseline"]["overall"]["60"]["n"] > 100
    page = c.get("/report", params={"symbols": "EURUSD", "session": "ny", "max_spread": 1.5})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html") and "Baseline" in page.text


def test_api_report_cannot_look_past_the_delayed_clock(api):
    c, clock = api
    j = c.get("/api/report").json()                                   # default delay 300 s
    assert j["meta"]["cutoff_ms"] == clock.t - 300_000 and j["meta"]["filters"]["to_ms"] == clock.t - 300_000
    assert c.get("/api/report", params={"t_to": clock.t}).json()["meta"]["filters"]["to_ms"] == clock.t - 300_000
    live = c.get("/api/report", params={"delay": 0, "peeked": 1}).json()
    assert live["meta"]["filters"]["to_ms"] == clock.t and live["meta"]["n_labels"] >= j["meta"]["n_labels"]
    # (only FINAL labels count and those need their 5-minute window, so at a 300 s delay the clamp removes nothing;
    #  a longer delay must remove entries)
    longer = c.get("/api/report", params={"delay": 900}).json()
    assert longer["meta"]["filters"]["to_ms"] == clock.t - 900_000 and longer["meta"]["n_labels"] < j["meta"]["n_labels"]
    assert c.get("/api/report", params={"delay": 0}).status_code == 403
    assert c.get("/report", params={"delay": 0}).status_code == 403


def test_api_validation(api):
    c, _ = api
    for params in ({"symbols": "NOPE"}, {"session": "x"}, {"direction": "up"}, {"horizon": 45}, {"oracle": "fixed_tp"},
                   {"date_from": "not-a-date"}, {"delay": -1}):
        assert c.get("/api/report", params=params).status_code == 400, params
    assert c.get("/api/report", params={"oracle": "run", "horizon": 120}).status_code == 200
