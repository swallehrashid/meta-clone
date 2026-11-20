"""Delayed-live mode: a bot running on the paper engine, on the delayed clock.

`BotManager.heartbeat(now)` replaces the plain "advance the account clock" tick of the server. It walks the account clock
to each decision-grid time that has been reached (`engine.advance_to(d)`), calls the SAME `BotRunner.step(d)` the replay
uses, then carries on to now - delay. So replay and live take identical decisions (tests/test_bot_modes.py).

State that must survive a restart (which bot, its parameters, symbols, limits, "running") is kept in SQLite; the bot itself is
stateless by contract, so nothing else needs saving. After downtime only the last `MAX_CATCHUP` decision times are acted on -
it never trades on stale data it missed.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from collector.market_hours import market_open
from core.candles import CandleCache
from core.config import Config
from engine.account import Engine
from bot.interface import CloseAll
from bot.runner import BotRunner, Event, RiskLimits
from bot.strategies import REGISTRY, available, make_bot

log = logging.getLogger("bot.live")
MAX_CATCHUP = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bot_log (id INTEGER PRIMARY KEY AUTOINCREMENT, t_ms INTEGER, kind TEXT, detail TEXT, data TEXT);
"""


class BotManager:
    def __init__(self, cfg: Config, engine: Engine, clock: Optional[Callable[[], int]] = None):
        self.cfg, self.engine = cfg, engine
        self.clock = clock or (lambda: int(time.time() * 1000))      # real time: the live edge a forward window may never pass
        self.conn = engine.conn
        with engine.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        self.runner: Optional[BotRunner] = None
        engine.advance_hook = self._walk
        self.config: Optional[Dict[str, Any]] = None
        self.cache = CandleCache()
        self._last_decided = 0
        self.started_ms: Optional[int] = None
        self._resume()

    # ------------------------------------------------------------------ persistence
    def _get(self, key: str) -> Optional[str]:
        r = self.conn.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def _set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO bot_state (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()

    def _log(self, ev: Event) -> None:
        self.conn.execute("INSERT INTO bot_log (t_ms, kind, detail, data) VALUES (?,?,?,?)", (ev.t_ms, ev.kind, ev.detail, json.dumps(ev.data)))
        self.conn.execute("DELETE FROM bot_log WHERE id <= (SELECT MAX(id) FROM bot_log) - 500")
        self.conn.commit()

    def _resume(self) -> None:
        raw = self._get("config")
        if raw and self._get("running") == "1":
            try:
                c = json.loads(raw)
                self._build(c["name"], c["params"], c["symbols"], c["limits"], c.get("forward_s", 0))
                log.info("resumed bot %s", c["name"])
            except Exception:                                   # noqa: BLE001
                log.exception("could not resume the bot; leaving it stopped")
                self._set("running", "0")

    def _build(self, name: str, params: dict, symbols: Sequence[str], limits: dict, forward_s: int = 0) -> None:
        bot = make_bot(name, params)
        if forward_s > self.engine.delay_s:
            raise ValueError(f"forward data can only reach as far as the delay hides: forward {forward_s}s > delay {self.engine.delay_s:g}s "
                             "(raise the terminal delay first)")
        self.runner = BotRunner(self.cfg, self.engine, bot, list(symbols), RiskLimits.from_dict(limits), self.cache, on_event=self._log,
                                forward_s=forward_s, forward_limit=self.clock)
        self.config = {"name": name, "params": bot.params, "symbols": list(symbols), "limits": limits, "forward_s": forward_s}
        self._last_decided = (self.engine.t_engine // bot.interval_ms) * bot.interval_ms

    # ------------------------------------------------------------------ control
    def start(self, name: str, params: Optional[dict] = None, symbols: Optional[Sequence[str]] = None, limits: Optional[dict] = None,
              now_ms: Optional[int] = None, forward_s: int = 0) -> dict:
        with self.engine.lock:
            if self.runner is not None and not self.runner.stats.halted:
                raise ValueError("a bot is already running - stop it first")
            if now_ms is not None and self.engine.t_engine == 0:        # a freshly reset account: bring its clock to the chart time first
                self.engine.advance_to(int(now_ms - self.engine.delay_s * 1000))
            syms = list(symbols) if symbols else list(self.cfg.broker_symbols[:1])
            self._build(name, params or {}, syms, limits or {}, forward_s)          # validates everything before anything is saved
            self._set("config", json.dumps(self.config))
            self._set("running", "1")
            self.started_ms = self.engine.t_engine
            self._log(Event(self.engine.t_engine, "info", f"started {name}" + (f" WITH {forward_s}s FORWARD DATA (trades tagged peeked)" if forward_s else " (blind)"),
                            {"params": self.config["params"], "forward_s": forward_s}))
            return self.status()

    def stop(self, close_positions: bool = False) -> dict:
        with self.engine.lock:
            if self.runner is not None:
                if close_positions and self.engine.t_engine:
                    try:
                        self.runner._run(CloseAll(), self.runner._context(self.engine.t_engine), self.engine.t_engine, 0, 0.0)
                    except Exception:                           # noqa: BLE001
                        log.exception("closing the bot's positions failed")
                self._log(Event(self.engine.t_engine, "info", "stopped"))
            self._set("running", "0")
            self.runner = None
            return self.status()

    # ------------------------------------------------------------------ the clock
    def _walk(self, cutoff: int) -> int:
        """Installed as engine.advance_hook: whoever moves the account clock (heartbeat, API poll, a human's order), time passes
        through here. Walk to each decision-grid time reached, let the bot decide with the engine exactly there, then go on."""
        eng, r = self.engine, self.runner
        n = 0
        if r is not None and not r.stats.halted:
            iv = r.bot.interval_ms
            last = cutoff // iv * iv
            for d in range(last - (MAX_CATCHUP - 1) * iv, last + 1, iv):      # after downtime: only the most recent ones
                if d <= self._last_decided:
                    continue
                self._last_decided = d
                if d >= eng.t_engine and market_open(d):
                    n += eng._advance_raw(d)
                    r.step(d)
                    if r.stats.halted:
                        break
        return n + eng._advance_raw(cutoff)

    def heartbeat(self, now_ms: int) -> None:
        with self.engine.lock:
            self.engine.advance_to(int(now_ms - self.engine.delay_s * 1000))

    # ------------------------------------------------------------------ reading
    def status(self) -> dict:
        r = self.runner
        base = {"available": available(), "running": bool(r and not r.stats.halted), "halted": bool(r and r.stats.halted)}
        if r is None:
            return base | {"bot": None}
        st = r.stats
        return base | {"bot": self.config, "stats": {"steps": st.steps, "decisions_with_actions": st.decisions_with_actions, "opened": st.opened,
                                                      "closed_by_bot": st.closed_by_bot, "rejected": st.rejected, "errors": st.errors,
                                                      "halt_reason": st.halt_reason, "last_decision_ms": self._last_decided or None},
                       "limits": asdict_limits(r.limits)}

    def log_view(self, limit: int = 100) -> List[dict]:
        rows = self.conn.execute("SELECT id, t_ms, kind, detail, data FROM bot_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": i, "t_ms": t, "kind": k, "detail": d, "data": json.loads(x or "{}")} for i, t, k, d, x in rows]


def asdict_limits(l: RiskLimits) -> dict:
    from dataclasses import asdict
    return asdict(l)
