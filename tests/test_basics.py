"""config / pips / db / market_hours / time_offset"""
from datetime import datetime, timezone

import pytest

from collector.market_hours import market_open
from collector.time_offset import OffsetTracker, estimate_offset_hours, MS_PER_HOUR
from core import db
from core.config import load_config
from core.pips import pip_size, to_pips


def utc_ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


# ---- config
def test_config_loads_and_applies_suffix():
    cfg = load_config()
    assert cfg.symbols[:2] == ["EURUSD", "GBPUSD"]
    assert cfg.delay.presets_seconds == [60, 300, 900]
    assert cfg.grader.fixed_mode.enabled is False
    cfg2 = cfg.model_copy(update={"symbol_suffix": "m"})
    assert cfg2.broker_symbols[0] == "EURUSDm"


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("symbols: [EURUSD]\nbogus: 1\n")
    with pytest.raises(Exception):
        load_config(p)


# ---- pips
@pytest.mark.parametrize("digits,expected", [(5, 1e-4), (4, 1e-4), (3, 1e-2), (2, 1e-2)])
def test_pip_size(digits, expected):
    assert pip_size(digits) == pytest.approx(expected)


def test_to_pips():
    assert to_pips(0.00012, 5) == pytest.approx(1.2)
    assert to_pips(0.012, 3) == pytest.approx(1.2)


# ---- db
def test_db_dedup_and_wal(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    rows = [("EURUSD", 1000, 1.1, 1.2, 0, 0), ("EURUSD", 1000, 1.1, 1.2, 0, 1)]
    assert db.insert_ticks(conn, rows) == 2
    assert db.insert_ticks(conn, rows) == 0          # exact repeat ignored
    assert db.last_tick_ms(conn, "EURUSD") == 1000
    db.set_state(conn, "k", "1"); db.set_state(conn, "k", "2")
    assert db.get_state(conn, "k") == "2"


# ---- market hours (NY: Sun 17:00 open -> Fri 17:00 close). Mar 2025 is EDT (UTC-4).
def test_market_hours():
    assert market_open(utc_ms(2025, 3, 4, 14))            # Tue
    assert not market_open(utc_ms(2025, 3, 8, 12))        # Sat
    assert market_open(utc_ms(2025, 3, 14, 20, 59))       # Fri 16:59 NY (EDT)
    assert not market_open(utc_ms(2025, 3, 14, 21, 0))    # Fri 17:00 NY (EDT)
    assert market_open(utc_ms(2025, 3, 7, 21, 59))        # Fri 16:59 NY (still EST)
    assert not market_open(utc_ms(2025, 3, 7, 22, 0))     # Fri 17:00 NY (EST)
    assert not market_open(utc_ms(2025, 3, 9, 20, 59))    # Sun 16:59 NY (EDT began that day)
    assert market_open(utc_ms(2025, 3, 9, 21, 0))         # Sun 17:00 NY


# ---- offset estimation
def test_offset_estimate_fresh_tick():
    now = utc_ms(2025, 3, 4, 14, 0, 0)
    assert estimate_offset_hours(now + 2 * MS_PER_HOUR - 800, now) == 2
    assert estimate_offset_hours(now + 3 * MS_PER_HOUR - 4000, now) == 3
    assert estimate_offset_hours(now, now) == 0


def test_offset_estimate_rejects_stale_tick():
    now = utc_ms(2025, 3, 4, 14, 0, 0)
    stale = now + 3 * MS_PER_HOUR - 25 * 60_000     # 25 minutes old -> ambiguous
    assert estimate_offset_hours(stale, now) is None


def test_tracker_reports_change_and_ignores_closed_market():
    tr = OffsetTracker(recheck_seconds=3600)
    now = utc_ms(2025, 3, 4, 14)
    assert tr.due(now)
    assert tr.update(now + 2 * MS_PER_HOUR, now, True) == (False, None, 2)
    assert not tr.due(now + 1000)
    later = now + 3600_000
    assert tr.due(later)
    assert tr.update(later + 3 * MS_PER_HOUR, later, False) == (False, 2, 2)  # closed: untouched
    assert tr.offset_hours == 2
    assert tr.update(later + 3 * MS_PER_HOUR, later, True) == (True, 2, 3)
