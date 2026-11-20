"""Peek: `/ws/peek?symbols=...&tf=M1&peeked=1[&session_id=...]` - the same feed with delay 0.

Kept in its own module on purpose (hard rules 1-2): this is the only endpoint that can
show data newer than the delayed cutoff. Opening it is an explicit, logged action:
  * the client MUST pass peeked=1 to declare the session as peeked, otherwise refused;
  * open and close are written to `peek_log`.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core import db
from core.config import Config
from server.auth import ws_is_admin
from server.stream import StreamError, StreamSession, drive


def _log(st, sid: str, session: StreamSession, event: str) -> None:
    """Peek log uses its own short-lived connection, never the session's."""
    c = db.connect(st.db_path)
    try:
        db.log_peek(c, st.clock(), sid, ",".join(session.symbols), event)
    finally:
        c.close()


def make_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/peek")
    async def ws_peek(ws: WebSocket, symbols: str, tf: str = "M1", peeked: int = 0,
                      session_id: Optional[str] = None):
        st = ws.app.state
        cfg: Config = st.cfg
        if not ws_is_admin(ws):
            await ws.close(code=4403, reason="admin login required")
            return
        if peeked != 1:
            await ws.close(code=4403, reason="peek requires peeked=1 (explicit opt-in)")
            return
        conn = db.connect(st.db_path, check_same_thread=False)
        try:
            session = StreamSession(cfg, conn, st.cache, st.clock, symbols.split(","), tf, 0,
                                    allow_zero_delay=True)
        except StreamError as e:
            conn.close()
            await ws.close(code=4400, reason=str(e)[:120])
            return
        sid = session_id or uuid.uuid4().hex
        _log(st, sid, session, "open")
        await ws.accept()
        try:
            await drive(ws, session, st.interval_s, cfg.server.heartbeat_seconds)
        except WebSocketDisconnect:
            pass
        finally:
            session.close()
            _log(st, sid, session, "close")

    return router
