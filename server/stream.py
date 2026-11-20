"""Delay stream: websocket `/ws/stream?symbols=EURUSD,GBPUSD&delay=300&tf=M1`.

`StreamSession` holds all the logic and is synchronous (testable without sockets); the
websocket handler only drives it. Every read goes through a DataView pinned to one
shared cutoff = utc_now - delay, so the browser never receives data past the cutoff.

Server -> client messages
  {"type":"backfill","reset":true,"symbol","tf","delay","cutoff_ms","pip","digits","candles":[...]}
  {"type":"update","cutoff_ms","symbols":{SYM:{"ticks":[[t_ms,bid,ask]...],"candles":[...]}}}
  {"type":"heartbeat","cutoff_ms"}      {"type":"error","message"}
Client -> server messages
  {"type":"set_delay","delay":60}  {"type":"set_tf","tf":"M5"}  {"type":"set_symbols","symbols":[...]}
Changing any of them re-syncs with fresh backfill messages (no reconnect needed).
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core import db
from core.candles import CandleBuilder, CandleCache, bucket_open
from core.config import Config
from core.dataview import DataView, TickKey
from core.pips import pip_size
from server.auth import ws_is_admin


class StreamError(ValueError):
    pass


class StreamSession:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, cache: CandleCache,
                 clock: Callable[[], int], symbols: List[str], tf: str, delay_s: float,
                 allow_zero_delay: bool = False, fixed_delay: Optional[Callable[[], float]] = None):
        self.cfg, self.conn, self.cache, self.clock = cfg, conn, cache, clock
        self.allow_zero_delay = allow_zero_delay
        # a public (non-admin) client does not choose its delay: it is the terminal's, set in the back office
        self.fixed_delay = fixed_delay
        self.symbols = self.resolve_symbols(symbols)
        self.tf = self.check_tf(tf)
        self.delay_s = self.check_delay(delay_s)
        self.cursor: Dict[str, Optional[TickKey]] = {}
        self.builders: Dict[str, CandleBuilder] = {}
        self._last_cutoff = 0
        # DB work runs in worker threads; a cancelled handler must never close the
        # connection under a running query (that segfaults), so close() takes this lock.
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self.conn.close()

    # ------------------------------------------------------------ validation
    def resolve_symbols(self, symbols: List[str]) -> List[str]:
        out = []
        for s in symbols:
            s = s.strip()
            if not s:
                continue
            if s in self.cfg.symbols:
                s = s + self.cfg.symbol_suffix
            if s not in self.cfg.broker_symbols:
                raise StreamError(f"unknown symbol {s!r}")
            if s not in out:
                out.append(s)
        if not out:
            raise StreamError("no symbols requested")
        return out

    def check_tf(self, tf: str) -> str:
        if tf not in self.cfg.candles.timeframes:
            raise StreamError(f"unsupported timeframe {tf!r}")
        return tf

    def check_delay(self, delay_s: float) -> float:
        lo = 0 if self.allow_zero_delay else self.cfg.delay.min_seconds
        if self.allow_zero_delay and delay_s != 0:
            raise StreamError("peek stream has delay 0")
        if not (lo <= delay_s <= self.cfg.delay.max_seconds):
            raise StreamError(f"delay must be between {lo} and {self.cfg.delay.max_seconds} seconds")
        return delay_s

    # ------------------------------------------------------------ cutoff
    def cutoff_ms(self) -> int:
        """One shared cutoff for every symbol; never moves backwards on its own."""
        c = int(self.clock() - self.delay_s * 1000)
        self._last_cutoff = max(self._last_cutoff, c)
        return self._last_cutoff

    def _view(self, symbol: str, cutoff: int) -> DataView:
        return DataView(self.conn, symbol, cutoff, self.cache, self.cfg.candles.price_side)

    # ------------------------------------------------------------ messages
    def apply(self, msg: dict) -> None:
        """Apply a client control message (raises StreamError if invalid). Caller must
        then call resync()."""
        kind = msg.get("type")
        if kind == "set_delay":
            if self.allow_zero_delay:
                raise StreamError("delay is fixed at 0 on the peek stream")
            if self.fixed_delay is not None and float(msg["delay"]) != self.fixed_delay():
                raise StreamError("the delay is set by the server")
            self.delay_s = self.check_delay(float(msg["delay"]))
        elif kind == "set_tf":
            self.tf = self.check_tf(str(msg["tf"]))
        elif kind == "set_symbols":
            self.symbols = self.resolve_symbols(list(msg["symbols"]))
        else:
            raise StreamError(f"unknown message type {kind!r}")
        self._last_cutoff = 0   # a delay change may legitimately move the cutoff back

    def resync(self) -> List[dict]:
        with self._lock:
            return self._resync() if not self._closed else []

    def poll(self) -> Optional[dict]:
        with self._lock:
            return self._poll() if not self._closed else None

    def _resync(self) -> List[dict]:
        """Fresh backfill for every symbol at the current cutoff; resets cursors."""
        cutoff = self.cutoff_ms()
        out = []
        self.cursor, self.builders = {}, {}
        for sym in self.symbols:
            dv = self._view(sym, cutoff)
            candles = dv.candles(self.tf, self.cfg.server.backfill_candles)
            digits = db.get_digits(self.conn, sym)
            pip = pip_size(digits)
            forming_open = bucket_open(cutoff, self.tf)
            seed = candles[-1] if candles and candles[-1].t_ms == forming_open else None
            self.builders[sym] = CandleBuilder(self.tf, self.cfg.candles.price_side, seed)
            self.cursor[sym] = dv.last_tick_key()
            lt = dv.last_tick()
            out.append({"type": "backfill", "reset": True, "symbol": sym, "tf": self.tf,
                        "delay": self.delay_s, "cutoff_ms": cutoff, "digits": digits,
                        "pip": pip, "candles": [c.to_dict(pip) for c in candles],
                        "last_tick": [lt[0], lt[2], lt[3]] if lt else None})
        return out

    def _poll(self) -> Optional[dict]:
        """New ticks in (previous position, cutoff] plus the updated candles."""
        cutoff = self.cutoff_ms()
        limit = self.cfg.server.tick_batch_limit
        updates = {}
        for sym in self.symbols:
            dv = self._view(sym, cutoff)
            rows = dv.ticks_after(self.cursor.get(sym), limit)
            if not rows:
                continue
            self.cursor[sym] = (rows[-1][0], rows[-1][1])
            pip = pip_size(db.get_digits(self.conn, sym))
            candles = self.builders[sym].add(rows)
            updates[sym] = {"ticks": [[r[0], r[2], r[3]] for r in rows],
                            "candles": [c.to_dict(pip) for c in candles]}
        if not updates:
            return None
        return {"type": "update", "cutoff_ms": cutoff, "symbols": updates}

    def heartbeat(self) -> dict:
        return {"type": "heartbeat", "cutoff_ms": self.cutoff_ms()}


# ---------------------------------------------------------------------- websocket
async def drive(ws: WebSocket, session: StreamSession, interval_s: float,
                heartbeat_s: float) -> None:
    """Run a session over an accepted websocket until the client goes away."""
    send = ws.send_json
    for m in await asyncio.to_thread(session.resync):
        await send(m)

    resync_needed = False
    pending_error: Optional[str] = None

    async def receiver() -> None:
        nonlocal resync_needed, pending_error
        while True:
            msg = await ws.receive_json()
            try:
                session.apply(msg)
                resync_needed = True
            except (StreamError, KeyError, TypeError, ValueError) as e:
                pending_error = str(e)

    rx = asyncio.create_task(receiver())
    last_sent = time.monotonic()
    try:
        while not rx.done():
            if pending_error:
                err, pending_error = pending_error, None
                await send({"type": "error", "message": err})
            if resync_needed:
                resync_needed = False
                for m in await asyncio.to_thread(session.resync):
                    await send(m)
                last_sent = time.monotonic()
            else:
                msg = await asyncio.to_thread(session.poll)
                if msg is not None:
                    await send(msg)
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= heartbeat_s:
                    await send(session.heartbeat())
                    last_sent = time.monotonic()
            # wait out the interval, but wake immediately if the client disconnects, so the
            # session (and a peek session's 'close' log entry) ends within milliseconds
            await asyncio.wait({rx}, timeout=interval_s)
    finally:
        rx.cancel()
    exc = rx.exception() if rx.done() and not rx.cancelled() else None
    if exc and not isinstance(exc, WebSocketDisconnect):
        raise exc


def make_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/stream")
    async def ws_stream(ws: WebSocket, symbols: str, delay: float | None = None,
                        tf: str = "M1"):
        st = ws.app.state
        cfg: Config = st.cfg
        locked = not ws_is_admin(ws)
        fixed = (lambda: st.engine.delay_s) if locked else None
        try:
            session = StreamSession(
                cfg, db.connect(st.db_path, check_same_thread=False), st.cache, st.clock,
                symbols.split(","), tf, st.engine.delay_s if locked else (cfg.delay.default_seconds if delay is None else delay),
                fixed_delay=fixed)
        except StreamError as e:
            await ws.close(code=4400, reason=str(e)[:120])
            return
        await ws.accept()
        try:
            await drive(ws, session, st.interval_s, cfg.server.heartbeat_seconds)
        except WebSocketDisconnect:
            pass
        finally:
            session.close()

    return router
