"""The verify tool must pass on good data and fail loudly on bad data."""
from datetime import datetime, timezone

import pytest

from collector.collector import Collector
from collector.sim_client import SimClient
from collector.verify import verify_symbol
from core import db
from core.config import load_config


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


@pytest.fixture
def populated():
    cfg = load_config(); cfg.symbols = ["EURUSD"]
    cfg.collector.backfill_hours_on_start = 0.02
    cfg.collector.backfill_chunk_minutes = 1
    t0 = ms(2025, 3, 4, 14, 0, 0)
    clock = Clock(t0)
    sim = SimClient(clock, offset_hours=3)
    conn = db.connect(":memory:")
    col = Collector(cfg, conn, sim, clock); col.start()
    while clock.t < t0 + 90_000:
        clock.t += 250; col.poll_once()
    first = conn.execute("SELECT MIN(t_ms) FROM ticks").fetchone()[0]
    return conn, sim, first, clock.t - 2000


def test_passes_on_good_data(populated):
    conn, sim, a, b = populated
    r = verify_symbol(conn, sim, "EURUSD", a, b, 3, seed=1)
    assert r.ok, r
    assert r.stored == r.broker > 100 and r.spot_checked == 10


def test_fails_when_ticks_missing(populated):
    conn, sim, a, b = populated
    with conn:
        conn.execute("DELETE FROM ticks WHERE (symbol,t_ms,seq) IN "
                     "(SELECT symbol,t_ms,seq FROM ticks ORDER BY t_ms,seq LIMIT 40 OFFSET 50)")
    r = verify_symbol(conn, sim, "EURUSD", a, b, 3, seed=1)
    assert r.missing_from_db == 40 and not r.ok


def test_fails_when_a_price_is_wrong(populated):
    conn, sim, a, b = populated
    with conn:
        conn.execute("UPDATE ticks SET bid = bid + 0.0001")
    r = verify_symbol(conn, sim, "EURUSD", a, b, 3, seed=1)
    assert r.spot_mismatches and not r.ok


def test_fails_when_timestamps_are_shifted_an_hour(populated):
    conn, sim, a, b = populated
    r = verify_symbol(conn, sim, "EURUSD", a, b, 2, seed=1)   # wrong offset assumption
    assert not r.ok
