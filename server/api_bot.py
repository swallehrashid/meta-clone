"""Bot control API (delayed-live mode).   GET /api/bot   POST /api/bot/start   POST /api/bot/stop

The bot runs on the paper account's delayed clock, as a human would on the delayed chart; it never sees Peek data and its
trades are tagged source="bot", clean - unless it was started with forward_s > 0 (a training run that may see the next forward_s
seconds, never more than the delay hides), in which case every trade is tagged peeked. Replay of stored history is a separate
command (python -m bot.run) or a job in the back office.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    params: Dict[str, Any] = {}
    symbols: Optional[List[str]] = None
    limits: Dict[str, Any] = {}
    forward_s: int = 0                      # > 0: training run, the bot sees that many seconds ahead (orders tagged peeked)


class StopIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    close_positions: bool = False


def _full(m, log: int = 40) -> dict:
    """One response shape for every endpoint: status + the recent activity log."""
    return {**m.status(), "log": m.log_view(log)}


def make_router() -> APIRouter:
    router = APIRouter(prefix="/api/bot")

    @router.get("")
    def status(request: Request, log: int = 40):
        return _full(request.app.state.bots, max(1, min(log, 500)))

    @router.post("/start")
    def start(body: StartIn, request: Request):
        st = request.app.state
        syms = None
        if body.symbols:
            syms = [s + st.cfg.symbol_suffix if s in st.cfg.symbols else s for s in body.symbols]
            bad = [s for s in syms if s not in st.cfg.broker_symbols]
            if bad:
                raise HTTPException(400, f"unknown symbols {bad}")
        try:
            st.bots.start(body.name, body.params, syms, body.limits, now_ms=st.clock(), forward_s=body.forward_s)
            return _full(st.bots)
        except ValueError as e:
            raise HTTPException(400 if "already" not in str(e) else 409, str(e)) from None

    @router.post("/stop")
    def stop(body: StopIn, request: Request):
        m = request.app.state.bots
        m.stop(body.close_positions)
        return _full(m)

    return router
