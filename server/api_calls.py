"""Practice calls: Buy / Sell buttons that do NOT open a position.

POST /calls stores a call at the current DELAYED time (server clock - delay, so the client
cannot back-date it), flagged peeked=1 if the client says so OR the session has an open
peek stream (a call cannot be un-peeked by lying). The labeler grades each call at its
exact time; GET /calls shows the result once final, GET /calls/scorecard aggregates them
(calls, average pips per horizon, win rate), split clean vs peeked.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from core import db

RESULT_COLS = ["status", "entry_price", "spread_pips", "pnl_30s", "pnl_60s", "pnl_120s", "pnl_300s",
               "mfe_pips", "mae_pips", "t_to_mfe_ms", "t_to_mae_ms", "fixed_outcome",
               "fixed_pnl_pips", "fixed_exit_ms"]
HORIZONS = ("pnl_30s", "pnl_60s", "pnl_120s", "pnl_300s")


class CallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: str
    delay: float                      # the delay of the chart the call was made on
    note: str = ""
    peeked: bool = False
    session_id: Optional[str] = None


def _sym(st, symbol: str) -> str:
    cfg = st.cfg
    s = symbol + cfg.symbol_suffix if symbol in cfg.symbols else symbol
    if s not in cfg.broker_symbols:
        raise HTTPException(404, f"unknown symbol {symbol!r}")
    return s


def _row(r) -> dict:
    keys = ["id", "symbol", "t_ms", "direction", "note", "peeked", "session_id", "created_ms",
            "delay_s"] + RESULT_COLS
    d = dict(zip(keys, r))
    d["peeked"] = int(d["peeked"] or 0)
    d["status"] = d["status"] or "pending"
    return d


SELECT = ("SELECT c.id, c.symbol, c.t_ms, c.direction, c.note, c.peeked, c.session_id, "
          "c.created_ms, c.delay_s, " + ",".join("r." + c for c in RESULT_COLS) +
          " FROM calls c LEFT JOIN call_results r ON r.call_id = c.id")


def make_router() -> APIRouter:
    router = APIRouter()

    @router.post("/calls")
    def create_call(body: CallIn, request: Request):
        st = request.app.state
        cfg = st.cfg
        sym = _sym(st, body.symbol)
        if body.direction not in ("buy", "sell"):
            raise HTTPException(400, "direction must be 'buy' or 'sell'")
        if not (cfg.delay.min_seconds <= body.delay <= cfg.delay.max_seconds):
            raise HTTPException(400, "bad delay (calls are made on a delayed chart)")
        now = st.clock()
        t_ms = int(now - body.delay * 1000)
        conn = db.connect(st.db_path)
        try:
            peeked = body.peeked
            if body.session_id and not peeked:
                last = conn.execute("SELECT event FROM peek_log WHERE session_id=? "
                                    "ORDER BY rowid DESC LIMIT 1", (body.session_id,)).fetchone()
                peeked = bool(last and last[0] == "open")
            with conn:
                cur = conn.execute(
                    "INSERT INTO calls (symbol, t_ms, direction, note, peeked, session_id, created_ms, delay_s) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (sym, t_ms, body.direction, body.note[:500], int(peeked), body.session_id, now, body.delay))
            row = conn.execute(SELECT + " WHERE c.id=?", (cur.lastrowid,)).fetchone()
        finally:
            conn.close()
        return _row(row)

    @router.get("/calls")
    def list_calls(request: Request, symbol: Optional[str] = None, limit: int = 50):
        st = request.app.state
        q, args = SELECT, []
        if symbol:
            q += " WHERE c.symbol=?"; args.append(_sym(st, symbol))
        q += " ORDER BY c.id DESC LIMIT ?"; args.append(max(1, min(limit, 1000)))
        conn = db.connect(st.db_path)
        try:
            return {"calls": [_row(r) for r in conn.execute(q, args)]}
        finally:
            conn.close()

    @router.get("/calls/scorecard")
    def scorecard(request: Request, symbol: Optional[str] = None):
        st = request.app.state
        where, args = "", []
        if symbol:
            where, args = " WHERE c.symbol=?", [_sym(st, symbol)]
        conn = db.connect(st.db_path)
        try:
            rows = [_row(r) for r in conn.execute(SELECT + where, args)]
        finally:
            conn.close()

        def summarise(rs):
            final = [r for r in rs if r["status"] == "final"]
            out = {"calls": len(rs), "graded": len(final),
                   "pending": sum(r["status"] == "pending" for r in rs),
                   "ungradable": sum(r["status"] == "ungradable" for r in rs),
                   "avg_pips": {}, "win_rate": {}}
            for h in HORIZONS:
                vals = [r[h] for r in final if r[h] is not None]
                out["avg_pips"][h] = round(sum(vals) / len(vals), 3) if vals else None
                out["win_rate"][h] = round(100 * sum(v > 0 for v in vals) / len(vals), 1) if vals else None
            for k in ("mfe_pips", "mae_pips", "spread_pips"):
                vals = [r[k] for r in final if r[k] is not None]
                out["avg_" + k] = round(sum(vals) / len(vals), 3) if vals else None
            return out

        return {"all": summarise(rows),
                "clean": summarise([r for r in rows if not r["peeked"]]),
                "peeked": summarise([r for r in rows if r["peeked"]])}

    return router
