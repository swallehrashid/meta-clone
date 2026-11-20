"""GET /api/report (JSON, for the web app) and GET /report (the same report as one HTML page).

Query: symbols=EURUSD,GBPUSD  date_from=2025-03-01  date_to=2025-03-31 (inclusive, display timezone)
       t_from= t_to= (UTC ms, alternative to dates)  session=08:00-11:00|ny  max_spread=1.5
       direction=buy|sell  horizon=60  oracle=top_decile|run|fixed_tp

A report aggregates outcomes that lie after each entry, so - like /labels - it only covers entries at
or before the delayed clock (now - delay); entries up to the live edge need an explicit peeked=1.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from analysis.data import Filters
from analysis.report import build_report, filters_from_args, render_html
from core import db


def _build(request: Request, symbols, date_from, date_to, t_from, t_to, session, max_spread, direction,
           horizon, oracle, delay, peeked) -> dict:
    st = request.app.state
    cfg = st.cfg
    d = cfg.delay.default_seconds if delay is None else delay
    if d < 0 or d > cfg.delay.max_seconds:
        raise HTTPException(400, "bad delay")
    if d == 0 and peeked != 1:
        raise HTTPException(403, "reports up to the live edge need an explicit peeked=1")
    try:
        f: Filters = filters_from_args(cfg, symbols, date_from, date_to, session, max_spread, direction)
        if t_from is not None:
            f.t_from_ms = t_from if f.t_from_ms is None else max(f.t_from_ms, t_from)
        if t_to is not None:
            f.t_to_ms = t_to if f.t_to_ms is None else min(f.t_to_ms, t_to)
        cutoff = int(st.clock() - d * 1000)
        f.t_to_ms = cutoff if f.t_to_ms is None else min(f.t_to_ms, cutoff)          # never past the delayed clock
        ocfg = cfg.analysis.oracle.model_copy(update={"mode": oracle}) if oracle else None
        conn = db.connect(st.db_path)
        try:
            rep = build_report(conn, cfg, f, horizon, ocfg)
        finally:
            conn.close()
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    rep["meta"]["cutoff_ms"] = cutoff
    return rep


def make_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/report")
    def report_json(request: Request, symbols: Optional[str] = None, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, t_from: Optional[int] = None, t_to: Optional[int] = None,
                    session: Optional[str] = None, max_spread: Optional[float] = None,
                    direction: Optional[str] = None, horizon: Optional[int] = None,
                    oracle: Optional[str] = None, delay: Optional[float] = None, peeked: int = 0):
        return _build(request, symbols, date_from, date_to, t_from, t_to, session, max_spread, direction,
                      horizon, oracle, delay, peeked)

    @router.get("/report", response_class=HTMLResponse)
    def report_html(request: Request, symbols: Optional[str] = None, date_from: Optional[str] = None,
                    date_to: Optional[str] = None, t_from: Optional[int] = None, t_to: Optional[int] = None,
                    session: Optional[str] = None, max_spread: Optional[float] = None,
                    direction: Optional[str] = None, horizon: Optional[int] = None,
                    oracle: Optional[str] = None, delay: Optional[float] = None, peeked: int = 0):
        rep = _build(request, symbols, date_from, date_to, t_from, t_to, session, max_spread, direction,
                     horizon, oracle, delay, peeked)
        return HTMLResponse(render_html(rep))

    return router
