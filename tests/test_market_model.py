"""The market model must be realistic enough to be useful and have NO exploitable structure."""
from datetime import date, datetime, timezone

import numpy as np
import pytest

from collector.collector import Collector
from collector.market_model import DAY_MS, MarketModel, MarketSimClient, SPECS, seq_within_ms
from core import db
from core.config import load_config
from core.pips import pip_size
from tools.make_history import main as make_history

D = lambda y, m, d: (date(y, m, d) - date(1970, 1, 1)).days
WEEKDAYS = [D(2026, 3, 2) + k for k in range(0, 32) if (D(2026, 3, 2) + k + 3) % 7 < 5]     # 22 weekdays in March 2026


@pytest.fixture(scope="module")
def model():
    return MarketModel(1)


def minute_mid(model, sym, days):
    """Mid price sampled at each minute boundary that has a tick (last tick of the minute)."""
    out_t, out_m = [], []
    for d in days:
        t, b, a = model.day_ticks(sym, d)
        if not len(t):
            continue
        m = (b + a) / 2
        minute = t // 60_000
        last = np.r_[minute[1:] != minute[:-1], True]
        out_t.append(minute[last] * 60_000); out_m.append(m[last])
    return np.concatenate(out_t), np.concatenate(out_m)


def returns(model, sym, days, step):
    t, m = minute_mid(model, sym, days)
    grid = np.arange(t[0], t[-1] + 1, step)
    idx = np.searchsorted(t, grid, "right") - 1
    px = m[idx]
    keep = (grid - t[idx]) < 150_000                              # ignore grid points in closed periods (stale price)
    r = np.diff(px) / pip_size(SPECS[sym].digits)
    ok = keep[1:] & keep[:-1] & (np.abs(r) < 200)
    return r[ok]


# ------------------------------------------------------------------ determinism
def test_pure_function_of_seed_symbol_and_day():
    a, b = MarketModel(5), MarketModel(5)
    d = WEEKDAYS[3]
    for sym in SPECS:
        x, y = a.day_ticks(sym, d), b.day_ticks(sym, d)
        assert all(np.array_equal(p, q) for p, q in zip(x, y))
    other = MarketModel(6).day_ticks("EURUSD", d)
    assert not np.array_equal(a.day_ticks("EURUSD", d)[1][:50], other[1][:50])
    # asking for days in a different order (cache state) changes nothing
    c = MarketModel(5); c.day_ticks("EURUSD", WEEKDAYS[10]); y = c.day_ticks("EURUSD", d)
    assert np.array_equal(a.day_ticks("EURUSD", d)[0], y[0])


# ------------------------------------------------------------------ no exploitable structure
@pytest.mark.parametrize("sym", list(SPECS))
def test_returns_are_zero_mean_and_serially_uncorrelated(model, sym):
    r = returns(model, sym, WEEKDAYS, 60_000)                       # 1-minute returns in pips
    n = len(r)
    assert n > 20_000
    assert abs(r.mean()) / (r.std() / np.sqrt(n)) < 4                # no drift to ride
    for lag in (1, 2, 3, 5):
        rho = np.corrcoef(r[:-lag], r[lag:])[0, 1]
        assert abs(rho) < 0.04, (sym, lag, rho)                      # no momentum, no mean reversion
    # sign persistence: P(next same sign) ~ 1/2
    same = (np.sign(r[:-1]) == np.sign(r[1:]))[(r[:-1] != 0) & (r[1:] != 0)].mean()
    assert abs(same - 0.5) < 0.02


def test_variance_scales_linearly_with_horizon(model):
    v1 = returns(model, "EURUSD", WEEKDAYS, 60_000).var()
    v5 = returns(model, "EURUSD", WEEKDAYS, 300_000).var()
    v15 = returns(model, "EURUSD", WEEKDAYS, 900_000).var()
    assert 0.85 < v5 / (5 * v1) < 1.15 and 0.85 < v15 / (15 * v1) < 1.2       # variance ratio ~ 1 = random walk


def test_a_momentum_rule_cannot_make_money_but_pays_the_spread(model):
    """Sign of the previous 5 minutes -> trade the next 5. Gross edge ~ 0, so net = -spread."""
    r = returns(model, "EURUSD", WEEKDAYS, 300_000)
    gross = np.sign(r[:-1]) * r[1:]
    z = gross.mean() / (gross.std() / np.sqrt(len(gross)))
    assert abs(z) < 3.5
    t, b, a = model.day_ticks("EURUSD", WEEKDAYS[5])
    spread = ((a - b) / 1e-4).mean()
    assert gross.mean() - spread < -0.4                                   # after costs it loses


def test_daily_volatility_is_in_the_configured_ballpark(model):
    for sym, spec in SPECS.items():
        pip = pip_size(spec.digits)
        days = [d for d in range(D(2026, 1, 5), D(2026, 4, 1)) if (d + 3) % 7 in (1, 2, 3)]       # Tue-Thu: full days
        moves = []
        for d in days:
            t, b, a = model.day_ticks(sym, d)
            m = (b + a) / 2
            moves.append((m[-1] - m[0]) / pip)
        sd = np.std(moves)
        assert 0.6 * spec.daily_vol_pips < sd < 1.5 * spec.daily_vol_pips, (sym, sd)


# ------------------------------------------------------------------ realism
def test_intraday_seasonality(model):
    busy = returns_hours(model, "EURUSD", 8, 11)
    quiet = returns_hours(model, "EURUSD", 20, 23)
    assert quiet > 0 and 2.0 < busy / quiet < 3.5               # designed ~2.6 (NY overlap vs late Asia, in std terms)


def returns_hours(model, sym, h_from, h_to):
    """Std of 5-minute returns during New York hours [h_from, h_to) on 2-6 March 2026 (EST = UTC-5)."""
    out = []
    for d in WEEKDAYS[:5]:
        lo = d * DAY_MS + (h_from + 5) * 3_600_000
        hi = d * DAY_MS + (h_to + 5) * 3_600_000
        t, b, a = model.ticks_between(sym, lo - 3_600_000, hi)          # may spill into the next UTC day
        if not len(t):
            continue
        m = (b + a) / 2
        grid = np.arange(lo, hi, 300_000)
        idx = np.searchsorted(t, grid, "right") - 1
        out.extend(np.diff(m[idx]) / 1e-4)
    return float(np.std(out))


def test_pairs_are_correlated_as_designed(model):
    r = {s: returns(model, s, WEEKDAYS, 300_000) for s in SPECS}
    n = min(len(x) for x in r.values())
    c = lambda a, b: np.corrcoef(r[a][:n], r[b][:n])[0, 1]
    assert 0.35 < c("EURUSD", "GBPUSD") < 0.8                              # designed ~0.59
    assert 0.55 < c("EURJPY", "GBPJPY") < 0.95                             # designed ~0.79
    assert abs(c("EURUSD", "EURJPY")) < c("EURUSD", "GBPUSD")              # weaker across the factor groups


def test_spreads_are_positive_quantised_and_widen_when_liquidity_is_thin(model):
    d = WEEKDAYS[3]
    for sym, spec in SPECS.items():
        t, b, a = model.day_ticks(sym, d)
        assert (a > b).all()
        assert np.array_equal(np.round(b, spec.digits), b) and np.array_equal(np.round(a, spec.digits), a)
        pip = pip_size(spec.digits)
        assert 0.5 * spec.spread_pips < np.median((a - b) / pip) < 2.2 * spec.spread_pips
    t, b, a = model.day_ticks("EURUSD", d)
    ny = ((t // 1000 % 86400) / 3600 - 5) % 24
    sp = (a - b) / 1e-4
    assert sp[(ny >= 17) & (ny < 18.5)].mean() > 1.8 * sp[(ny >= 9) & (ny < 11)].mean()       # rollover is wide


def test_market_is_closed_on_weekends_and_ticks_are_in_order(model):
    sat = D(2026, 3, 7)
    for sym in SPECS:
        assert len(model.day_ticks(sym, sat)[0]) == 0
    t, b, a = model.day_ticks("EURUSD", D(2026, 3, 6))                      # Friday: nothing after 17:00 New York (22:00 UTC, EST)
    assert t.max() < D(2026, 3, 6) * DAY_MS + 22 * 3_600_000
    t, *_ = model.day_ticks("EURUSD", D(2026, 3, 8))                         # Sunday: opens 17:00 NY (DST began that morning -> 21:00 UTC)
    assert len(t) > 0 and t.min() >= D(2026, 3, 8) * DAY_MS + 21 * 3_600_000
    t, *_ = model.day_ticks("EURUSD", WEEKDAYS[2])
    assert (np.diff(t) >= 0).all()
    assert (np.diff(t) == 0).sum() >= 5                                      # ticks can share a millisecond (~50/day expected)


def test_price_is_continuous_across_midnight(model):
    for sym, spec in SPECS.items():
        pip = pip_size(spec.digits)
        for d in WEEKDAYS[1:4]:                                                # Tue-Thu: the next day is also a trading day
            t0, b0, a0 = model.day_ticks(sym, d)
            t1, b1, a1 = model.day_ticks(sym, d + 1)
            jump = abs((b1[0] + a1[0]) / 2 - (b0[-1] + a0[-1]) / 2) / pip
            assert jump < 12, (sym, d, jump)                                 # ~ a few seconds of Asian trading, not a daily gap


def test_price_levels_are_sensible_for_2026(model):
    bands = {"EURUSD": (0.85, 1.35), "GBPUSD": (1.0, 1.6), "EURJPY": (130, 200), "GBPJPY": (150, 240)}
    for sym, (lo, hi) in bands.items():
        t, b, a = model.day_ticks(sym, D(2026, 6, 10))
        assert lo < b.min() and a.max() < hi, sym


def test_tick_rate_is_realistic(model):
    t, *_ = model.day_ticks("EURUSD", WEEKDAYS[2])
    assert 40_000 < len(t) < 150_000


def test_seq_within_ms():
    t = np.array([5, 5, 5, 6, 7, 7])
    assert seq_within_ms(t).tolist() == [0, 1, 2, 0, 0, 1] and len(seq_within_ms(t[:0])) == 0


# ------------------------------------------------------------------ history == live
def test_bulk_history_equals_what_the_live_collector_serves(tmp_path):
    """make_history and `collector --sim` must produce identical rows, so they can be chained."""
    cfg = load_config(); cfg.symbols = ["EURUSD", "GBPJPY"]
    end_day = "2026-03-04"
    hist = tmp_path / "hist.db"
    import tools.make_history as mh
    cfgpath = tmp_path / "c.yaml"
    cfgpath.write_text("symbols: [EURUSD, GBPJPY]\n")
    assert make_history(["--config", str(cfgpath), "--db", str(hist), "--weeks", "0.3", "--end", end_day, "--seed", "9"]) == 0
    h = db.connect(hist)
    assert db.get_state(h, "offset_hours") == "3" and db.get_state(h, "digits:GBPJPY") == "3"

    # the same window through the real collector + live client, with a clock standing at the end of that day
    t_end = int(datetime(2026, 3, 4, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
    first = h.execute("SELECT MIN(t_ms) FROM ticks").fetchone()[0]
    live = db.connect(tmp_path / "live.db")
    cfg.collector.backfill_hours_on_start = (t_end - first) / 3_600_000 + 1
    cfg.collector.backfill_chunk_minutes = 360
    col = Collector(cfg, live, MarketSimClient(lambda: t_end, 3, seed=9), clock=lambda: t_end)
    col.start()
    col.poll_once()
    for sym in cfg.symbols:
        q = "SELECT t_ms, seq, bid, ask FROM ticks WHERE symbol=? AND t_ms >= ? AND t_ms <= ? ORDER BY t_ms, seq"
        a = h.execute(q, (sym, first, t_end)).fetchall()
        b = live.execute(q, (sym, first, t_end)).fetchall()
        assert len(a) > 20_000 and a == b, sym

    # and the collector can continue from the history database's resume cursors with nothing new in the overlap
    cont = Collector(cfg, h, MarketSimClient(lambda: t_end, 3, seed=9), clock=lambda: t_end)
    before = h.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    cont.start(); cont.poll_once()
    assert h.execute("SELECT COUNT(*) FROM ticks").fetchone()[0] == before
