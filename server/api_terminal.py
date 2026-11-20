"""The phone-style terminal's own API (public) and the admin login / terminal-settings API.

    GET  /api/terminal                public  settings the terminal obeys (delay, pairs, timeframe, lot size, ...)
    GET  /api/terminal/quotes         public  the Quotes screen: bid / ask / day change / day low-high, all as of now - delay
    POST /api/admin/login   {username, password}      POST /api/admin/logout      GET /api/admin/me
    GET  /api/admin/terminal          admin   the same settings          PUT /api/admin/terminal   {delay_s: 60, ...}

Everything the terminal shows is read through a DataView pinned at (now - delay): it cannot reach newer data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from core import db
from core.dataview import DataView
from core.pips import pip_size
from server import settings
from server.auth import COOKIE, SESSION_SECONDS, client_id, require_admin

DESCRIPTIONS = {
    "EURUSD": "Euro vs US Dollar", "GBPUSD": "Great Britain Pound vs US Dollar", "USDJPY": "US Dollar vs Japanese Yen",
    "EURJPY": "Euro vs Japanese Yen", "GBPJPY": "Great Britain Pound vs Japanese Yen", "AUDUSD": "Australian Dollar vs US Dollar",
    "USDCAD": "US Dollar vs Canadian Dollar", "USDCHF": "US Dollar vs Swiss Franc", "NZDUSD": "New Zealand Dollar vs US Dollar",
    "EURGBP": "Euro vs Great Britain Pound", "AUDJPY": "Australian Dollar vs Japanese Yen", "CADJPY": "Canadian Dollar vs Japanese Yen",
}


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


def terminal_state(st) -> Dict[str, Any]:
    conn = db.connect(st.db_path)
    try:
        return settings.load(st.cfg, conn)
    finally:
        conn.close()


def public_view(st) -> Dict[str, Any]:
    s = terminal_state(st)
    cfg = st.cfg
    s["symbols"] = [x for x in s["symbols"] if x in cfg.symbols]
    s["broker_symbols"] = {x: x + cfg.symbol_suffix for x in s["symbols"]}
    s["descriptions"] = {x: DESCRIPTIONS.get(x, x) for x in s["symbols"]}
    s["timeframes"] = cfg.candles.timeframes
    s["delay"] = {"min": cfg.delay.min_seconds, "max": cfg.delay.max_seconds}
    return s


def compute_quotes(st, delay_s: float, tz_name: str, symbols) -> list:
    cfg = st.cfg
    cutoff = int(st.clock() - delay_s * 1000)
    tz = ZoneInfo(tz_name)
    conn = db.connect(st.db_path)
    out = []
    try:
        for sym in symbols:
            bs = sym + cfg.symbol_suffix
            dv = DataView(conn, bs, cutoff, st.cache, cfg.candles.price_side)
            lt = dv.last_tick()
            if lt is None:
                continue
            digits = db.get_digits(conn, bs)
            point = 10.0 ** -digits
            day = datetime.fromtimestamp(lt[0] / 1000, tz=timezone.utc).astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            day_start = int(day.timestamp() * 1000)
            # the day's low / high / reference are extended incrementally: the terminal polls this every second
            memo = st.quote_cache.get((sym, day_start))
            if memo is None or cutoff < memo["upto"]:                         # first look at this day, or the delay was lengthened
                prev = dv.with_cutoff(day_start - 1).last_tick()
                ref = prev[2] if prev else None
                if ref is None:                                               # no earlier data: measure from the day's FIRST tick (one indexed row)
                    first = dv.ticks_after((day_start - 1, 2 ** 31), 1)
                    ref = first[0][2] if first else lt[2]
                ext = dv.bid_extremes(day_start)
                memo = {"upto": cutoff, "ref": ref, "lo": ext[0] if ext else lt[2], "hi": ext[1] if ext else lt[2]}
            elif cutoff > memo["upto"]:
                ext = dv.bid_extremes(memo["upto"] + 1, cutoff)
                if ext:
                    memo["lo"], memo["hi"] = min(memo["lo"], ext[0]), max(memo["hi"], ext[1])
                memo["upto"] = cutoff
            st.quote_cache[(sym, day_start)] = memo
            ref, lo, hi = memo["ref"], memo["lo"], memo["hi"]
            chg = lt[2] - ref
            out.append({"symbol": bs, "bid": lt[2], "ask": lt[3], "t_ms": lt[0], "digits": digits, "pip": pip_size(digits),
                        "spread_pts": round((lt[3] - lt[2]) / point), "change_pts": round(chg / point),
                        "change_pct": (chg / ref * 100) if ref else 0.0, "low": lo, "high": hi})
    finally:
        conn.close()
    return out


def make_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/terminal")
    def terminal(request: Request):
        return public_view(request.app.state)

    @router.get("/api/terminal/quotes")
    def quotes(request: Request):
        st = request.app.state
        s = terminal_state(st)
        return {"delay_s": s["delay_s"], "cutoff_ms": int(st.clock() - s["delay_s"] * 1000),
                "quotes": compute_quotes(st, s["delay_s"], s["tz"], [x for x in s["symbols"] if x in st.cfg.symbols])}

    # ------------------------------------------------------------------ admin session
    @router.post("/api/admin/login")
    def login(body: LoginIn, request: Request, response: Response):
        st = request.app.state
        if st.auth is None:
            raise HTTPException(400, "this server runs without admin login")
        who = client_id(request)
        if st.auth.throttled(who):
            raise HTTPException(429, "too many failed attempts - wait a minute")
        if not st.auth.check(body.username, body.password):
            st.auth.record_failure(who)
            raise HTTPException(401, "wrong username or password")
        st.auth.clear_failures(who)
        response.set_cookie(COOKIE, st.auth.issue(), max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
        return {"ok": True, "username": st.auth.username}

    @router.post("/api/admin/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @router.get("/api/admin/me")
    def me(request: Request):
        st = request.app.state
        ok = (not st.require_admin) or st.auth.verify(request.cookies.get(COOKIE))
        return {"admin": ok, "username": st.auth.username if ok and st.require_admin else None}

    # ------------------------------------------------------------------ admin: terminal settings
    @router.get("/api/admin/terminal", dependencies=[Depends(require_admin)])
    def get_settings(request: Request):
        return {**public_view(request.app.state), "all_symbols": request.app.state.cfg.symbols}

    @router.put("/api/admin/terminal", dependencies=[Depends(require_admin)])
    def put_settings(request: Request, body: Dict[str, Any]):
        st = request.app.state
        conn = db.connect(st.db_path)
        try:
            try:
                new = settings.save(st.cfg, conn, body)
            except (ValueError, TypeError) as e:
                raise HTTPException(400, str(e)) from None
        finally:
            conn.close()
        if "delay_s" in body:
            st.engine.set_delay(new["delay_s"])          # the paper account's clock follows the terminal's delay
        return {**public_view(st), "all_symbols": st.cfg.symbols}

    return router
