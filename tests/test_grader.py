"""Grader unit tests on hand-made tick sequences + an independent slow reference."""
import random

import numpy as np
import pytest

from grader.grader import GradeParams, grade_arrays
from tests.conftest import T0

PIP = 1e-4
BASE = 1.1000


def ticks(spec):
    """spec: [(dt_ms, bid_offset_pips, spread_pips)] -> arrays. Prices rounded to 5 dp."""
    t = np.array([T0 + s[0] for s in spec], dtype=np.int64)
    bid = np.array([round(BASE + s[1] * PIP, 5) for s in spec])
    ask = np.array([round(BASE + (s[1] + s[2]) * PIP, 5) for s in spec])
    return t, bid, ask


def P(**kw):
    d = dict(pip=PIP, horizons_s=(30, 60, 120, 300), entry_max_wait_ms=10_000)
    d.update(kw)
    return GradeParams(**d)


def g(spec, direction="buy", entry=0, p=None, end=None, **kw):
    t, b, a = ticks(spec)
    return grade_arrays(t, b, a, "EURUSD", T0 + entry, direction, p or P(**kw),
                        int(t[-1]) if end is None else T0 + end)


# a path that runs for the full 300 s window: spread 1.0 pip everywhere
RUN = [(0, 0, 1.0), (30_000, 5, 1.0), (60_000, 3, 1.0), (120_000, -2, 1.0),
       (200_000, 8, 1.0), (300_000, 4, 1.0)]


# ------------------------------------------------------------------ entry & sides
def test_buy_fills_at_ask_and_sell_at_bid():
    b, s = g(RUN, "buy"), g(RUN, "sell")
    assert b.entry_price == pytest.approx(BASE + 1 * PIP)       # ask
    assert s.entry_price == pytest.approx(BASE)                 # bid
    assert b.spread_pips == s.spread_pips == 1.0


def test_buy_and_sell_pnl_by_hand():
    b, s = g(RUN, "buy"), g(RUN, "sell")
    # buy: exit at bid. entry ask = +1.0.  30s bid=+5 -> +4 ; 60s +3 -> +2 ; 120s -2 -> -3 ; 300s +4 -> +3
    assert (b.pnl_30s, b.pnl_60s, b.pnl_120s, b.pnl_300s) == (4.0, 2.0, -3.0, 3.0)
    # sell: entry bid = 0. exit at ask = bid+1. 30s ask=6 -> -6 ; 60s 4 -> -4 ; 120s -1 -> +1 ; 300s 5 -> -5
    assert (s.pnl_30s, s.pnl_60s, s.pnl_120s, s.pnl_300s) == (-6.0, -4.0, 1.0, -5.0)
    assert b.status == s.status == "final"


def test_entry_is_first_tick_at_or_after_entry_time():
    spec = [(0, 0, 1.0), (5_000, 10, 1.0), (305_000, 10, 1.0)]
    assert g(spec, "buy", entry=0).entry_price == pytest.approx(BASE + PIP)
    assert g(spec, "buy", entry=1).entry_price == pytest.approx(BASE + 11 * PIP)   # next tick
    assert g(spec, "buy", entry=5_000).entry_price == pytest.approx(BASE + 11 * PIP)  # on the tick


def test_horizon_marks_the_quote_in_force_not_the_next_one():
    # no tick at +60 s: the +30 s quote (bid +5) is still in force
    spec = [(0, 0, 1.0), (30_000, 5, 1.0), (90_000, 50, 1.0), (305_000, 50, 1.0)]
    b = g(spec, "buy")
    assert b.pnl_60s == pytest.approx(4.0) and b.pnl_120s == pytest.approx(49.0)


def test_mfe_mae_net_of_spread_with_times():
    b = g(RUN, "buy")
    # net unrealised path (bid - ask_entry): -1, +4, +2, -3, +7, +3 -> 300s window includes all
    assert (b.mfe_pips, b.t_to_mfe_ms) == (7.0, 200_000)
    assert (b.mae_pips, b.t_to_mae_ms) == (-3.0, 120_000)


def test_mfe_includes_the_entry_tick_so_it_can_be_negative():
    spec = [(0, 0, 2.0), (10_000, -1, 2.0), (20_000, -4, 2.0), (310_000, -4, 2.0)]
    b = g(spec, "buy")
    assert b.mfe_pips == -2.0 and b.t_to_mfe_ms == 0           # the spread is paid immediately
    assert b.mae_pips == -6.0 and b.t_to_mae_ms == 20_000


# ------------------------------------------------------------------ costs
def test_slippage_is_charged_on_each_fill_and_commission_is_subtracted():
    plain = g(RUN, "buy")
    c = g(RUN, "buy", p=P(slippage_pips=0.25, commission_pips=0.3))
    for col in ("pnl_30s", "pnl_60s", "pnl_120s", "pnl_300s", "mfe_pips", "mae_pips"):
        assert getattr(c, col) == pytest.approx(getattr(plain, col) - 0.8)   # 2*0.25 + 0.3


# ------------------------------------------------------------------ pending / ungradable
def test_pending_until_the_full_horizon_is_in_the_data():
    short = RUN[:-1]                                            # newest tick at +200 s
    lab = g(short, "buy")
    assert lab.status == "pending" and lab.entry_price is not None and lab.pnl_300s is None
    assert g(RUN, "buy").status == "final"
    # the data end (not the arrays) decides: ticks may exist beyond the loaded arrays
    t, b, a = ticks(short)
    assert grade_arrays(t, b, a, "EURUSD", T0, "buy", P(), T0 + 310_000).status == "final"


def test_no_tick_soon_after_entry_is_ungradable():
    spec = [(0, 0, 1.0), (60_000, 0, 1.0), (400_000, 0, 1.0)]
    assert g(spec, "buy", entry=1_000, end=400_000) is None     # next tick is 59 s away


def test_entry_inside_a_data_hole_is_ungradable_but_one_beyond_the_data_is_pending():
    t, b, a = ticks([(0, 0, 1.0), (1_000, 0, 1.0)])
    # arrays hold nothing at/after the entry, but the DB has data far later -> hole -> ungradable
    assert grade_arrays(t, b, a, "EURUSD", T0 + 500_000, "buy", P(), T0 + 9_000_000) is None
    # nothing stored later either -> the quote just has not arrived yet
    lab = grade_arrays(t, b, a, "EURUSD", T0 + 500_000, "buy", P(), T0 + 1_000)
    assert lab.status == "pending" and lab.entry_price is None


def test_window_crossing_market_close_is_ungradable():
    from datetime import datetime, timezone
    fri = int(datetime(2025, 3, 14, 20, 58, tzinfo=timezone.utc).timestamp() * 1000)  # 16:58 NY
    spec = [(0, 0, 1.0), (299_000, 0, 1.0), (600_000, 0, 1.0)]
    t = np.array([fri + s[0] for s in spec], dtype=np.int64)
    b = np.array([BASE] * 3); a = b + PIP
    assert grade_arrays(t, b, a, "EURUSD", fri, "buy", P(), int(t[-1])) is None


def test_entry_while_the_market_is_closed_is_ungradable_at_once():
    from datetime import datetime, timezone
    sat = int(datetime(2025, 3, 8, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t = np.array([sat], dtype=np.int64)
    assert grade_arrays(t, np.array([BASE]), np.array([BASE + PIP]), "EURUSD", sat, "buy", P(), None) is None


def test_bad_direction():
    with pytest.raises(ValueError):
        g(RUN, "long")


# ------------------------------------------------------------------ fixed SL/TP mode
FIX = dict(fixed_enabled=True, sl_pips=3.0, tp_pips=4.5, max_hold_s=300)


def fixed(path, direction="buy", **kw):
    """path: [(dt_ms, bid_off, spread)]; always ends with a far tick so the window is complete."""
    return g(path + [(310_000, path[-1][1], path[-1][2])], direction, p=P(**{**FIX, **kw}))


def test_tp_hit_first():
    lab = fixed([(0, 0, 1.0), (10_000, 2, 1.0), (20_000, 5.5, 1.0), (30_000, -9, 1.0)])
    # entry ask +1.0 ; TP needs bid >= +5.5 : hit at 20 s
    assert (lab.fixed_outcome, lab.fixed_pnl_pips, lab.fixed_exit_ms) == ("tp", 4.5, T0 + 20_000)


def test_sl_hit_first():
    lab = fixed([(0, 0, 1.0), (10_000, -1, 1.0), (20_000, -2, 1.0), (30_000, 9, 1.0)])
    # entry ask +1.0 ; SL needs bid <= -2.0 : hit at 20 s, filled at the tick: -3.0
    assert (lab.fixed_outcome, lab.fixed_pnl_pips, lab.fixed_exit_ms) == ("sl", -3.0, T0 + 20_000)


def test_sl_gap_fills_at_the_tick_price_worse_than_sl():
    lab = fixed([(0, 0, 1.0), (10_000, -10, 1.0)])
    assert lab.fixed_outcome == "sl" and lab.fixed_pnl_pips == -11.0


def test_timeout_marks_the_quote_at_max_hold():
    lab = fixed([(0, 0, 1.0), (100_000, 2, 1.0), (200_000, 0.5, 1.0)])
    assert lab.fixed_outcome == "timeout" and lab.fixed_exit_ms == T0 + 300_000
    assert lab.fixed_pnl_pips == pytest.approx(0.5 - 1.0)      # bid +0.5 vs ask entry +1.0


def test_spread_wider_than_tp_never_hits_tp():
    # 6-pip spread, TP 4.5: bid would have to rise 6+4.5 pips above the original bid
    lab = fixed([(0, 0, 6.0), (10_000, 5, 6.0), (20_000, 9, 6.0)], tp_pips=4.5, sl_pips=30.0)
    assert lab.fixed_outcome == "timeout"
    lab2 = fixed([(0, 0, 6.0), (10_000, 11, 6.0)], tp_pips=4.5, sl_pips=30.0)   # bid +11 >= 6+4.5
    assert lab2.fixed_outcome == "tp"


def test_spread_wider_than_sl_stops_out_on_the_entry_tick():
    lab = fixed([(0, 0, 3.5), (10_000, 20, 3.5)], sl_pips=3.0)
    assert (lab.fixed_outcome, lab.fixed_exit_ms) == ("sl", T0) and lab.fixed_pnl_pips == -3.5


def test_sell_side_fixed_mode_uses_the_ask():
    # sell at bid 0 ; TP when ask <= -4.5 ; ask = bid + 1  -> bid <= -5.5
    assert fixed([(0, 0, 1.0), (10_000, -5.5, 1.0)], "sell").fixed_outcome == "tp"
    # SL when ask >= +3.0 -> bid >= +2.0
    lab = fixed([(0, 0, 1.0), (10_000, 2.0, 1.0)], "sell")
    assert lab.fixed_outcome == "sl" and lab.fixed_pnl_pips == -3.0


def test_tp_exactly_on_the_level_counts_despite_float_noise():
    lab = fixed([(0, 0, 1.0), (10_000, 5.5, 1.0)])               # bid exactly entry+4.5 pips
    assert lab.fixed_outcome == "tp"


def test_fixed_costs_applied():
    lab = fixed([(0, 0, 1.0), (10_000, 5.5, 1.0)], slippage_pips=0.1, commission_pips=0.2)
    assert lab.fixed_pnl_pips == pytest.approx(4.5 - 0.4)


# ------------------------------------------------------------------ identical timestamps
def test_ticks_at_identical_timestamps_use_sequence_order():
    # three ticks share one millisecond; entry uses the first, and in fixed mode the SL tick that
    # comes BEFORE the TP tick in sequence wins even though they have the same time
    spec = [(0, 0, 1.0), (5_000, -2, 1.0), (5_000, 6, 1.0), (5_000, 0, 1.0), (310_000, 0, 1.0)]
    lab = g(spec, "buy", p=P(**FIX))
    assert lab.fixed_outcome == "sl" and lab.fixed_exit_ms == T0 + 5_000
    spec2 = [(0, 0, 1.0), (0, 50, 1.0), (310_000, 0, 1.0)]
    assert g(spec2, "buy").entry_price == pytest.approx(BASE + PIP)   # first of the duplicates
    spec3 = [(0, 0, 1.0), (5_000, 6, 1.0), (5_000, -2, 1.0), (310_000, 0, 1.0)]
    assert g(spec3, "buy", p=P(**FIX)).fixed_outcome == "tp"          # TP tick first this time


# ------------------------------------------------------------------ independent reference
def reference(ticks_list, entry_t, direction, p):
    """Slow, literal re-statement of the spec: walk tick by tick. Returns a dict or None."""
    buy = direction == "buy"
    ent = next((x for x in ticks_list if x[0] >= entry_t), None)
    if ent is None:
        return None
    t_e, price = ent[0], (ent[2] if buy else ent[1])
    out = {"entry": price}
    cost = 2 * p.slippage_pips + p.commission_pips

    def pnl_at(x):
        return ((x[1] - price) if buy else (price - x[2])) / p.pip - cost

    def quote_at(ms):
        q = None
        for x in ticks_list:
            if x[0] <= ms:
                q = x
        return q
    for h in p.horizons_s:
        out[h] = round(pnl_at(quote_at(t_e + h * 1000)), 6)
    win = [x for x in ticks_list if t_e <= x[0] <= t_e + p.te_window_ms]
    series = [pnl_at(x) for x in win]
    out["mfe"], out["mae"] = round(max(series), 6), round(min(series), 6)
    out["t_mfe"] = win[series.index(max(series))][0] - t_e
    out["t_mae"] = win[series.index(min(series))][0] - t_e
    if p.fixed_enabled:
        res = None
        for x in ticks_list:
            if x[0] < t_e:
                continue
            if x[0] > t_e + p.max_hold_s * 1000:
                break
            fav = round(((x[1] - price) if buy else (price - x[2])) / p.pip, 6)
            if fav >= p.tp_pips:
                res = ("tp", round(p.tp_pips - cost, 6), x[0]); break
            if fav <= -p.sl_pips:
                res = ("sl", round(fav - cost, 6), x[0]); break
        if res is None:
            res = ("timeout", round(pnl_at(quote_at(t_e + p.max_hold_s * 1000)), 6),
                   t_e + p.max_hold_s * 1000)
        out["fixed"] = res
    return out


@pytest.mark.parametrize("fixed_on,slip,comm", [(False, 0.0, 0.0), (True, 0.0, 0.0), (True, 0.15, 0.35)])
def test_vectorised_grader_equals_slow_reference_on_random_paths(fixed_on, slip, comm):
    rng = random.Random(1234)
    p = P(fixed_enabled=fixed_on, slippage_pips=slip, commission_pips=comm, sl_pips=2.5, tp_pips=3.5)
    compared, outcomes = 0, set()
    for trial in range(60):
        # random walk with irregular gaps, duplicate timestamps and varying spread
        t, bid, spec = 0, 0.0, []
        sigma = rng.choice([0.03, 0.1, 0.9])                  # calm paths reach the timeout branch
        while t <= 340_000:
            spec.append((t, round(bid * 10) / 10, rng.choice([0.6, 1.0, 1.4, 2.0])))
            t += rng.choice([0, 0, 120, 700, 2500, 9000])
            bid += rng.gauss(0, sigma)
        ta, ba, aa = ticks(spec)
        tl = list(zip(ta.tolist(), ba.tolist(), aa.tolist()))
        for direction in ("buy", "sell"):
            entry = rng.choice([0, 1, 4_000, 12_000])
            got = grade_arrays(ta, ba, aa, "EURUSD", T0 + entry, direction, p, int(ta[-1]))
            want = reference(tl, T0 + entry, direction, p)
            if got is None or got.status != "final":
                continue                                      # (entry too late for a full window)
            compared += 1
            assert got.entry_price == pytest.approx(want["entry"])
            for h, col in ((30, "pnl_30s"), (60, "pnl_60s"), (120, "pnl_120s"), (300, "pnl_300s")):
                assert getattr(got, col) == pytest.approx(want[h], abs=1e-5)
            assert got.mfe_pips == pytest.approx(want["mfe"], abs=1e-5)
            assert got.mae_pips == pytest.approx(want["mae"], abs=1e-5)
            assert (got.t_to_mfe_ms, got.t_to_mae_ms) == (want["t_mfe"], want["t_mae"])
            if fixed_on:
                assert (got.fixed_outcome, got.fixed_exit_ms) == (want["fixed"][0], want["fixed"][2])
                assert got.fixed_pnl_pips == pytest.approx(want["fixed"][1], abs=1e-5)
                outcomes.add(got.fixed_outcome)
    assert compared >= 80, f"only {compared} comparisons ran - test is not exercising the grader"
    if fixed_on:
        assert outcomes == {"tp", "sl", "timeout"}, outcomes   # every branch really occurred
