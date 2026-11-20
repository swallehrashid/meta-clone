"""Runs a bot against the paper engine.

`BotRunner.step(t)` is the ONE place decisions are taken and executed. Both modes call it:
  * replay (`run_replay`): fast, over stored days, on a throw-away in-memory account;
  * delayed live (bot/live.py): once per decision-grid time as the account clock advances.
So the same bot trades identically in both (tests/test_bot_modes.py proves it). A future "MT5 demo with real fills"
mode only needs another executor behind the same `step` - it is not built (it needs a real feed).

Safety: `RiskLimits` are enforced HERE, outside the bot, so a buggy bot cannot exceed them; a bot that raises, or is
refused too often, is halted (kill switch). Orders are always `source="bot"`. A bot never sees Peek data, and unless the run asks for
forward data (`forward_s > 0`, a training / analysis run) its orders are `peeked=False`; a forward run tags EVERY order `peeked=True`.
"""
from __future__ import annotations

import collections
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from collector.market_hours import market_open
from core.candles import CandleCache
from core.config import Config
from core.dataview import DataView
from core.pips import pip_size
from engine.account import Engine
from engine.orders import UNSET as ENGINE_UNSET, EngineError
from bot.interface import (AccountInfo, Action, Bot, CancelOrder, Close, CloseAll, Context, ForwardView, GuardedView, Modify,
                           OrderInfo, Open, PositionInfo, Quote, UNSET)

log = logging.getLogger("bot")


@dataclass
class RiskLimits:
    max_open_positions: int = 3          # across all symbols
    max_volume_per_order: float = 1.0    # lots
    max_total_volume: float = 3.0        # lots open at once
    max_daily_loss_usd: float = 0.0      # 0 = off; otherwise stop opening (and close all) after losing this much in a day
    max_orders_per_hour: int = 60
    max_consecutive_errors: int = 5      # bot exceptions in a row before it is halted
    max_rejections_in_a_row: int = 20    # engine refusals in a row before it is halted

    @staticmethod
    def from_dict(d: Optional[dict]) -> "RiskLimits":
        base = RiskLimits()
        for k, v in (d or {}).items():
            if not hasattr(base, k):
                raise ValueError(f"unknown risk limit {k!r}")
            setattr(base, k, type(getattr(base, k))(v))
        return base


@dataclass
class Event:
    t_ms: int
    kind: str                # open | close | modify | cancel | reject | error | halt | info
    detail: str
    data: dict = field(default_factory=dict)


@dataclass
class RunStats:
    steps: int = 0
    decisions_with_actions: int = 0
    opened: int = 0
    closed_by_bot: int = 0
    rejected: int = 0
    errors: int = 0
    halted: bool = False
    halt_reason: str = ""


class BotRunner:
    def __init__(self, cfg: Config, engine: Engine, bot: Bot, symbols: Sequence[str], limits: Optional[RiskLimits] = None,
                 cache: Optional[CandleCache] = None, on_event: Optional[Callable[[Event], None]] = None,
                 forward_s: int = 0, forward_limit: Optional[Callable[[], int]] = None):
        bad = [s for s in symbols if s not in cfg.broker_symbols]
        if bad:
            raise ValueError(f"unknown symbols {bad}")
        if forward_s < 0 or forward_s > 3600:
            raise ValueError("forward_s must be between 0 and 3600")
        if bot.needs_forward and forward_s <= 0:
            raise ValueError(f"{bot.name} is a teacher: it needs forward data (set forward_s > 0)")
        # forward_s > 0 = a TRAINING / ANALYSIS run: the bot may see the next forward_s seconds. Every order is then tagged
        # peeked, so these results can never be mistaken for blind ones. `forward_limit` (live mode) caps the window at real time.
        self.forward_s, self.forward_limit = int(forward_s), forward_limit
        self.cfg, self.engine, self.bot = cfg, engine, bot
        self.symbols = tuple(symbols)
        self.limits = limits or RiskLimits()
        self.cache = cache or CandleCache()
        self.on_event = on_event
        self.stats = RunStats()
        self.events: Deque[Event] = collections.deque(maxlen=500)
        self._consec_errors = self._consec_rejects = 0
        self._orders: Deque[int] = collections.deque()           # timestamps of recent order requests
        self._last_deal_id = 0
        self._day_key: Optional[str] = None
        self._day_pnl = 0.0
        self._digits: Dict[str, int] = {}
        self._tz = ZoneInfo(cfg.display_timezone)

    # ------------------------------------------------------------------ events
    def _emit(self, t_ms: int, kind: str, detail: str, **data) -> None:
        ev = Event(t_ms, kind, detail, data)
        self.events.append(ev)
        if self.on_event:
            self.on_event(ev)

    def halt(self, t_ms: int, reason: str) -> None:
        if not self.stats.halted:
            self.stats.halted, self.stats.halt_reason = True, reason
            self._emit(t_ms, "halt", reason)
            log.warning("bot halted: %s", reason)

    # ------------------------------------------------------------------ context
    def _digits_of(self, sym: str) -> int:
        if sym not in self._digits:
            self._digits[sym] = self.engine._digits(sym)
        return self._digits[sym]

    def _context(self, t: int) -> Context:
        views, quotes, digits, fwd = {}, {}, {}, {}
        for s in self.symbols:
            dv = DataView(self.engine.conn, s, t, self.cache, self.cfg.candles.price_side)
            views[s] = GuardedView(dv)
            digits[s] = self._digits_of(s)
            tk = dv.last_tick()
            quotes[s] = None if tk is None else Quote(tk[0], tk[2], tk[3], (tk[3] - tk[2]) / pip_size(digits[s]))
            if self.forward_s:
                want = t + self.forward_s * 1000
                fcut = want if self.forward_limit is None else min(want, self.forward_limit())
                rows = DataView(self.engine.conn, s, max(fcut, t), None, self.cfg.candles.price_side).ticks(t + 1, fcut) if fcut > t else []
                fwd[s] = ForwardView(rows, self.cfg.candles.price_side, t, self.forward_s, fcut == want and market_open(fcut))
        acct = self.engine.account()
        mine = [p for p in self.engine.positions_view() if p["source"] == "bot" and p["symbol"] in self.symbols]
        orders = [o for o in self.engine.orders_view() if o["source"] == "bot" and o["kind"] == "open" and o["symbol"] in self.symbols]
        return Context(
            t_ms=t, symbols=self.symbols,
            account=AccountInfo(acct["balance"], acct["equity"], acct["margin"], acct["free_margin"], acct["margin_level"], acct["unrealized"]),
            positions=tuple(PositionInfo(p["id"], p["symbol"], p["side"], p["volume"], p["open_price"], p["open_ms"], p["sl"], p["tp"],
                                         p["price"], p["pips"], p["profit"]) for p in mine),
            orders=tuple(OrderInfo(o["id"], o["symbol"], o["side"], o["type"], o["volume"], o["price"], o["sl"], o["tp"]) for o in orders),
            cfg=self.cfg, _views=views, _digits=digits, _quotes=quotes, forward_s=self.forward_s, _forward=fwd)

    # ------------------------------------------------------------------ risk bookkeeping
    def _update_day_pnl(self, t: int) -> None:
        key = datetime.fromtimestamp(t / 1000, tz=timezone.utc).astimezone(self._tz).strftime("%Y-%m-%d")
        if key != self._day_key:
            self._day_key, self._day_pnl = key, 0.0
        for d in reversed(self.engine.history(30)):                   # oldest first
            if d["id"] > self._last_deal_id:
                self._last_deal_id = d["id"]
                if d["source"] == "bot":
                    self._day_pnl += d["net"]

    def _daily_loss_hit(self, ctx: Context) -> bool:
        lim = self.limits.max_daily_loss_usd
        return lim > 0 and (self._day_pnl + sum(p.profit or 0.0 for p in ctx.positions)) <= -lim

    def _check_open(self, a: Open, ctx: Context, t: int, pending_open: int, pending_volume: float) -> Optional[str]:
        L = self.limits
        if a.symbol not in self.symbols:
            return f"{a.symbol} is not one of this bot's symbols"
        if a.volume > L.max_volume_per_order + 1e-9:
            return f"volume {a.volume} exceeds the per-order limit {L.max_volume_per_order}"
        if len(ctx.positions) + len(ctx.orders) + pending_open >= L.max_open_positions:
            return f"already at the open-position limit ({L.max_open_positions})"
        if sum(p.volume for p in ctx.positions) + pending_volume + a.volume > L.max_total_volume + 1e-9:
            return f"total open volume would exceed {L.max_total_volume} lots"
        while self._orders and t - self._orders[0] > 3_600_000:
            self._orders.popleft()
        if len(self._orders) >= L.max_orders_per_hour:
            return f"order rate limit ({L.max_orders_per_hour}/hour)"
        if self._daily_loss_hit(ctx):
            return f"daily loss limit (${L.max_daily_loss_usd:g}) reached"
        return None

    # ------------------------------------------------------------------ one decision
    def step(self, t: int) -> List[Event]:
        """Decide and act at chart time t. The engine must already be at t (engine.advance_to(t))."""
        before = len(self.events)
        if self.stats.halted:
            return []
        self.stats.steps += 1
        self._update_day_pnl(t)
        ctx = self._context(t)
        try:
            actions = list(self.bot.decide(ctx) or [])
            self._consec_errors = 0
        except Exception as e:                                          # noqa: BLE001 - a bot may never crash the lab
            self.stats.errors += 1
            self._consec_errors += 1
            self._emit(t, "error", f"{type(e).__name__}: {e}")
            log.exception("bot decide() failed")
            if self._consec_errors >= self.limits.max_consecutive_errors:
                self.halt(t, f"{self._consec_errors} errors in a row")
            return list(self.events)[before:]
        if actions:
            self.stats.decisions_with_actions += 1
        if self._daily_loss_hit(ctx) and ctx.positions:                  # stop-loss for the whole day
            self._emit(t, "info", "daily loss limit reached: closing everything")
            self._run(CloseAll(), ctx, t, 0, 0.0)
            actions = [a for a in actions if not isinstance(a, Open)]
        pending_open, pending_vol = 0, 0.0
        for a in actions:
            ok = self._run(a, ctx, t, pending_open, pending_vol)
            if ok and isinstance(a, Open):
                pending_open += 1
                pending_vol += a.volume
            if self._consec_rejects >= self.limits.max_rejections_in_a_row:
                last = next((e.detail for e in reversed(self.events) if e.kind == "reject"), "")
                broke = " - the account is out of margin" if "not enough money" in last else ""
                self.halt(t, f"{self._consec_rejects} refused orders in a row (last: {last[:90]}){broke}")
                break
        return list(self.events)[before:]

    def _run(self, a: Any, ctx: Context, t: int, pending_open: int, pending_vol: float) -> bool:
        e = self.engine
        try:
            if isinstance(a, Open):
                why = self._check_open(a, ctx, t, pending_open, pending_vol)
                if why:
                    raise EngineError("risk_limit", why)
                sl, tp = self._levels(a, ctx)
                self._orders.append(t)
                r = e.place_order(a.symbol, a.side, a.type, a.volume, a.price, sl, tp, at_ms=t, source="bot", peeked=self.forward_s > 0,
                                  comment=a.comment or self.bot.name)
                self.stats.opened += 1
                self._emit(t, "open", f"{a.side} {a.volume} {a.symbol} ({r['status']})", id=r["id"], price=r.get("price"))
            elif isinstance(a, Close):
                self._own(a.position_id)
                e.close(a.position_id, a.volume, at_ms=t)
                self.stats.closed_by_bot += 1
                self._emit(t, "close", f"close #{a.position_id}", id=a.position_id)
            elif isinstance(a, CloseAll):
                n = 0
                for p in [p for p in e.positions_view() if p["source"] == "bot" and a.symbol in (None, p["symbol"])]:
                    e.close(p["id"], None, at_ms=t)
                    n += 1
                self.stats.closed_by_bot += n
                self._emit(t, "close", f"close all ({n})")
            elif isinstance(a, Modify):
                self._own(a.position_id)
                kw = {k: (ENGINE_UNSET if v is UNSET else v) for k, v in (("sl", a.sl), ("tp", a.tp))}
                e.modify(a.position_id, kind="position", at_ms=t, **kw)
                self._emit(t, "modify", f"modify #{a.position_id}", id=a.position_id)
            elif isinstance(a, CancelOrder):
                o = next((o for o in e.orders_view() if o["id"] == a.order_id and o["source"] == "bot"), None)
                if o is None:
                    raise EngineError("no_order", f"order {a.order_id} is not one of this bot's working orders")
                e.cancel_order(a.order_id, at_ms=t)
                self._emit(t, "cancel", f"cancel #{a.order_id}", id=a.order_id)
            else:
                raise EngineError("bad_action", f"not an action: {a!r}")
            self._consec_rejects = 0
            return True
        except EngineError as ex:
            self.stats.rejected += 1
            if ex.code != "risk_limit":          # hitting OUR limits is the limits working, not the bot malfunctioning
                self._consec_rejects += 1
            self._emit(t, "reject", f"{type(a).__name__}: {ex.message}", code=ex.code)
            return False

    def _own(self, position_id: int) -> None:
        p = self.engine.positions.get(position_id)
        if p is None or p.source != "bot":
            raise EngineError("not_yours", f"position {position_id} is not one of this bot's positions")

    def _levels(self, a: Open, ctx: Context) -> Tuple[Optional[float], Optional[float]]:
        if (a.sl is not None and a.sl_pips is not None) or (a.tp is not None and a.tp_pips is not None):
            raise EngineError("bad_order", "give SL/TP either as a price or in pips, not both")
        q = ctx.quote(a.symbol)
        if q is None:
            raise EngineError("no_quote", f"no quote for {a.symbol}")
        digits, pip = ctx.digits(a.symbol), ctx.pip(a.symbol)
        ref = a.price if a.type != "market" else (q.bid if a.side == "buy" else q.ask)      # exit-side price
        sgn = 1 if a.side == "buy" else -1
        sl = a.sl if a.sl_pips is None else round(ref - sgn * a.sl_pips * pip, digits)
        tp = a.tp if a.tp_pips is None else round(ref + sgn * a.tp_pips * pip, digits)
        return sl, tp


# ============================================================================================ replay
@dataclass
class RunResult:
    bot: str
    params: dict
    symbols: Tuple[str, ...]
    t_from_ms: int
    t_to_ms: int
    stats: RunStats
    deals: List[dict]
    equity: List[dict]
    account: dict
    events: List[Event]
    initial_balance: float
    forward_s: int = 0

    def summary(self) -> dict:
        d = self.deals
        wins = [x for x in d if x["net"] > 0]
        losses = [x for x in d if x["net"] <= 0]
        net = sum(x["net"] for x in d)
        eq = [p["equity"] for p in self.equity] or [self.initial_balance]
        peak, dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v)
            dd = max(dd, (peak - v) / peak * 100 if peak else 0.0)
        gp, gl = sum(x["net"] for x in wins), -sum(x["net"] for x in losses)
        return {"bot": self.bot, "forward_s": self.forward_s, "trades": len(d), "net_usd": net, "return_pct": net / self.initial_balance * 100,
                "win_pct": len(wins) / len(d) * 100 if d else None, "avg_net_pips": sum(x["net_pips"] for x in d) / len(d) if d else None,
                "avg_pips_gross_of_commission": sum(x["pips"] for x in d) / len(d) if d else None,
                "profit_factor": gp / gl if gl > 0 else None, "max_drawdown_pct": dd,
                "avg_hold_s": sum(x["duration_s"] for x in d) / len(d) if d else None,
                "exit_reasons": dict(collections.Counter(x["reason"] for x in d)),
                "steps": self.stats.steps, "rejected": self.stats.rejected, "errors": self.stats.errors,
                "halted": self.stats.halted, "halt_reason": self.stats.halt_reason}


def decision_times(t_from: int, t_to: int, interval_ms: int):
    """Grid times in [t_from, t_to] at which the market is open."""
    t = -(-t_from // interval_ms) * interval_ms
    while t <= t_to:
        if market_open(t):
            yield t
        t += interval_ms


def run_replay(cfg: Config, conn, bot: Bot, symbols: Sequence[str], t_from_ms: int, t_to_ms: int,
               limits: Optional[RiskLimits] = None, balance: Optional[float] = None, close_at_end: bool = True,
               progress: Optional[Callable[[int, int], None]] = None, forward_s: int = 0) -> RunResult:
    """Replay stored history through a bot and a FRESH paper account on `conn`.

    `conn` must be a sandbox (tools.sandbox.open_sandbox) or another throw-away database: the account tables are wiped.
    """
    eng = Engine(cfg, conn)
    eng.reset(balance=balance if balance is not None else cfg.engine.starting_balance)
    runner = BotRunner(cfg, eng, bot, symbols, limits, forward_s=forward_s)
    last_t, n = None, 0
    total = max(1, (t_to_ms - t_from_ms) // bot.interval_ms)
    for t in decision_times(t_from_ms, t_to_ms, bot.interval_ms):
        eng.advance_to(t)
        runner.step(t)
        last_t = t
        n += 1
        if progress and n % 2000 == 0:
            progress(n, total)
        if runner.stats.halted:
            break
    if last_t is not None:
        eng.advance_to(last_t)
        if close_at_end and eng.positions:
            try:
                eng.close_all(None, at_ms=last_t)
            except EngineError:
                pass
    return RunResult(bot.name, dict(bot.params), tuple(symbols), t_from_ms, t_to_ms, runner.stats, list(reversed(eng.history(1_000_000))),
                     eng.equity_curve(0, 1_000_000), eng.account(), list(runner.events), eng.initial_balance, forward_s)
