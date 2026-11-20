"""Delay stream + peek, exercised over real websocket connections (starlette TestClient)."""
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core import db
from core.candles import build_candles
from core.dataview import DataView
from server.app import create_app
from server.stream import StreamError, StreamSession
from core.candles import CandleCache
from tests.conftest import LIVE_END, FakeClock


@pytest.fixture
def env(tick_db, cfg):
    clock = FakeClock(LIVE_END)
    app = create_app(cfg, tick_db, clock, interval_s=0.02)
    with TestClient(app) as client:
        yield client, clock, tick_db


def recv_until(ws, pred, limit=400):
    seen = []
    for _ in range(limit):
        m = ws.receive_json()
        seen.append(m)
        if pred(m):
            return m, seen
    raise AssertionError(f"condition never met; last messages: {seen[-3:]}")


def backfills(ws, n):
    out = {}
    while len(out) < n:
        m = ws.receive_json()
        if m["type"] == "backfill":
            out[m["symbol"]] = m
    return out


# ---------------------------------------------------------------- backfill & delay
def test_cutoff_is_exactly_now_minus_delay_and_nothing_newer_is_sent(env):
    client, clock, path = env
    with client.websocket_connect("/ws/stream?symbols=EURUSD,EURJPY&delay=300&tf=M1") as ws:
        bf = backfills(ws, 2)
        conn = db.connect(path)
        for sym, m in bf.items():
            cutoff = clock.t - 300_000
            assert m["cutoff_ms"] == cutoff
            have = DataView(conn, sym, cutoff).candles("M1", 300)
            assert len(m["candles"]) == len(have) >= 90          # all history up to the cutoff
            assert m["candles"][-1]["time"] * 1000 <= cutoff           # no candle opens past cutoff
            # newest candle content equals a build from ticks <= cutoff only
            want = DataView(conn, sym, cutoff).candles("M1", 1)[0]
            got = m["candles"][-1]
            assert (got["open"], got["high"], got["low"], got["close"], got["ticks"]) == \
                   (want.o, want.h, want.l, want.c, want.n)
            # newest visible tick is the newest tick <= cutoff: 5 min behind the live edge
            nxt = conn.execute("SELECT MIN(t_ms) FROM ticks WHERE symbol=? AND t_ms>?",
                               (sym, cutoff)).fetchone()[0]
            last = DataView(conn, sym, cutoff).last_tick()[0]
            assert last <= cutoff < nxt
            assert LIVE_END - last >= 300_000                           # at least 5 min behind MT5


def test_live_updates_deliver_every_tick_once_in_order_and_never_past_cutoff(env):
    client, clock, path = env
    conn = db.connect(path)
    with client.websocket_connect("/ws/stream?symbols=EURUSD&delay=300&tf=M1") as ws:
        bf = backfills(ws, 1)["EURUSD"]
        start = bf["cutoff_ms"]
        clock.t += 7_300                                              # the market moves on 7.3 s
        end = clock.t - 300_000
        got = []
        seen_cutoff = []
        while not got or got[-1][0] < DataView(conn, "EURUSD", end).last_tick()[0]:
            m, _ = recv_until(ws, lambda x: x["type"] == "update")
            seen_cutoff.append(m["cutoff_ms"])
            got.extend(m["symbols"]["EURUSD"]["ticks"])
        want = [[t, b, a] for t, _s, b, a in DataView(conn, "EURUSD", end).ticks(start + 1)]
        assert got == want                                            # no gaps, no dupes, in order
        assert max(t for t, _, _ in got) <= end                       # never past the cutoff
        assert all(c <= end for c in seen_cutoff)


def test_stream_built_candle_equals_batch_candle(env):
    client, clock, path = env
    conn = db.connect(path)
    with client.websocket_connect("/ws/stream?symbols=EURUSD&delay=60&tf=M5") as ws:
        bf = backfills(ws, 1)["EURUSD"]
        live = {c["time"]: c for c in bf["candles"]}
        clock.t += 90_000                                             # cross a few minute edges
        end = clock.t - 60_000
        while True:
            m, _ = recv_until(ws, lambda x: x["type"] == "update")
            for c in m["symbols"]["EURUSD"]["candles"]:
                live[c["time"]] = c
            if m["cutoff_ms"] >= end and DataView(conn, "EURUSD", end).last_tick()[0] <= \
                    max(t for t in [m["symbols"]["EURUSD"]["ticks"][-1][0]]):
                break
        ref = DataView(conn, "EURUSD", end).candles("M5", 300)
        for r in ref[-3:]:
            c = live[r.t_ms // 1000]
            assert (c["open"], c["high"], c["low"], c["close"], c["ticks"]) == (r.o, r.h, r.l, r.c, r.n)


def test_switch_delay_300_60_900_without_reconnecting(env):
    client, clock, _ = env
    with client.websocket_connect("/ws/stream?symbols=EURUSD&delay=300&tf=M1") as ws:
        assert backfills(ws, 1)["EURUSD"]["cutoff_ms"] == clock.t - 300_000
        for delay in (60, 900, 300):
            ws.send_json({"type": "set_delay", "delay": delay})
            m, _ = recv_until(ws, lambda x: x["type"] == "backfill")
            assert m["delay"] == delay and m["reset"] is True
            assert m["cutoff_ms"] == clock.t - delay * 1000
            assert m["candles"][-1]["time"] * 1000 <= m["cutoff_ms"]
        # and updates keep flowing at the new delay on the same socket
        clock.t += 3000
        m, _ = recv_until(ws, lambda x: x["type"] == "update")
        assert m["cutoff_ms"] == clock.t - 300_000


def test_switch_timeframe_and_symbols_midstream(env):
    client, _, _ = env
    with client.websocket_connect("/ws/stream?symbols=EURUSD&delay=300&tf=M1") as ws:
        backfills(ws, 1)
        ws.send_json({"type": "set_tf", "tf": "H1"})
        m, _ = recv_until(ws, lambda x: x["type"] == "backfill")
        assert m["tf"] == "H1" and m["candles"][-1]["time"] % 3600 == 0
        ws.send_json({"type": "set_symbols", "symbols": ["EURJPY", "EURUSD"]})
        assert set(backfills(ws, 2)) == {"EURJPY", "EURUSD"}


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("q", ["symbols=EURUSD&delay=0", "symbols=EURUSD&delay=-5",
                               "symbols=EURUSD&delay=99999999", "symbols=NOPE&delay=300",
                               "symbols=EURUSD&delay=300&tf=W1", "symbols=&delay=300"])
def test_bad_requests_are_refused(env, q):
    client, _, _ = env
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/stream?{q}"):
            pass


def test_zero_delay_cannot_be_set_on_the_normal_stream(env):
    client, _, _ = env
    with client.websocket_connect("/ws/stream?symbols=EURUSD&delay=300") as ws:
        backfills(ws, 1)
        ws.send_json({"type": "set_delay", "delay": 0})
        m, _ = recv_until(ws, lambda x: x["type"] == "error")
        assert "delay" in m["message"]
        # the session keeps working at the old delay
        ws.send_json({"type": "set_delay", "delay": 60})
        assert recv_until(ws, lambda x: x["type"] == "backfill")[0]["delay"] == 60


def test_cutoff_never_moves_backwards_on_its_own(tick_db, cfg):
    clock = FakeClock(LIVE_END)
    s = StreamSession(cfg, db.connect(tick_db), CandleCache(), clock, ["EURUSD"], "M1", 300)
    a = s.cutoff_ms()
    clock.t -= 10_000                       # clock glitch
    assert s.cutoff_ms() == a


# ---------------------------------------------------------------- peek
def test_peek_refused_without_explicit_peeked_flag(env):
    client, _, path = env
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/peek?symbols=EURUSD"):
            pass
    assert db.connect(path).execute("SELECT COUNT(*) FROM peek_log").fetchone()[0] == 0


def test_peek_is_live_edge_and_is_logged(env):
    client, clock, path = env
    with client.websocket_connect("/ws/peek?symbols=EURUSD&peeked=1&session_id=abc") as ws:
        m = backfills(ws, 1)["EURUSD"]
        assert m["delay"] == 0 and m["cutoff_ms"] == clock.t
        conn = db.connect(path)
        assert m["candles"][-1]["time"] * 1000 > clock.t - 120_000    # live, not 5 min behind
        ws.send_json({"type": "set_delay", "delay": 300})             # peek can't be "delayed back"
        assert recv_until(ws, lambda x: x["type"] == "error")[0]
    log = db.connect(path).execute("SELECT session_id, event FROM peek_log ORDER BY rowid").fetchall()
    assert log[0] == ("abc", "open")
    import time; time.sleep(0.2)
    log = db.connect(path).execute("SELECT session_id, event FROM peek_log ORDER BY rowid").fetchall()
    assert log[-1] == ("abc", "close")


def test_session_ends_at_once_on_disconnect_not_a_poll_interval_later(tick_db, cfg):
    """A call made right after closing Peek must not still count as peeked, so the server has to
    notice a disconnect immediately. Drive a session over a fake socket with a 1 s poll interval and
    measure how long it takes to finish after the client goes away."""
    import asyncio
    import time
    from starlette.websockets import WebSocketDisconnect
    from server.stream import drive

    class FakeWS:
        def __init__(self): self.sent = []; self.gone = asyncio.Event()
        async def send_json(self, m): self.sent.append(m)
        async def receive_json(self):
            await self.gone.wait()
            raise WebSocketDisconnect(1001)

    async def scenario():
        sess = StreamSession(cfg, db.connect(tick_db, check_same_thread=False), CandleCache(),
                             FakeClock(LIVE_END), ["EURUSD"], "M1", 0, allow_zero_delay=True)
        ws = FakeWS()
        task = asyncio.create_task(drive(ws, sess, 1.0, 5.0))
        await asyncio.sleep(0.35)                       # session is running, loop is inside its 1 s wait
        assert ws.sent and ws.sent[0]["type"] == "backfill" and not task.done()
        t0 = time.monotonic()
        ws.gone.set()
        await asyncio.wait_for(task, 3)
        sess.close()
        return time.monotonic() - t0

    took = asyncio.run(scenario())
    assert took < 0.3, f"server needed {took:.2f} s to notice the disconnect"


def test_normal_stream_never_shows_what_peek_shows(env):
    client, clock, _ = env
    with client.websocket_connect("/ws/peek?symbols=EURUSD&peeked=1") as pk:
        live = backfills(pk, 1)["EURUSD"]["candles"][-1]
    with client.websocket_connect("/ws/stream?symbols=EURUSD&delay=300") as ws:
        delayed = backfills(ws, 1)["EURUSD"]
    assert live["time"] > delayed["candles"][-1]["time"]
    assert delayed["candles"][-1]["time"] * 1000 <= clock.t - 300_000


def test_health_and_public_config(env):
    client, _, _ = env
    assert client.get("/health").json()["ok"] is True
    cfgj = client.get("/api/config").json()
    assert cfgj["delay"]["presets_seconds"] == [60, 300, 900] and "M1" in cfgj["timeframes"]
