"""No-lookahead guarantees of DataView (hard rule 1)."""
import pytest

from core import db
from core.dataview import DataView, LookaheadError

CUT = 1_700_000_000_000


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    rows = []
    # ticks at CUT-2000, CUT-1000, two at CUT (seq 0,1), and "future" ticks at CUT+1, CUT+5000
    for t, seq, bid in [(CUT - 2000, 0, 1.1000), (CUT - 1000, 0, 1.1010), (CUT, 0, 1.1020),
                        (CUT, 1, 1.1030), (CUT + 1, 0, 9.9), (CUT + 5000, 0, 9.9)]:
        rows.append(("EURUSD", t, bid, bid + 0.0002, 0, seq))
    db.insert_ticks(c, rows)
    return c


def test_raises_when_end_is_past_cutoff(conn):
    dv = DataView(conn, "EURUSD", CUT)
    with pytest.raises(LookaheadError):
        dv.ticks(CUT - 5000, CUT + 1)


def test_raises_when_start_is_past_cutoff(conn):
    dv = DataView(conn, "EURUSD", CUT)
    with pytest.raises(LookaheadError):
        dv.ticks(CUT + 1)
    with pytest.raises(LookaheadError):
        dv.ticks(CUT + 1, CUT + 2)


def test_raises_when_cursor_is_past_cutoff(conn):
    with pytest.raises(LookaheadError):
        DataView(conn, "EURUSD", CUT).ticks_after((CUT + 1, 0))


def test_cannot_widen_a_view(conn):
    dv = DataView(conn, "EURUSD", CUT)
    with pytest.raises(LookaheadError):
        dv.with_cutoff(CUT + 1)
    assert dv.with_cutoff(CUT - 1500).last_tick()[0] == CUT - 2000   # narrowing is fine


def test_default_reads_stop_exactly_at_cutoff_inclusive(conn):
    dv = DataView(conn, "EURUSD", CUT)
    got = dv.ticks(0)
    assert [r[0] for r in got] == [CUT - 2000, CUT - 1000, CUT, CUT]
    assert max(r[2] for r in got) < 2                      # the 9.9 future prices never appear
    assert dv.last_tick() == (CUT, 1, 1.1030, pytest.approx(1.1032))
    assert [r[:2] for r in dv.ticks_after(None)] == [(CUT - 2000, 0), (CUT - 1000, 0), (CUT, 0), (CUT, 1)]
    assert dv.ticks_after((CUT, 1)) == []                  # nothing newer is visible
    assert [r[:2] for r in dv.ticks_after((CUT - 1000, 0))] == [(CUT, 0), (CUT, 1)]


def test_candles_ignore_future_ticks(conn):
    # future tick has a huge price inside the SAME M1 bucket as the cutoff
    base = CUT - CUT % 60_000
    c = db.connect(":memory:")
    db.insert_ticks(c, [("EURUSD", base + 1000, 1.10, 1.1002, 0, 0),
                        ("EURUSD", base + 2000, 1.11, 1.1102, 0, 0),
                        ("EURUSD", base + 3000, 9.99, 9.9902, 0, 0)])
    out = DataView(c, "EURUSD", base + 2500).candles("M1", 5)
    assert len(out) == 1 and out[0].h == 1.11 and out[0].c == 1.11 and out[0].n == 2


def test_candle_cache_cannot_leak_future_into_an_earlier_view():
    """A later view caches the full candle; an earlier view must still see only its part."""
    from core.candles import CandleCache
    base = 1_700_000_040_000 - 1_700_000_040_000 % 60_000
    c = db.connect(":memory:")
    db.insert_ticks(c, [("EURUSD", base + 1000, 1.10, 1.1002, 0, 0),
                        ("EURUSD", base + 40_000, 1.50, 1.5002, 0, 0),
                        ("EURUSD", base + 70_000, 1.20, 1.2002, 0, 0),
                        ("EURUSD", base + 130_000, 1.30, 1.3002, 0, 0)])
    cache = CandleCache()
    DataView(c, "EURUSD", base + 125_000, cache).candles("M1", 5)     # caches the 1.50 candle
    early = DataView(c, "EURUSD", base + 20_000, cache).candles("M1", 5)
    assert len(early) == 1 and early[0].h == 1.10
