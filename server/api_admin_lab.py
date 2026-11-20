"""Bot lab (back office, admin only): train, replay and compare bots, with or without forward data.

    GET  /api/admin/lab/bots               bots (with parameter specs), trained models, symbols
    POST /api/admin/lab/replay             start a replay job over stored days   {bot, params, symbols, date_from, date_to, forward_s, note}
    POST /api/admin/lab/train              watch the teacher -> learn -> test blind {name, symbols, train_from, train_to, test_from, test_to, ...}
    GET  /api/admin/lab/runs               every job so far (newest first), summary columns for comparing iterations
    GET  /api/admin/lab/runs/{id}          one job in full (result, progress)      DELETE /api/admin/lab/runs/{id}
    GET  /api/admin/lab/models             trained models           GET /api/admin/lab/models/{name} (weights)   DELETE ...
    POST /api/admin/lab/live               run a bot on the terminal's paper account (the chart you see on the phone UI)
    GET  /api/admin/lab/live               the live bot's status and the paper account's trades split by clean / peeked

Jobs run in a background thread, one at a time, on a READ-ONLY sandbox of the history (the ticks file and the paper account
are never touched). Forward data is explicit in every request (`forward_s`); runs that used it are labelled and their trades
are tagged peeked, so a forward-fed result can never be filed as a blind one.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from bot import model as bmodel
from bot.evaluate import evaluate
from bot.runner import RiskLimits, run_replay
from bot.strategies import available, make_bot
from bot.train import day_range, run_pipeline
from core import db
from tools.sandbox import open_sandbox

SCHEMA = """CREATE TABLE IF NOT EXISTS lab_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, status TEXT, title TEXT, forward_s INTEGER, request TEXT, result TEXT,
  error TEXT, created_ms INTEGER, finished_ms INTEGER)"""
_lock = threading.Lock()
_progress: Dict[int, str] = {}
_running: Optional[int] = None


class ReplayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot: str
    params: Dict[str, Any] = {}
    symbols: Optional[List[str]] = None
    date_from: str
    date_to: str
    forward_s: int = 0
    limits: Dict[str, Any] = {}
    balance: float = 100_000.0
    note: str = ""


class TrainIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    symbols: Optional[List[str]] = None
    train_from: str
    train_to: str
    test_from: Optional[str] = None
    test_to: Optional[str] = None
    forward_s: int = 60
    hold_s: int = 60
    min_edge_pips: float = 1.0
    tp_fraction: float = 0.8
    sl_buffer_pips: float = 1.0
    l2: float = 5.0
    sl_pips: float = 5.0
    tp_pips: float = 3.0
    volume: float = 0.1


class LiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot: str
    params: Dict[str, Any] = {}
    symbols: Optional[List[str]] = None
    forward_s: int = 0
    limits: Dict[str, Any] = {}


def _conn(st):
    c = db.connect(st.db_path)
    c.executescript(SCHEMA)
    return c


def _syms(st, symbols: Optional[List[str]]) -> List[str]:
    cfg = st.cfg
    out = [s + cfg.symbol_suffix if s in cfg.symbols else s for s in (symbols or cfg.symbols)]
    bad = [s for s in out if s not in cfg.broker_symbols]
    if bad:
        raise HTTPException(400, f"unknown symbols {bad}")
    return out


def _start(st, kind: str, title: str, forward_s: int, request: dict, work) -> int:
    """Register a job and run `work(progress) -> result dict` in a thread. One job at a time."""
    global _running
    with _lock:
        if _running is not None:
            raise HTTPException(409, f"job #{_running} is still running - one at a time")
        c = _conn(st)
        try:
            cur = c.execute("INSERT INTO lab_runs (kind, status, title, forward_s, request, created_ms) VALUES (?,?,?,?,?,?)",
                            (kind, "running", title, forward_s, json.dumps(request), st.clock()))
            c.commit()
            jid = cur.lastrowid
        finally:
            c.close()
        _running = jid

    def run():
        global _running
        status, result, error = "done", None, None
        try:
            result = work(lambda m: _progress.__setitem__(jid, m))
        except Exception as e:                                           # noqa: BLE001 - the job reports it, the server lives on
            status, error = "failed", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        c = _conn(st)
        try:
            c.execute("UPDATE lab_runs SET status=?, result=?, error=?, finished_ms=? WHERE id=?",
                      (status, json.dumps(result, default=str) if result is not None else None, error, int(time.time() * 1000), jid))
            c.commit()
        finally:
            c.close()
        with _lock:
            _running = None
            _progress.pop(jid, None)

    threading.Thread(target=run, daemon=True, name=f"lab-job-{jid}").start()
    return jid


def _row(r, full: bool) -> dict:
    jid, kind, status, title, fwd, req, res, err, created, finished = r
    d = {"id": jid, "kind": kind, "status": status, "title": title, "forward_s": fwd, "created_ms": created, "finished_ms": finished,
         "error": err, "progress": _progress.get(jid), "request": json.loads(req or "{}")}
    result = json.loads(res) if res else None
    if full:
        d["result"] = result
    elif result:                                                    # compact columns for comparing iterations
        if kind == "replay":
            s = result["evaluation"]["summary"]
            de = result["evaluation"].get("direction_edge_pips") or {}
            d["summary"] = {k: s.get(k) for k in ("trades", "net_usd", "avg_net_pips", "win_pct", "profit_factor", "max_drawdown_pct")} | {"direction_z": de.get("z")}
        else:
            t = result.get("test") or {}
            d["summary"] = {"verdict": result.get("verdict"), "direction_auc": result["fit"].get("validation_auc_direction"),
                            "blind_avg_net_pips": (t.get("learned_blind", {}).get("summary") or {}).get("avg_net_pips"),
                            "random_avg_net_pips": (t.get("random_null", {}).get("summary") or {}).get("avg_net_pips"),
                            "teacher_avg_net_pips": (t.get("teacher_forward", {}).get("summary") or {}).get("avg_net_pips")}
    return d


SEL = "SELECT id, kind, status, title, forward_s, request, result, error, created_ms, finished_ms FROM lab_runs"


def _downsample(points: List[dict], n: int = 400) -> List[dict]:
    if len(points) <= n:
        return points
    step = len(points) / n
    return [points[int(i * step)] for i in range(n)] + [points[-1]]


def make_router() -> APIRouter:
    router = APIRouter(prefix="/api/admin/lab")

    @router.get("/bots")
    def bots(request: Request):
        st = request.app.state
        return {"bots": available(), "models": bmodel.list_models(), "symbols": st.cfg.symbols,
                "delay_s": st.engine.delay_s, "forward_note": "forward_s > 0 lets a bot see the next seconds; its trades are tagged peeked."}

    @router.get("/range")
    def data_range(request: Request):
        """First / last stored tick per symbol (UTC ms) - what the date pickers default to."""
        st = request.app.state
        c = db.connect(st.db_path)
        try:
            out = {}
            for s in st.cfg.broker_symbols:
                r = db.tick_range(c, s)
                if r:
                    out[s] = {"from_ms": r[0], "to_ms": r[1]}
            return {"symbols": out, "tz": st.cfg.display_timezone}
        finally:
            c.close()

    # ------------------------------------------------------------------ jobs
    @router.post("/replay")
    def replay(body: ReplayIn, request: Request):
        st = request.app.state
        syms = _syms(st, body.symbols)
        try:
            bot = make_bot(body.bot, body.params)
            lo, hi = day_range(body.date_from, body.date_to, st.cfg.display_timezone)
            limits = RiskLimits.from_dict({"max_open_positions": 20, "max_total_volume": 200.0, "max_orders_per_hour": 10_000, **body.limits})
            if not 0 <= body.forward_s <= 3600:
                raise ValueError("forward_s must be between 0 and 3600")
            if bot.needs_forward and body.forward_s <= 0:
                raise ValueError(f"{bot.name} is a teacher: it needs forward data (set forward_s > 0)")
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        title = f"{bot.name} {body.date_from}..{body.date_to} " + (f"FORWARD {body.forward_s}s" if body.forward_s else "blind")

        def work(progress):
            conn = open_sandbox(str(Path(st.db_path).resolve()))
            try:
                res = run_replay(st.cfg, conn, make_bot(body.bot, body.params), syms, lo, hi, limits, body.balance, forward_s=body.forward_s,
                                 progress=lambda n, tot: progress(f"{n:,} decision steps"))
                progress("grading against the labels")
                ev = evaluate(conn, st.cfg, res)
            finally:
                conn.close()
            return {"evaluation": ev, "equity": _downsample(res.equity), "deals": res.deals[-300:], "forward_s": body.forward_s,
                    "tagged_peeked": body.forward_s > 0, "symbols": syms, "params": res.params}

        return {"id": _start(st, "replay", title, body.forward_s, body.model_dump(), work)}

    @router.post("/train")
    def train(body: TrainIn, request: Request):
        st = request.app.state
        syms = _syms(st, body.symbols)
        try:
            bmodel._safe(body.name)
            tr = day_range(body.train_from, body.train_to, st.cfg.display_timezone)
            te = day_range(body.test_from, body.test_to, st.cfg.display_timezone) if body.test_from and body.test_to else None
            if te and te[0] <= tr[1]:
                raise ValueError("the test days must come AFTER the training days (otherwise the blind test is not blind)")
            if not 1 <= body.forward_s <= 3600 or body.hold_s > body.forward_s:
                raise ValueError("forward_s must be 1..3600 and at least hold_s")
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        kw = body.model_dump(exclude={"name", "symbols", "train_from", "train_to", "test_from", "test_to"})

        def work(progress):
            return run_pipeline(st.cfg, st.db_path, syms, tr, te, body.name, progress=progress, **kw)

        return {"id": _start(st, "train", f"train {body.name} {body.train_from}..{body.train_to}", body.forward_s, body.model_dump(), work)}

    @router.get("/runs")
    def runs(request: Request, limit: int = 50):
        c = _conn(request.app.state)
        try:
            return {"runs": [_row(r, False) for r in c.execute(f"{SEL} ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))],
                    "running": _running}
        finally:
            c.close()

    @router.get("/runs/{rid}")
    def run_one(rid: int, request: Request):
        c = _conn(request.app.state)
        try:
            r = c.execute(f"{SEL} WHERE id=?", (rid,)).fetchone()
        finally:
            c.close()
        if r is None:
            raise HTTPException(404, "no such run")
        return _row(r, True)

    @router.delete("/runs/{rid}")
    def run_delete(rid: int, request: Request):
        if _running == rid:
            raise HTTPException(409, "that job is still running")
        c = _conn(request.app.state)
        try:
            c.execute("DELETE FROM lab_runs WHERE id=?", (rid,))
            c.commit()
        finally:
            c.close()
        return {"ok": True}

    # ------------------------------------------------------------------ models
    @router.get("/models")
    def models():
        return {"models": bmodel.list_models()}

    @router.get("/models/{name}")
    def model_one(name: str):
        try:
            m = bmodel.LinearModel.load(name)
        except ValueError as e:
            raise HTTPException(404, str(e)) from None
        return {"name": name, "meta": m.meta, "weights": m.explain()}

    @router.delete("/models/{name}")
    def model_delete(name: str):
        try:
            p = bmodel.models_dir() / f"{bmodel._safe(name)}.json"
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        p.unlink(missing_ok=True)
        return {"ok": True}

    # ------------------------------------------------------------------ live (the terminal's own paper account)
    @router.post("/live")
    def live_start(body: LiveIn, request: Request):
        st = request.app.state
        syms = _syms(st, body.symbols)
        try:
            st.bots.start(body.bot, body.params, syms, body.limits, now_ms=st.clock(), forward_s=body.forward_s)
        except ValueError as e:
            raise HTTPException(409 if "already" in str(e) else 400, str(e)) from None
        return live_status(request)

    @router.get("/live")
    def live_status(request: Request):
        st = request.app.state
        m = st.bots
        deals = st.engine.history(500)
        bots = [d for d in deals if d["source"] == "bot"]
        split = {}
        for key, flag in (("clean", 0), ("peeked", 1)):
            g = [d for d in bots if int(d["peeked"] or 0) == flag]
            split[key] = {"trades": len(g), "net_usd": sum(d["net"] for d in g),
                          "avg_net_pips": (sum(d["net_pips"] for d in g) / len(g)) if g else None}
        return {**m.status(), "log": m.log_view(30), "account": st.engine.account(), "deals_by_tag": split,
                "terminal_delay_s": st.engine.delay_s}

    return router
