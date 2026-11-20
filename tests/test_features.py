import pytest

from core import db
from core.dataview import DataView
from grader.features import FEATURES, compute_features, minutes_since_open
from tests.conftest import LIVE_END, T0, make_cfg
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MIN = 60_000
PIP = 1e-4
NY = ZoneInfo("America/New_York")


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


def put(conn, spec, symbol="EURUSD", spread=0.0002):
    """spec: [(t_ms, bid)] -> ticks with a fixed spread; same-ms ticks get increasing seq."""
    seq, prev, rows = 0, None, []
    for t, bid in spec:
        seq = seq + 1 if t == prev else 0
        prev = t
        rows.append((symbol, t, bid, round(bid + spread, 5), 0, seq))
    db.insert_ticks(conn, rows)


def candle_ticks(minute_start, o, h, l, c):
    return [(minute_start + 1_000, o), (minute_start + 20_000, h),
            (minute_start + 40_000, l), (minute_start + 58_000, c)]


@pytest.fixture
def hand(cfg):
    """Three completed minutes (14:00, 14:01, 14:02) then the entry 20 s into 14:03."""
    conn = db.connect(":memory:")
    spec = (candle_ticks(T0, 1.1000, 1.1010, 1.0995, 1.1005)
            + candle_ticks(T0 + MIN, 1.1005, 1.1020, 1.1000, 1.1015)
            + candle_ticks(T0 + 2 * MIN, 1.1015, 1.1018, 1.1002, 1.1004)
            + [(T0 + 3 * MIN + 5_000, 1.1006), (T0 + 3 * MIN + 15_000, 1.1007)])
    put(conn, spec)
    db.set_state(conn, "digits:EURUSD", "5")
    return conn, T0 + 3 * MIN + 20_000


def feats(conn, t, cfg, symbol="EURUSD"):
    return compute_features(DataView(conn, symbol, t), cfg, 5)


def test_all_starter_features_are_registered():
    assert set(FEATURES) == {
        "spread_pips", "minutes_since_ny_open", "minutes_since_london_open", "last_m1_direction",
        "last_m1_range_pips", "last_m1_body_pct", "m1_closed_above_prev_high",
        "m1_closed_below_prev_low", "dist_to_session_high_pips", "dist_to_session_low_pips",
        "ticks_last_60s", "range_last_15m_pips"}


def test_hand_computed_values(hand, cfg):
    conn, E = hand
    f = feats(conn, E, cfg)
    assert f["spread_pips"] == pytest.approx(2.0)
    # 14:02 candle: o 1.1015 h 1.1018 l 1.1002 c 1.1004
    assert f["last_m1_direction"] == -1.0
    assert f["last_m1_range_pips"] == pytest.approx(16.0)
    assert f["last_m1_body_pct"] == pytest.approx(11 / 16 * 100)
    assert f["m1_closed_above_prev_high"] == 0.0 and f["m1_closed_below_prev_low"] == 0.0
    # window (E-60s, E] = (2:20.000, 3:20.000]: ticks at 2:40, 2:58, 3:05, 3:15 (the 2:20.000
    # high tick sits exactly on the open edge and is excluded)
    assert f["ticks_last_60s"] == 4.0
    # 15 min range of bid: low 1.0995, high 1.1020
    assert f["range_last_15m_pips"] == pytest.approx(25.0)
    # session (london 03:00 NY = 08:00 UTC) high/low over everything stored; last bid 1.1007
    assert f["dist_to_session_high_pips"] == pytest.approx((1.1020 - 1.1007) / PIP)
    assert f["dist_to_session_low_pips"] == pytest.approx((1.1007 - 1.0995) / PIP)
    # T0 = 14:00 UTC = 09:00 NY (EST): NY open 08:00 -> 60 min + 3:20 ; London 03:00 -> 360 + 3:20
    assert f["minutes_since_ny_open"] == pytest.approx(63 + 20 / 60)
    assert f["minutes_since_london_open"] == pytest.approx(363 + 20 / 60)


def test_breakout_flags():
    cfg = make_cfg()
    conn = db.connect(":memory:")
    # 14:01 closes above 14:00's high
    put(conn, candle_ticks(T0, 1.1000, 1.1010, 1.0995, 1.1005)
        + candle_ticks(T0 + MIN, 1.1005, 1.1030, 1.1004, 1.1020) + [(T0 + 2 * MIN + 1000, 1.1020)])
    f = feats(conn, T0 + 2 * MIN + 2000, cfg)
    assert f["m1_closed_above_prev_high"] == 1.0 and f["m1_closed_below_prev_low"] == 0.0
    assert f["last_m1_direction"] == 1.0
    conn = db.connect(":memory:")
    put(conn, candle_ticks(T0, 1.1000, 1.1010, 1.0995, 1.1005)
        + candle_ticks(T0 + MIN, 1.1005, 1.1006, 1.0980, 1.0990) + [(T0 + 2 * MIN + 1000, 1.0990)])
    f = feats(conn, T0 + 2 * MIN + 2000, cfg)
    assert f["m1_closed_below_prev_low"] == 1.0 and f["m1_closed_above_prev_high"] == 0.0


def test_empty_previous_minute_gives_no_candle_features_not_stale_ones():
    cfg = make_cfg()
    conn = db.connect(":memory:")
    put(conn, candle_ticks(T0, 1.1000, 1.1010, 1.0995, 1.1005) + [(T0 + 2 * MIN + 1000, 1.1005)])
    f = feats(conn, T0 + 2 * MIN + 2000, cfg)          # 14:01 had no ticks
    for name in ("last_m1_direction", "last_m1_range_pips", "last_m1_body_pct",
                 "m1_closed_above_prev_high", "m1_closed_below_prev_low"):
        assert name not in f
    assert "spread_pips" in f and "ticks_last_60s" in f


def test_doji_has_direction_zero_and_body_zero():
    cfg = make_cfg()
    conn = db.connect(":memory:")
    put(conn, [(T0 + 1000, 1.1000), (T0 + 2000, 1.1000), (T0 + MIN + 500, 1.1000)])
    f = feats(conn, T0 + MIN + 900, cfg)
    assert f["last_m1_direction"] == 0.0 and f["last_m1_body_pct"] == 0.0


def test_minutes_since_open_is_dst_aware():
    # NY open 08:00. Sun 2025-03-09 02:00 EST->EDT.
    assert minutes_since_open(ms(2025, 3, 10, 12, 30), "08:00", NY) == pytest.approx(30)   # EDT
    assert minutes_since_open(ms(2025, 3, 4, 13, 30), "08:00", NY) == pytest.approx(30)    # EST
    # 07:00 EDT Sunday: latest open was Saturday 08:00 EST (13:00 UTC) = 22 h earlier, not 23
    assert minutes_since_open(ms(2025, 3, 9, 11, 0), "08:00", NY) == pytest.approx(22 * 60)
    # exactly at the open -> 0, one minute before -> ~24 h
    assert minutes_since_open(ms(2025, 3, 10, 12, 0), "08:00", NY) == 0
    assert minutes_since_open(ms(2025, 3, 10, 11, 59), "08:00", NY) == pytest.approx(1439)


# --------------------------------------------------------------- no lookahead
class LoggingView(DataView):
    """Records the latest timestamp of every request and every row handed back."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.log = []

    def _seen(self, *ts):
        self.log.extend(t for t in ts if t is not None)

    def ticks(self, start_ms, end_ms=None):
        out = super().ticks(start_ms, end_ms)
        self._seen(start_ms, end_ms, *(r[0] for r in out)); return out

    def ticks_after(self, key, limit=5000):
        out = super().ticks_after(key, limit)
        self._seen(key[0] if key else None, *(r[0] for r in out)); return out

    def last_tick(self):
        r = super().last_tick(); self._seen(r[0] if r else None); return r

    def count_ticks(self, s, e=None):
        self._seen(s, e); return super().count_ticks(s, e)

    def bid_extremes(self, s, e=None):
        self._seen(s, e); return super().bid_extremes(s, e)

    def candles(self, tf, n, **kw):
        out = super().candles(tf, n, **kw); self._seen(*(c.t_ms for c in out)); return out


def test_features_never_read_past_the_entry_time(tick_db, cfg):
    conn = db.connect(tick_db)
    checked = 0
    for entry in range(T0 - 30 * MIN + 17_000, T0 + 5 * MIN, 3 * MIN + 13_000):
        for sym in cfg.broker_symbols:
            dv = LoggingView(conn, sym, entry)
            f = compute_features(dv, cfg, db.get_digits(conn, sym))
            assert len(f) >= 10
            assert dv.log and max(dv.log) <= entry, (sym, entry, max(dv.log) - entry)
            checked += 1
    assert checked >= 10


def test_poisoning_the_future_changes_nothing(tick_db, cfg):
    """Insert absurd ticks AFTER the entry time (inside the forming minute and beyond); every
    feature must be bit-for-bit identical."""
    conn = db.connect(tick_db)
    entry = T0 + 4 * MIN + 30_000
    before = {s: compute_features(DataView(conn, s, entry), cfg, db.get_digits(conn, s))
              for s in cfg.broker_symbols}
    rows = []
    for s in cfg.broker_symbols:
        for k, dt in enumerate((1, 500, 5_000, 29_000, 90_000)):
            rows.append((s, entry + dt, 999.0, 999.5, 0, 0)); rows.append((s, entry + dt, 0.001, 0.002, 0, 1))
    db.insert_ticks(conn, rows)
    for s in cfg.broker_symbols:
        after = compute_features(DataView(conn, s, entry), cfg, db.get_digits(conn, s))
        assert after == before[s]
