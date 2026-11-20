"""GET /labels and GET /features.

A label's outcome describes the market AFTER the entry (that is its whole point - "the
data between the delayed clock and real time"), so this is the one sanctioned channel
that carries post-cutoff information. To keep hard rule 2 meaningful:
  * only entries at or before the delayed clock (now - delay) can be requested;
  * delay=0 (entries up to the live edge) needs the explicit opt-in peeked=1.
The web app shows labels only behind a toggle that is off by default.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from core import db
LABEL_COLS = db.LABEL_COLS

MAX_LIMIT = 50_000


def _resolve(st, symbol: str) -> str:
    cfg = st.cfg
    s = symbol + cfg.symbol_suffix if symbol in cfg.symbols else symbol
    if s not in cfg.broker_symbols:
        raise HTTPException(404, f"unknown symbol {symbol!r}")
    return s


def _window(st, delay: Optional[float], peeked: int, t_from: Optional[int], t_to: Optional[int]):
    cfg = st.cfg
    d = cfg.delay.default_seconds if delay is None else delay
    if d < 0 or d > cfg.delay.max_seconds:
        raise HTTPException(400, "bad delay")
    if d == 0 and peeked != 1:
        raise HTTPException(403, "entries up to the live edge need an explicit peeked=1")
    cutoff = int(st.clock() - d * 1000)
    hi = cutoff if t_to is None else min(t_to, cutoff)
    return (t_from if t_from is not None else 0), hi, cutoff


def make_router() -> APIRouter:
    router = APIRouter()

    @router.get("/labels")
    def labels(request: Request, symbol: str, t_from: Optional[int] = None,
               t_to: Optional[int] = None, direction: Optional[str] = None,
               status: Optional[str] = None, delay: Optional[float] = None, peeked: int = 0,
               limit: int = 5000):
        st = request.app.state
        sym = _resolve(st, symbol)
        lo, hi, cutoff = _window(st, delay, peeked, t_from, t_to)
        if direction not in (None, "buy", "sell") or status not in (None, "pending", "final"):
            raise HTTPException(400, "bad direction/status")
        q = f"SELECT {','.join(LABEL_COLS)} FROM labels WHERE symbol=? AND entry_t_ms BETWEEN ? AND ?"
        args = [sym, lo, hi]
        for col, val in (("direction", direction), ("status", status)):
            if val:
                q += f" AND {col}=?"; args.append(val)
        q += " ORDER BY entry_t_ms, direction LIMIT ?"
        args.append(max(1, min(limit, MAX_LIMIT)))
        conn = db.connect(st.db_path)
        try:
            rows = [dict(zip(LABEL_COLS, r)) for r in conn.execute(q, args)]
        finally:
            conn.close()
        return {"symbol": sym, "cutoff_ms": cutoff, "count": len(rows), "labels": rows}

    @router.get("/features")
    def features(request: Request, symbol: str, t_from: Optional[int] = None,
                 t_to: Optional[int] = None, names: Optional[str] = None,
                 delay: Optional[float] = None, peeked: int = 0, limit: int = 5000):
        st = request.app.state
        sym = _resolve(st, symbol)
        lo, hi, cutoff = _window(st, delay, peeked, t_from, t_to)
        conn = db.connect(st.db_path)
        try:
            ts = [r[0] for r in conn.execute(
                "SELECT DISTINCT t_ms FROM features WHERE symbol=? AND t_ms BETWEEN ? AND ? "
                "ORDER BY t_ms LIMIT ?", (sym, lo, hi, max(1, min(limit, MAX_LIMIT))))]
            out = {t: {} for t in ts}
            if ts:
                q = ("SELECT t_ms, name, value FROM features WHERE symbol=? AND t_ms BETWEEN ? AND ?")
                args = [sym, ts[0], ts[-1]]
                if names:
                    wanted = [n for n in names.split(",") if n]
                    q += f" AND name IN ({','.join('?' * len(wanted))})"
                    args += wanted
                for t, n, v in conn.execute(q, args):
                    if t in out:
                        out[t][n] = v
        finally:
            conn.close()
        return {"symbol": sym, "cutoff_ms": cutoff, "count": len(ts),
                "rows": [{"t_ms": t, "features": out[t]} for t in ts]}

    return router
