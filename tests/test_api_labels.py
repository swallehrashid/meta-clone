import pytest
from starlette.testclient import TestClient

from core import db
from grader.labeler_worker import Labeler
from server.app import create_app
from tests.conftest import LIVE_END, FakeClock

MIN = 60_000


@pytest.fixture
def api(tick_db, cfg):
    Labeler(cfg, db.connect(tick_db)).run_once()
    clock = FakeClock(LIVE_END)
    with TestClient(create_app(cfg, tick_db, clock)) as c:
        yield c, clock


def test_labels_are_clamped_to_the_delayed_clock(api):
    c, clock = api
    r = c.get("/labels", params={"symbol": "EURUSD"}).json()           # default delay 300 s
    assert r["cutoff_ms"] == clock.t - 300_000
    assert r["count"] > 50 and max(l["entry_t_ms"] for l in r["labels"]) <= r["cutoff_ms"]
    # asking for newer entries explicitly is clamped, not honoured
    r2 = c.get("/labels", params={"symbol": "EURUSD", "t_to": clock.t}).json()
    assert max(l["entry_t_ms"] for l in r2["labels"]) <= clock.t - 300_000


def test_zero_delay_needs_explicit_peek(api):
    c, clock = api
    assert c.get("/labels", params={"symbol": "EURUSD", "delay": 0}).status_code == 403
    assert c.get("/features", params={"symbol": "EURUSD", "delay": 0}).status_code == 403
    assert c.get("/labels", params={"symbol": "EURUSD", "delay": -5}).status_code == 400     # malformed, not "needs peek"
    r = c.get("/labels", params={"symbol": "EURUSD", "delay": 0, "peeked": 1}).json()
    assert max(l["entry_t_ms"] for l in r["labels"]) > clock.t - 300_000       # live edge visible


def test_filters_and_shape(api):
    c, clock = api
    lo, hi = clock.t - 20 * MIN, clock.t - 10 * MIN
    r = c.get("/labels", params={"symbol": "EURUSD", "t_from": lo, "t_to": hi, "direction": "buy",
                                 "status": "final", "delay": 60}).json()
    assert r["count"] > 0
    for l in r["labels"]:
        assert lo <= l["entry_t_ms"] <= hi and l["direction"] == "buy" and l["status"] == "final"
    assert set(r["labels"][0]) == set(db.LABEL_COLS)
    assert c.get("/labels", params={"symbol": "NOPE"}).status_code == 404
    assert c.get("/labels", params={"symbol": "EURUSD", "direction": "up"}).status_code == 400
    assert c.get("/labels", params={"symbol": "EURUSD", "limit": 5}).json()["count"] == 5


def test_features_endpoint(api):
    c, clock = api
    r = c.get("/features", params={"symbol": "EURJPY", "names": "spread_pips,ticks_last_60s",
                                   "t_from": clock.t - 30 * MIN, "t_to": clock.t - 20 * MIN}).json()
    assert r["count"] > 5
    for row in r["rows"]:
        assert set(row["features"]) <= {"spread_pips", "ticks_last_60s"}
        assert clock.t - 30 * MIN <= row["t_ms"] <= clock.t - 20 * MIN
    full = c.get("/features", params={"symbol": "EURUSD", "limit": 3}).json()["rows"][0]["features"]
    assert len(full) >= 10
