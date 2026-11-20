"""Paper-trading API (Phase 6). The account runs on the DELAYED clock: every call carries the `delay` of the chart
it was made on, the server converts it to a chart time (now - delay) and the engine advances to that time first.
Trading is therefore only possible on a delayed chart (delay >= delay.min_seconds); a Peek session does not change
the clock, it only tags the trade `peeked`.

    GET  /api/trade/state            account + positions + pending orders (advances the account clock)
    GET  /api/trade/history          closed trades        GET /api/trade/equity   equity curve
    POST /api/trade/orders           place market / limit / stop      DELETE /api/trade/orders/{id}   cancel
    PATCH /api/trade/orders/{id}     move price / SL / TP of a pending order
    PATCH /api/trade/positions/{id}  move SL / TP (null clears)       POST /api/trade/positions/{id}/close   (partial: volume)
    POST /api/trade/positions/close_all                               POST /api/trade/account/reset  {confirm: true}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from core import db
from engine.orders import UNSET, EngineError
from server.auth import COOKIE, require_admin

log = logging.getLogger("engine")
JITTER_MS = 2000


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: str
    type: str = "market"
    volume: float
    price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    delay: float
    peeked: bool = False
    session_id: Optional[str] = None
    comment: str = ""
    stoplimit: Optional[float] = None            # stop limit orders: the limit price placed when the stop is reached
    expiration: str = "gtc"                      # gtc | today | specified (pending orders)
    expire_ms: Optional[int] = None              # specified: UTC ms


class CloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    volume: Optional[float] = None
    delay: float


class CloseAllIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: Optional[str] = None
    delay: float


class ModifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    stoplimit: Optional[float] = None
    delay: float


class ResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    balance: Optional[float] = None
    leverage: Optional[float] = None


def _chart_time(request: Request, delay: Optional[float]) -> int:
    """The chart's 'now' for a delay, after recording the delay for the background clock."""
    st = request.app.state
    # only the back office may pick a delay; everyone else trades on the terminal's (a client cannot ask for fresher prices)
    admin = (not st.require_admin) or st.auth.verify(request.cookies.get(COOKIE))
    if not admin:
        delay = None
    d = st.engine.delay_s if delay is None else delay
    cfg = st.cfg.delay
    if not (cfg.min_seconds <= d <= cfg.max_seconds):
        raise HTTPException(400, f"trading needs a delayed chart: delay must be between {cfg.min_seconds} and {cfg.max_seconds} s")
    if delay is not None and delay != st.engine.delay_s:
        st.engine.set_delay(delay)
    at = int(st.clock() - d * 1000)
    # The background heartbeat may have moved the account clock a few ms past this request's chart time while the request was
    # on its way to the engine lock: that is processing order, not a stale chart. Execute at the account clock instead of
    # refusing. A real lag (the delay was lengthened, seconds or more) is still refused by the engine.
    lag = st.engine.t_engine - at
    if 0 < lag <= JITTER_MS:
        at = st.engine.t_engine
    return at


def _peeked(st, flag: bool, session_id: Optional[str]) -> bool:
    if flag or not session_id:
        return flag
    conn = db.connect(st.db_path)
    try:
        last = conn.execute("SELECT event FROM peek_log WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (session_id,)).fetchone()
    finally:
        conn.close()
    return bool(last and last[0] == "open")                           # a call cannot be un-peeked by omission


def _expiry(st, body: "OrderIn", at_ms: int) -> Optional[int]:
    """GTC -> None; today -> the end of the chart's day in the terminal's time zone; specified -> the given time."""
    kind = (body.expiration or "gtc").lower()
    if kind == "gtc" or body.type == "market":
        return None
    if kind == "today":
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo
        from server import settings
        conn = db.connect(st.db_path)
        try:
            tz = ZoneInfo(settings.load(st.cfg, conn)["tz"])
        finally:
            conn.close()
        day = datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc).astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return int((day + timedelta(days=1)).timestamp() * 1000) - 1
    if kind == "specified":
        if body.expire_ms is None:
            raise HTTPException(400, "a specified expiration needs expire_ms")
        return int(body.expire_ms)
    raise HTTPException(400, "expiration must be gtc, today or specified")


def _snapshot(st) -> dict:
    e = st.engine
    return {"account": e.account(), "positions": e.positions_view(), "orders": e.orders_view()}


async def run_clock(app, interval_s: float) -> None:
    """Background heartbeat: keep the account clock at now - delay so SL / TP / pending orders trigger on their own."""
    st = app.state
    while True:
        await asyncio.sleep(interval_s)
        try:
            await asyncio.to_thread(st.bots.heartbeat, st.clock())      # advances the account clock; runs the bot if one is on
        except Exception:                                              # noqa: BLE001 - never let the heartbeat die
            log.exception("account clock advance failed")


def make_router() -> APIRouter:
    router = APIRouter(prefix="/api/trade")

    @router.get("/state")
    def state(request: Request, delay: Optional[float] = None):
        st = request.app.state
        st.engine.advance_to(_chart_time(request, delay))
        return _snapshot(st)

    @router.get("/history")
    def history(request: Request, limit: int = 100):
        return {"deals": request.app.state.engine.history(max(1, min(limit, 5000)))}

    @router.get("/orders_history")
    def orders_history(request: Request, limit: int = 500):
        return {"orders": request.app.state.engine.order_history(max(1, min(limit, 5000)))}

    @router.get("/equity")
    def equity(request: Request, since: int = 0):
        return {"points": request.app.state.engine.equity_curve(since)}

    @router.post("/orders")
    def place(body: OrderIn, request: Request):
        st = request.app.state
        at = _chart_time(request, body.delay)
        r = st.engine.place_order(body.symbol, body.side, body.type, body.volume, body.price, body.sl, body.tp, at_ms=at,
                                  source="manual", peeked=_peeked(st, body.peeked, body.session_id),
                                  session_id=body.session_id, comment=body.comment[:64], stoplimit=body.stoplimit,
                                  expire_ms=_expiry(st, body, at))
        return {**r, "at_ms": at, **_snapshot(st)}

    @router.delete("/orders/{order_id}")
    def cancel(order_id: int, request: Request, delay: Optional[float] = None):
        st = request.app.state
        st.engine.cancel_order(order_id, at_ms=_chart_time(request, delay))
        return _snapshot(st)

    @router.patch("/orders/{order_id}")
    def modify_order(order_id: int, body: ModifyIn, request: Request):
        st = request.app.state
        kw = {k: getattr(body, k) for k in ("price", "sl", "tp", "stoplimit") if k in body.model_fields_set}
        st.engine.modify(order_id, kind="order", at_ms=_chart_time(request, body.delay), **kw)
        return _snapshot(st)

    @router.patch("/positions/{position_id}")
    def modify_position(position_id: int, body: ModifyIn, request: Request):
        st = request.app.state
        kw = {k: getattr(body, k) for k in ("price", "sl", "tp") if k in body.model_fields_set}
        st.engine.modify(position_id, kind="position", at_ms=_chart_time(request, body.delay), **kw)
        return _snapshot(st)

    @router.post("/positions/close_all")
    def close_all(body: CloseAllIn, request: Request):
        st = request.app.state
        out = st.engine.close_all(body.symbol, at_ms=_chart_time(request, body.delay))
        return {**out, **_snapshot(st)}

    @router.post("/positions/{position_id}/close")
    def close(position_id: int, body: CloseIn, request: Request):
        st = request.app.state
        out = st.engine.close(position_id, body.volume, at_ms=_chart_time(request, body.delay))
        return {**out, **_snapshot(st)}

    @router.post("/account/reset", dependencies=[Depends(require_admin)])
    def reset(body: ResetIn, request: Request):
        if not body.confirm:
            raise HTTPException(400, "resetting wipes all positions, history and the equity curve: send confirm=true")
        st = request.app.state
        st.engine.reset(body.balance, body.leverage)
        return _snapshot(st)

    return router


def install_error_handler(app) -> None:
    @app.exception_handler(EngineError)
    async def _handler(request: Request, exc: EngineError):
        status = 409 if exc.code == "clock_behind" else 400
        return JSONResponse(status_code=status, content={"detail": exc.message, "code": exc.code})
