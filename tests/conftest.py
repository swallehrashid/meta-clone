import shutil
from datetime import datetime, timezone

import pytest

from collector.collector import Collector
from collector.sim_client import SimClient
from core import db
from core.config import load_config

T0 = int(datetime(2025, 3, 4, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)  # Tue, open
LIVE_END = T0 + 600_000      # the collector has data up to here


class FakeClock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


def make_cfg(symbols=("EURUSD", "EURJPY")):
    cfg = load_config()
    cfg.symbols = list(symbols)
    cfg.collector.backfill_hours_on_start = 1.5
    cfg.collector.backfill_chunk_minutes = 30
    cfg.server.heartbeat_seconds = 0.05
    return cfg


@pytest.fixture(scope="session")
def tick_db_template(tmp_path_factory):
    """Collected once: 90 min of history before T0 plus 10 live minutes, via the real collector."""
    path = tmp_path_factory.mktemp("tickdb") / "ticks.db"
    cfg = make_cfg()
    clock = FakeClock(T0)
    sim = SimClient(clock, offset_hours=3)
    conn = db.connect(path)
    col = Collector(cfg, conn, sim, clock)
    col.start()
    while clock.t < LIVE_END:
        clock.t += 1000
        col.poll_once()
    conn.close()
    return path


@pytest.fixture
def tick_db(tick_db_template, tmp_path):
    """A private writable copy for each test."""
    dst = tmp_path / "ticks.db"
    shutil.copy(tick_db_template, dst)
    return dst


@pytest.fixture
def cfg():
    return make_cfg()
