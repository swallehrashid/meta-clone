import pandas as pd
import pytest

from core import db
from core.candles import CandleBuilder, build_candles, bucket_open, tf_ms
from core.dataview import DataView
from core.pips import pip_size
from tests.conftest import LIVE_END, T0

MIN = 60_000


def tk(t, bid, ask=None, seq=0):
    return (t, seq, bid, bid + 0.0002 if ask is None else ask)


def test_ohlc_count_and_avg_spread():
    ticks = [tk(T0 + 1000, 1.1000, 1.1002), tk(T0 + 2000, 1.1010, 1.1013),
             tk(T0 + 3000, 1.0990, 1.0992), tk(T0 + 59_999, 1.1005, 1.1008)]
    (c,) = build_candles(ticks, "M1")
    assert (c.t_ms, c.o, c.h, c.l, c.c, c.n) == (T0, 1.1000, 1.1010, 1.0990, 1.1005, 4)
    assert c.avg_spread == pytest.approx((0.0002 + 0.0003 + 0.0002 + 0.0003) / 4)


def test_boundaries_align_to_utc():
    # tick exactly on the boundary belongs to the NEW candle; one ms earlier to the old
    ticks = [tk(T0 + MIN - 1, 1.1), tk(T0 + MIN, 1.2)]
    a, b = build_candles(ticks, "M1")
    assert (a.t_ms, a.c) == (T0, 1.1) and (b.t_ms, b.o) == (T0 + MIN, 1.2)
    # 14:00 is an M5/M15/H1 boundary; 14:07 falls in 14:05 / 14:00 / 14:00
    t = T0 + 7 * MIN
    assert bucket_open(t, "M5") == T0 + 5 * MIN
    assert bucket_open(t, "M15") == T0
    assert bucket_open(t, "H1") == T0
    assert bucket_open(T0 - 1, "H1") == T0 - 3_600_000


def test_ask_side_option():
    (c,) = build_candles([tk(T0, 1.1000, 1.1005), tk(T0 + 1, 1.1010, 1.1020)], "M1", side="ask")
    assert (c.o, c.c) == (1.1005, 1.1020)


def test_unknown_timeframe():
    with pytest.raises(ValueError):
        tf_ms("W1")


def test_ticks_sharing_a_timestamp_keep_order():
    (c,) = build_candles([tk(T0, 1.10, seq=0), tk(T0, 1.30, seq=1), tk(T0, 1.20, seq=2)], "M1")
    assert (c.o, c.h, c.l, c.c, c.n) == (1.10, 1.30, 1.10, 1.20, 3)


def test_incremental_builder_equals_batch_over_real_ticks(tick_db):
    conn = db.connect(tick_db)
    rows = conn.execute("SELECT t_ms,seq,bid,ask FROM ticks WHERE symbol='EURUSD' "
                        "ORDER BY t_ms,seq").fetchall()
    for tf in ("M1", "M5", "M15", "H1"):
        b = CandleBuilder(tf)
        final = {}
        for i in range(0, len(rows), 137):                # arbitrary batch sizes
            for c in b.add(rows[i:i + 137]):
                final[c.t_ms] = c
        assert [final[k] for k in sorted(final)] == build_candles(rows, tf)


def test_candles_match_independent_pandas_resample(tick_db):
    """Cross-check against pandas' own resampling (stand-in for MT5 on a machine without it).
    Every OHLC value must agree within 0.1 pip - in fact exactly."""
    conn = db.connect(tick_db)
    for sym in ("EURUSD", "EURJPY"):
        pip = pip_size(db.get_digits(conn, sym))
        df = pd.read_sql_query("SELECT t_ms, bid FROM ticks WHERE symbol=? ORDER BY t_ms, seq",
                               conn, params=(sym,))
        df.index = pd.to_datetime(df.t_ms, unit="ms")
        dv = DataView(conn, sym, LIVE_END)
        for tf, rule in (("M1", "1min"), ("M5", "5min"), ("M15", "15min"), ("H1", "60min")):
            ref = df.bid.resample(rule, origin="epoch", label="left", closed="left").ohlc().dropna()
            mine = dv.candles(tf, 10_000)
            assert len(mine) == len(ref), (sym, tf)
            for c, (ts, r) in zip(mine, ref.iterrows()):
                assert c.t_ms == int(ts.timestamp() * 1000)
                for got, want in ((c.o, r.open), (c.h, r.high), (c.l, r.low), (c.c, r.close)):
                    assert abs(got - want) / pip < 0.1


def test_candles_returns_exactly_n_most_recent(tick_db):
    cutoff = LIVE_END - 30_000                                            # mid-minute
    out = DataView(db.connect(tick_db), "EURUSD", cutoff).candles("M1", 20)
    assert len(out) == 20
    assert out[-1].t_ms == cutoff - cutoff % MIN                          # forming candle last
    assert [c.t_ms for c in out] == sorted({c.t_ms for c in out})


def test_cache_gives_same_answer_cold_warm_and_for_earlier_cutoffs(tick_db):
    conn = db.connect(tick_db)
    from core.candles import CandleCache
    cache = CandleCache()
    late = DataView(conn, "EURUSD", LIVE_END, cache)
    early = DataView(conn, "EURUSD", T0 - 5 * MIN - 17_000, cache)
    cold = late.candles("M1", 60)
    warm = late.candles("M1", 60)
    assert cold == warm
    # an earlier view sharing the (now warm) cache must still see its own partial last candle
    fresh = DataView(conn, "EURUSD", T0 - 5 * MIN - 17_000, CandleCache()).candles("M1", 60)
    assert early.candles("M1", 60) == fresh
    # callers cannot corrupt the cache by mutating what they got
    warm[-1].h = 99.0
    assert late.candles("M1", 60)[-1].h != 99.0


def test_empty_buckets_are_skipped_and_n_still_filled():
    """A gap (weekend-like) must not leave the result short while older candles exist."""
    conn = db.connect(":memory:")
    rows = []
    for k in (0, 1, 2, 3, 4):                         # 5 candles, then 3 days of nothing
        rows.append(("EURUSD", T0 + k * MIN, 1.1 + k / 1e4, 1.1002 + k / 1e4, 0, 0))
    rows.append(("EURUSD", T0 + 3 * 86_400_000, 1.2, 1.2002, 0, 0))
    db.insert_ticks(conn, rows)
    dv = DataView(conn, "EURUSD", T0 + 3 * 86_400_000 + 5000)
    out = dv.candles("M1", 6)
    assert len(out) == 6 and out[-1].o == 1.2 and out[0].t_ms == T0
