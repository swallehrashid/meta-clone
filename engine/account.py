"""The paper-trading engine: an MT5-like USD account that runs on the DELAYED clock.

Time model
  The account has its own clock, `t_engine`, that only moves forward. It is advanced to the chart's cutoff
  (now - delay) by `advance_to`, which replays every tick in (t_engine, cutoff] through a DataView - exactly
  what the chart shows - filling pending orders and triggering SL/TP in time order across symbols. Orders are
  placed "at" a chart time (`at_ms`); the engine first advances to it, so nothing can ever use a tick later
  than the clock. If the chart is moved back (a longer delay) the account clock does not rewind: new orders
  are refused until the chart catches up.

Conventions: see engine/fills.py. Margin is simple (volume x contract x base price / leverage, summed over
positions, no hedge offset). Commission is charged half at open and half at close. No swaps.

Persistence: everything lives in SQLite (tables acct_*), written through on every change, so a restart loses
nothing. All public methods are serialised by one lock.
"""
from __future__ import annotations

import heapq
import sqlite3
import threading
from functools import wraps
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from collector.market_hours import market_open
from core.config import Config
from core.dataview import DataView
from core.fx import usd_per_base, usd_per_quote
from engine import fills, pnl
from engine.orders import (UNSET, EngineError, Order, Position, TYPES, SIDES, check_levels,
                           check_pending_price, check_volume)

SCHEMA = """
CREATE TABLE IF NOT EXISTS acct (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  currency TEXT, leverage REAL, balance REAL, initial_balance REAL, t_engine_ms INTEGER,
  delay_s REAL, next_id INTEGER, peak_equity REAL, created_ms INTEGER
);
CREATE TABLE IF NOT EXISTS acct_orders (
  id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, type TEXT, volume REAL, price REAL, sl REAL, tp REAL,
  status TEXT, placed_ms INTEGER, fill_at_ms INTEGER, kind TEXT, target_position INTEGER, source TEXT,
  peeked INTEGER, session_id TEXT, comment TEXT, reason TEXT, position_id INTEGER
);
CREATE TABLE IF NOT EXISTS acct_positions (
  id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, volume REAL, open_price REAL, open_ms INTEGER, sl REAL, tp REAL,
  source TEXT, peeked INTEGER, session_id TEXT, comment TEXT
);
CREATE TABLE IF NOT EXISTS acct_deals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER, symbol TEXT, side TEXT, volume REAL,
  open_price REAL, open_ms INTEGER, close_price REAL, close_ms INTEGER, sl REAL, tp REAL,
  pips REAL, gross REAL, commission REAL, net REAL, net_pips REAL, reason TEXT, source TEXT, peeked INTEGER,
  duration_s REAL
);
CREATE TABLE IF NOT EXISTS acct_equity (t_ms INTEGER PRIMARY KEY, balance REAL, equity REAL, margin REAL);
CREATE TABLE IF NOT EXISTS acct_cursor (symbol TEXT PRIMARY KEY, t_ms INTEGER, seq INTEGER);
"""
ORDER_COLS = ["id", "symbol", "side", "type", "volume", "price", "sl", "tp", "status", "placed_ms", "fill_at_ms",
              "kind", "target_position", "source", "peeked", "session_id", "comment", "reason", "position_id",
              "stoplimit", "expire_ms", "done_ms"]
POS_COLS = ["id", "symbol", "side", "volume", "open_price", "open_ms", "sl", "tp", "source", "peeked",
            "session_id", "comment"]
DEAL_COLS = ["id", "position_id", "symbol", "side", "volume", "open_price", "open_ms", "close_price", "close_ms",
             "sl", "tp", "pips", "gross", "commission", "net", "net_pips", "reason", "source", "peeked", "duration_s"]
BATCH = 5000


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(acct_orders)")}
    for col, typ in (("stoplimit", "REAL"), ("expire_ms", "INTEGER"), ("done_ms", "INTEGER")):     # added later: migrate old files
        if col not in have:
            conn.execute(f"ALTER TABLE acct_orders ADD COLUMN {col} {typ}")
    conn.commit()


def _txn(fn):
    """Run a public method under the lock inside one transaction; roll back + reload state on failure."""
    @wraps(fn)
    def wrapper(self, *a, **kw):
        with self.lock:
            try:
                out = fn(self, *a, **kw)
                self.conn.commit()
                return out
            except BaseException:
                self.conn.rollback()
                self._load()
                raise
    return wrapper


class _Snap:
    """Quotes at one instant, read through the DataView and memoised for ONE valuation pass (one query per symbol)."""

    def __init__(self, eng: "Engine", t_ms: int):
        self._eng, self.t_ms = eng, t_ms
        self._ticks: Dict[str, Optional[tuple]] = {}

    def tick(self, sym: str):
        if sym not in self._ticks:
            self._ticks[sym] = self._eng._view(sym, self.t_ms).last_tick()
        return self._ticks[sym]

    def mid(self, sym: str) -> Optional[float]:
        tk = self.tick(sym)
        return None if tk is None else (tk[2] + tk[3]) / 2


class Engine:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, lock: Optional[threading.RLock] = None):
        self.cfg, self.conn, self.e = cfg, conn, cfg.engine
        self.lock = lock or threading.RLock()
        ensure_schema(conn)
        self._insts: Dict[str, pnl.Instrument] = {}
        self.advance_hook: Optional[Callable[[int], int]] = None
        self._now = 0                     # time of the tick / request being processed (stamps when an order stops working)
        self._load()

    # ================================================================= persistence
    def _load(self) -> None:
        c = self.conn
        row = c.execute("SELECT currency, leverage, balance, initial_balance, t_engine_ms, delay_s, next_id, "
                        "peak_equity, created_ms FROM acct WHERE id=1").fetchone()
        if row is None:
            c.execute("INSERT INTO acct VALUES (1, 'USD', ?, ?, ?, 0, ?, 1, ?, 0)",
                      (self.e.leverage, self.e.starting_balance, self.e.starting_balance, 300.0, self.e.starting_balance))
            c.commit()
            row = c.execute("SELECT currency, leverage, balance, initial_balance, t_engine_ms, delay_s, next_id, "
                            "peak_equity, created_ms FROM acct WHERE id=1").fetchone()
        (self.currency, self.leverage, self.balance, self.initial_balance, self.t_engine, self.delay_s,
         self.next_id, self.peak_equity, self.created_ms) = row
        self.orders: Dict[int, Order] = {r[0]: Order(*r[:-1]) for r in c.execute(
            f"SELECT {','.join(ORDER_COLS)} FROM acct_orders WHERE status='working' ORDER BY id")}
        self.positions: Dict[int, Position] = {r[0]: Position(*r) for r in c.execute(
            f"SELECT {','.join(POS_COLS)} FROM acct_positions ORDER BY id")}
        self.cursor: Dict[str, Tuple[int, int]] = {s: (t, q) for s, t, q in c.execute("SELECT symbol, t_ms, seq FROM acct_cursor")}
        last = c.execute("SELECT MAX(t_ms) FROM acct_equity").fetchone()[0]
        self._last_sample_ms = last if last is not None else -1
        self._next_risk_ms = 0
        self._dirty = False                  # balance/positions changed since the last equity sample

    def _save_acct(self) -> None:
        self.conn.execute("UPDATE acct SET leverage=?, balance=?, t_engine_ms=?, delay_s=?, next_id=?, peak_equity=? WHERE id=1",
                          (self.leverage, self.balance, self.t_engine, self.delay_s, self.next_id, self.peak_equity))

    def _save_order(self, o: Order) -> None:
        done = None if o.status == "working" else (self._now or self.t_engine)    # when it stopped working (engine time)
        self.conn.execute(f"INSERT OR REPLACE INTO acct_orders ({','.join(ORDER_COLS)}) VALUES ({','.join('?' * len(ORDER_COLS))})",
                          [getattr(o, c) for c in ORDER_COLS[:-1]] + [done])

    def _save_pos(self, p: Position) -> None:
        self.conn.execute(f"INSERT OR REPLACE INTO acct_positions ({','.join(POS_COLS)}) VALUES ({','.join('?' * len(POS_COLS))})",
                          [getattr(p, c) for c in POS_COLS])

    def _save_cursor(self, sym: str) -> None:
        t, q = self.cursor[sym]
        self.conn.execute("INSERT OR REPLACE INTO acct_cursor (symbol, t_ms, seq) VALUES (?,?,?)", (sym, t, q))

    # ================================================================= market data (always through DataView)
    def _view(self, symbol: str, t_ms: int) -> DataView:
        return DataView(self.conn, symbol, t_ms, price_side=self.cfg.candles.price_side)

    def _digits(self, symbol: str) -> int:
        from core import db
        return db.get_digits(self.conn, symbol)

    def _inst(self, symbol: str) -> pnl.Instrument:
        inst = self._insts.get(symbol)                       # digits never change: look them up once
        if inst is None:
            inst = self._insts[symbol] = pnl.Instrument.of(symbol, self.cfg.symbol_suffix, self._digits(symbol),
                                                           self.cfg.costs.contract_size)
        return inst

    def _quote_in_force(self, symbol: str, t_ms: int) -> Tuple[int, float, float]:
        """(tick time, bid, ask) of the newest tick at or before t_ms; refuses stale / missing quotes."""
        if not market_open(t_ms):
            raise EngineError("market_closed", "the market is closed")
        tk = self._view(symbol, t_ms).last_tick()
        if tk is None:
            raise EngineError("no_quote", f"no quote for {symbol}")
        if t_ms - tk[0] > self.e.max_quote_age_s * 1000:
            raise EngineError("no_quote", f"{symbol} has no recent quote (last one is {(t_ms - tk[0]) // 1000}s old)")
        return tk[0], tk[2], tk[3]

    def _mid_fn(self, t_ms: int) -> Callable[[str], Optional[float]]:
        """Mid prices as of t_ms, ALWAYS read through the DataView (never from replay state). Valuation is therefore
        a pure function of the data: identical after a restart and independent of how same-millisecond ticks of
        different symbols happened to be merged."""
        return _Snap(self, t_ms).mid

    # ================================================================= valuation
    def _mark(self, p: Position, t_ms: int, snap: Optional["_Snap"] = None) -> Optional[Tuple[float, float, float]]:
        """(exit-side price, pips, gross USD) of an open position at t_ms, from the newest tick <= t_ms.
        `snap` shares memoised quotes across the positions of one valuation pass."""
        snap = snap or _Snap(self, t_ms)
        v = snap.tick(p.symbol)
        if v is None:
            return None
        px = v[2] if p.side == "buy" else v[3]
        inst = self._inst(p.symbol)
        uq = usd_per_quote(p.symbol, self.cfg.symbol_suffix, snap.mid)
        return px, pnl.pips(inst, p.side, p.open_price, px), pnl.gross_usd(inst, p.side, p.volume, p.open_price, px, uq)

    def _margin_of(self, p: Position, t_ms: int, snap: Optional["_Snap"] = None) -> float:
        snap = snap or _Snap(self, t_ms)
        inst = self._inst(p.symbol)
        return pnl.margin_usd(inst, p.volume, usd_per_base(p.symbol, self.cfg.symbol_suffix, snap.mid), self.leverage)

    def _totals(self, t_ms: int) -> Tuple[float, float, float]:
        """(equity, used margin, unrealised gross P&L) at t_ms."""
        snap = _Snap(self, t_ms)
        unreal = margin = 0.0
        for p in self.positions.values():
            m = self._mark(p, t_ms, snap)
            if m is not None:
                unreal += m[2]
            margin += self._margin_of(p, t_ms, snap)
        eq = self.balance + unreal
        if eq > self.peak_equity:
            self.peak_equity = eq
        return eq, margin, unreal

    # ================================================================= opening / closing (internal, no commit)
    def _open_position(self, order: Order, t_ms: int, bid: float, ask: float, fill_ms: int) -> Optional[Position]:
        inst = self._inst(order.symbol)
        px = fills.entry_price(order.side, bid, ask, self.cfg.costs.extra_slippage_pips, inst.pip)
        eq, used, _ = self._totals(t_ms)
        need = pnl.margin_usd(inst, order.volume, usd_per_base(order.symbol, self.cfg.symbol_suffix, self._mid_fn(t_ms)), self.leverage)
        if need > eq - used + 1e-9:
            order.status, order.reason = "rejected", f"not enough money: need {need:.2f}, free {eq - used:.2f}"
            self._save_order(order)
            return None
        pos = Position(order.id, order.symbol, order.side, order.volume, px, fill_ms, order.sl, order.tp,
                       order.source, order.peeked, order.session_id, order.comment)
        self.positions[pos.id] = pos
        self._dirty = True
        self.balance -= self.cfg.costs.commission_per_lot_round_trip / 2 * order.volume
        order.status, order.position_id = "filled", pos.id
        self._save_pos(pos); self._save_order(order)
        self.orders.pop(order.id, None)
        self._activate(order.symbol, fill_ms)
        return pos

    def _close_volume(self, pos: Position, volume: float, t_ms: int, bid: float, ask: float, reason: str,
                      close_ms: int) -> dict:
        inst = self._inst(pos.symbol)
        px = fills.exit_price(pos.side, bid, ask, self.cfg.costs.extra_slippage_pips, inst.pip)
        uq = usd_per_quote(pos.symbol, self.cfg.symbol_suffix, self._mid_fn(t_ms))
        gross = pnl.gross_usd(inst, pos.side, volume, pos.open_price, px, uq)
        comm_rt = self.cfg.costs.commission_per_lot_round_trip * volume
        pv = pnl.pip_value_usd_per_lot(inst, uq) * volume
        pips = pnl.pips(inst, pos.side, pos.open_price, px)
        net = gross - comm_rt
        self._dirty = True
        self.balance += gross - comm_rt / 2                         # the opening half was charged when it opened
        deal = dict(position_id=pos.id, symbol=pos.symbol, side=pos.side, volume=volume, open_price=pos.open_price,
                    open_ms=pos.open_ms, close_price=px, close_ms=close_ms, sl=pos.sl, tp=pos.tp, pips=pips,
                    gross=gross, commission=comm_rt, net=net, net_pips=net / pv if pv else 0.0, reason=reason,
                    source=pos.source, peeked=pos.peeked, duration_s=(close_ms - pos.open_ms) / 1000)
        self.conn.execute(f"INSERT INTO acct_deals ({','.join(c for c in DEAL_COLS if c != 'id')}) "
                          f"VALUES ({','.join('?' * (len(DEAL_COLS) - 1))})", [deal[c] for c in DEAL_COLS if c != "id"])
        if volume >= pos.volume - 1e-9:
            del self.positions[pos.id]
            self.conn.execute("DELETE FROM acct_positions WHERE id=?", (pos.id,))
        else:
            pos.volume = round(pos.volume - volume, 8)
            self._save_pos(pos)
        return deal

    def _expire(self, o: Order, t_ms: int) -> None:
        o.status, o.reason = "expired", "expired"
        self._save_order(o)
        self.conn.execute("UPDATE acct_orders SET done_ms=? WHERE id=?", (t_ms, o.id))
        self.orders.pop(o.id, None)

    def _activate(self, symbol: str, t_ms: int) -> None:
        """Start (or keep) following ticks for `symbol` from the quote in force at t_ms onwards."""
        if symbol not in self.cursor or not self._symbol_active(symbol, exclude_new=True):
            key = self._view(symbol, t_ms).last_tick_key()
            if key is not None:
                self.cursor[symbol] = key
                self._save_cursor(symbol)

    def _symbol_active(self, symbol: str, exclude_new: bool = False) -> bool:
        n = sum(1 for p in self.positions.values() if p.symbol == symbol)
        n += sum(1 for o in self.orders.values() if o.symbol == symbol)
        return n > (1 if exclude_new else 0)

    # ================================================================= advancing the clock
    @_txn
    def advance_to(self, cutoff_ms: int) -> int:
        """Replay ticks in (t_engine, cutoff] for every symbol with an open position or working order.
        Returns the number of ticks processed. The clock never moves backwards."""
        return self._advance(cutoff_ms)

    def _advance(self, cutoff: int) -> int:
        """Move the account clock to `cutoff`. EVERY path (the server heartbeat, an API poll, a human's order, a bot's
        order) comes through here, so an `advance_hook` (the live bot manager) sees every stretch of time exactly once and
        can stop at decision boundaries - decisions never depend on WHO happened to advance the clock."""
        if cutoff <= self.t_engine:
            return 0
        if self.advance_hook is not None:
            return self.advance_hook(cutoff)          # must move the clock to `cutoff` using _advance_raw
        return self._advance_raw(cutoff)

    def _advance_raw(self, cutoff: int) -> int:
        if cutoff <= self.t_engine:
            return 0
        n = 0
        symbols = sorted({p.symbol for p in self.positions.values()} | {o.symbol for o in self.orders.values()})
        if symbols:
            for sym in symbols:
                if sym not in self.cursor:
                    self._activate(sym, self.t_engine)
            for t, seq, bid, ask, sym in heapq.merge(*[self._ticks(s, cutoff) for s in symbols], key=lambda x: (x[0], x[1])):
                self._on_tick(sym, t, bid, ask)
                self.cursor[sym] = (t, seq)
                n += 1
            for sym in symbols:
                if sym in self.cursor:
                    self._save_cursor(sym)
            self._fill_due_market_orders(cutoff, inclusive=True)
        for o in [o for o in self.orders.values() if o.expire_ms is not None and o.expire_ms <= cutoff]:
            self._expire(o, o.expire_ms)                              # also expires orders on a symbol that did not tick
        self.t_engine = cutoff
        self._risk_and_equity(cutoff)
        self._save_acct()
        return n

    def _ticks(self, sym: str, cutoff: int) -> Iterator[Tuple[int, int, float, float, str]]:
        dv = self._view(sym, cutoff)
        key = self.cursor.get(sym)
        while True:
            rows = dv.ticks_after(key, BATCH)
            for t, seq, bid, ask in rows:
                yield t, seq, bid, ask, sym
            if len(rows) < BATCH:
                return
            key = (rows[-1][0], rows[-1][1])

    def _fill_due_market_orders(self, t_ms: int, inclusive: bool) -> None:
        if not self.orders:
            return
        due = sorted((o for o in self.orders.values() if o.type == "market" and o.fill_at_ms is not None
                      and (o.fill_at_ms <= t_ms if inclusive else o.fill_at_ms < t_ms)), key=lambda o: (o.fill_at_ms, o.id))
        for o in due:
            self._execute_market(o, o.fill_at_ms)

    def _execute_market(self, o: Order, at_ms: int) -> None:
        """Fill a market order (open or close) with the quote in force at `at_ms`."""
        try:
            tk_ms, bid, ask = self._quote_in_force(o.symbol, at_ms)
        except EngineError as ex:
            o.status, o.reason = "rejected", ex.message
            self._save_order(o); self.orders.pop(o.id, None)
            return
        if o.kind == "close":
            pos = self.positions.get(o.target_position)
            if pos is None:
                o.status, o.reason = "rejected", "position no longer open"
            else:
                self._close_volume(pos, min(o.volume, pos.volume), at_ms, bid, ask, "manual", at_ms)
                o.status = "filled"
            self._save_order(o); self.orders.pop(o.id, None)
        else:
            self._open_position(o, at_ms, bid, ask, at_ms)

    def _on_tick(self, sym: str, t: int, bid: float, ask: float) -> None:
        self._now = t
        self._fill_due_market_orders(t, inclusive=False)          # queued market orders that fell before this tick
        if self.orders:
            for o in [o for o in self.orders.values() if o.symbol == sym and o.type in ("limit", "stop", "stop_limit") and t >= o.placed_ms]:
                if o.expire_ms is not None and t >= o.expire_ms:
                    self._expire(o, t)
                    continue
                if o.type == "stop_limit":
                    if fills.pending_triggered("stop", o.side, o.price, bid, ask):
                        o.type, o.price, o.stoplimit = "limit", o.stoplimit, None       # the stop was hit: now a limit order
                        self._save_order(o)
                    continue
                if fills.pending_triggered(o.type, o.side, o.price, bid, ask):
                    self._open_position(o, t, bid, ask, t)
        for p in [p for p in self.positions.values() if p.symbol == sym]:
            hit = fills.sl_tp_hit(p.side, p.sl, p.tp, bid, ask)
            if hit:
                self._close_volume(p, p.volume, t, bid, ask, hit, t)
        self._fill_due_market_orders(t, inclusive=True)
        if t >= self._next_risk_ms or self._dirty or t // self._sample_step() != self._last_sample_ms // self._sample_step():
            self._risk_and_equity(t)

    # ----------------------------------------------------------------- stop-out and the equity curve
    def _sample_step(self) -> int:
        return self.e.equity_sample_s * 1000 if self.positions else 15 * 60_000

    def _risk_and_equity(self, t_ms: int, force: bool = False) -> None:
        if self.positions:
            for _ in range(len(self.positions) + 1):
                eq, margin, _u = self._totals(t_ms)
                if margin > 0 and eq / margin * 100 < self.e.stop_out_pct:
                    snap = _Snap(self, t_ms)
                    worst = min(self.positions.values(), key=lambda p: (self._mark(p, t_ms, snap) or (0, 0, 0))[2])
                    tk = self._view(worst.symbol, t_ms).last_tick()
                    self._close_volume(worst, worst.volume, t_ms, tk[2], tk[3], "stopout", t_ms)
                else:
                    break
        eq, margin, _u = self._totals(t_ms)
        # How soon the next stop-out check is due: the thinner the margin cushion, the more often we look
        # (never slower than 10 s, never faster than `risk_check_ms`).
        level = (eq / margin * 100) if margin else float("inf")
        so = self.e.stop_out_pct
        self._next_risk_ms = t_ms + (self.e.risk_check_ms if level < 3 * so else 3 * self.e.risk_check_ms if level < 10 * so
                                     else 10 * self.e.risk_check_ms)
        step = self._sample_step()
        if force or self._dirty or t_ms // step != self._last_sample_ms // step:
            self.conn.execute("INSERT OR REPLACE INTO acct_equity (t_ms, balance, equity, margin) VALUES (?,?,?,?)",
                              (t_ms, self.balance, eq, margin))
            self._last_sample_ms = t_ms
            self._dirty = False

    def advance_wall(self, now_ms: int) -> int:
        """Server heartbeat: move the account clock to now - delay (the last delay a client used)."""
        return self.advance_to(int(now_ms - self.delay_s * 1000))

    # ================================================================= public API
    @_txn
    def set_delay(self, delay_s: float) -> None:
        self.delay_s = float(delay_s)
        self._save_acct()

    def _prepare(self, at_ms: int) -> None:
        self._now = at_ms
        if at_ms < self.t_engine:
            raise EngineError("clock_behind", f"the chart is {(self.t_engine - at_ms) / 1000:.0f}s behind the account clock "
                                              "(the delay was increased); wait for it to catch up")
        self._advance(at_ms)

    @_txn
    def place_order(self, symbol: str, side: str, order_type: str = "market", volume: float = 0.0,
                    price: Optional[float] = None, sl: Optional[float] = None, tp: Optional[float] = None, *,
                    at_ms: int, source: str = "manual", peeked: bool = False, session_id: Optional[str] = None,
                    comment: str = "", stoplimit: Optional[float] = None, expire_ms: Optional[int] = None) -> dict:
        """Place a market / limit / stop order at chart time `at_ms`. Market orders fill with the quote in force
        at at_ms (+ configured latency). Raises EngineError for anything invalid."""
        if symbol not in self.cfg.broker_symbols:
            raise EngineError("bad_symbol", f"unknown symbol {symbol!r}")
        if side not in SIDES or order_type not in TYPES:
            raise EngineError("bad_order", "side must be buy/sell and type market/limit/stop/stop_limit")
        if source not in ("manual", "bot"):
            raise EngineError("bad_order", "source must be manual or bot")
        volume = check_volume(volume, self.e)
        self._prepare(at_ms)
        inst = self._inst(symbol)
        _t, bid, ask = self._quote_in_force(symbol, at_ms)
        min_dist = self.e.min_stop_pips * inst.pip
        oid = self.next_id
        if order_type == "market":
            if price is not None or stoplimit is not None:
                raise EngineError("bad_order", "a market order has no price")
            if expire_ms is not None:
                raise EngineError("bad_order", "a market order does not expire")
            ref = bid if side == "buy" else ask                      # SL/TP are measured from the exit-side price
            check_levels(side, ref, sl, tp, min_dist, "current price")
            o = Order(oid, symbol, side, "market", volume, None, sl, tp, "working", at_ms,
                      at_ms + self.e.latency_ms, "open", None, source, int(peeked), session_id, comment)
        else:
            if price is None:
                raise EngineError("bad_order", "a pending order needs a price")
            check_pending_price(order_type, side, price, bid, ask, stoplimit)
            check_levels(side, stoplimit if order_type == "stop_limit" else price, sl, tp, min_dist, "order price")
            if expire_ms is not None and expire_ms <= at_ms:
                raise EngineError("invalid_expiration", "the expiration time has already passed")
            o = Order(oid, symbol, side, order_type, volume, price, sl, tp, "working", at_ms, None, "open", None,
                      source, int(peeked), session_id, comment, "", None, stoplimit, expire_ms)
        self.next_id += 1
        self.orders[o.id] = o
        self._save_order(o)
        self._activate(symbol, at_ms)
        if o.type == "market" and self.e.latency_ms == 0:
            self._execute_market(o, at_ms)
        self._save_acct()
        if o.status == "rejected":
            raise EngineError("rejected", o.reason)
        return {"id": o.id, "status": o.status, "position_id": o.position_id,
                "price": self.positions[o.position_id].open_price if o.position_id in self.positions else None}

    @_txn
    def close(self, position_id: int, volume: Optional[float] = None, *, at_ms: int) -> dict:
        """Close (all or part of) a position at chart time at_ms."""
        pos = self.positions.get(position_id)
        if pos is None:
            raise EngineError("no_position", f"position {position_id} is not open")
        vol = pos.volume if volume is None else check_volume(volume, self.e)
        if vol > pos.volume + 1e-9:
            raise EngineError("bad_volume", f"position only has {pos.volume} lots")
        self._prepare(at_ms)
        if self.e.latency_ms > 0:
            o = Order(self.next_id, pos.symbol, "sell" if pos.side == "buy" else "buy", "market", vol, None, None, None,
                      "working", at_ms, at_ms + self.e.latency_ms, "close", position_id, pos.source, pos.peeked, pos.session_id)
            self.next_id += 1
            self.orders[o.id] = o; self._save_order(o); self._save_acct()
            return {"id": o.id, "status": "working"}
        _t, bid, ask = self._quote_in_force(pos.symbol, at_ms)
        deal = self._close_volume(pos, vol, at_ms, bid, ask, "manual", at_ms)
        self._save_acct()
        return {"status": "closed", "deal": deal}

    @_txn
    def close_all(self, symbol: Optional[str] = None, *, at_ms: int, cancel_orders: bool = True) -> dict:
        self._prepare(at_ms)
        closed, cancelled = [], []
        for pos in [p for p in self.positions.values() if symbol in (None, p.symbol)]:
            _t, bid, ask = self._quote_in_force(pos.symbol, at_ms)
            closed.append(self._close_volume(pos, pos.volume, at_ms, bid, ask, "manual", at_ms)["position_id"])
        if cancel_orders:
            for o in [o for o in self.orders.values() if symbol in (None, o.symbol) and o.kind == "open"]:
                o.status, o.reason = "cancelled", "close all"
                self._save_order(o); del self.orders[o.id]; cancelled.append(o.id)
        self._save_acct()
        return {"closed": closed, "cancelled": cancelled}

    @_txn
    def modify(self, target_id: int, *, kind: str = "position", price=UNSET, sl=UNSET, tp=UNSET, stoplimit=UNSET,
               at_ms: int) -> dict:
        """Change SL/TP of an open position, or price/SL/TP of a pending order. None clears SL/TP; UNSET keeps."""
        self._prepare(at_ms)
        if kind == "position":
            pos = self.positions.get(target_id)
            if pos is None:
                raise EngineError("no_position", f"position {target_id} is not open")
            if price is not UNSET:
                raise EngineError("bad_order", "a position has no price to change")
            new_sl = pos.sl if sl is UNSET else sl
            new_tp = pos.tp if tp is UNSET else tp
            _t, bid, ask = self._quote_in_force(pos.symbol, at_ms)
            ref = bid if pos.side == "buy" else ask
            check_levels(pos.side, ref, new_sl, new_tp, self.e.min_stop_pips * self._inst(pos.symbol).pip, "current price")
            pos.sl, pos.tp = new_sl, new_tp
            self._save_pos(pos); self._save_acct()
            return pos.to_dict()
        o = self.orders.get(target_id)
        if o is None or o.type == "market":
            raise EngineError("no_order", f"pending order {target_id} not found")
        new_price = o.price if price is UNSET else price
        new_sl = o.sl if sl is UNSET else sl
        new_tp = o.tp if tp is UNSET else tp
        new_sl_px = o.stoplimit if stoplimit is UNSET else stoplimit
        _t, bid, ask = self._quote_in_force(o.symbol, at_ms)
        check_pending_price(o.type, o.side, new_price, bid, ask, new_sl_px if o.type == "stop_limit" else None)
        check_levels(o.side, new_sl_px if o.type == "stop_limit" else new_price, new_sl, new_tp,
                     self.e.min_stop_pips * self._inst(o.symbol).pip, "order price")
        o.price, o.sl, o.tp = new_price, new_sl, new_tp
        if o.type == "stop_limit":
            o.stoplimit = new_sl_px
        self._save_order(o); self._save_acct()
        return o.to_dict()

    @_txn
    def cancel_order(self, order_id: int, *, at_ms: int) -> dict:
        self._prepare(at_ms)
        o = self.orders.get(order_id)
        if o is None:
            raise EngineError("no_order", f"order {order_id} is not working")
        o.status, o.reason = "cancelled", "cancelled by user"
        self._save_order(o); del self.orders[order_id]; self._save_acct()
        return o.to_dict()

    # ----------------------------------------------------------------- reading state
    def positions_view(self) -> List[dict]:
        with self.lock:
            t = self.t_engine
            snap = _Snap(self, t)
            out = []
            for p in self.positions.values():
                m = self._mark(p, t, snap)
                d = p.to_dict()
                d.update(price=None if m is None else m[0], pips=None if m is None else m[1],
                         profit=None if m is None else m[2], margin=self._margin_of(p, t, snap),
                         commission=self.cfg.costs.commission_per_lot_round_trip * p.volume)
                out.append(d)
            return out

    def orders_view(self) -> List[dict]:
        with self.lock:
            return [o.to_dict() for o in self.orders.values()]

    def order_history(self, limit: int = 500) -> List[dict]:
        """Every order that is no longer working (filled, cancelled, expired, rejected), newest first."""
        with self.lock:
            rows = self.conn.execute(f"SELECT {','.join(ORDER_COLS)} FROM acct_orders WHERE status != 'working' "
                                     "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(zip(ORDER_COLS, r)) for r in rows]

    def history(self, limit: int = 100) -> List[dict]:
        with self.lock:
            rows = self.conn.execute(f"SELECT {','.join(DEAL_COLS)} FROM acct_deals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(zip(DEAL_COLS, r)) for r in rows]

    def equity_curve(self, since_ms: int = 0, limit: int = 5000) -> List[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT t_ms, balance, equity, margin FROM acct_equity WHERE t_ms >= ? ORDER BY t_ms LIMIT ?",
                                     (since_ms, limit)).fetchall()
            return [{"t_ms": r[0], "balance": r[1], "equity": r[2], "margin": r[3]} for r in rows]

    def account(self) -> dict:
        with self.lock:
            t = self.t_engine
            eq, margin, unreal = self._totals(t) if t else (self.balance, 0.0, 0.0)
            realized, deals = self.conn.execute("SELECT COALESCE(SUM(net), 0), COUNT(*) FROM acct_deals").fetchone()
            return {"currency": self.currency, "leverage": self.leverage, "balance": self.balance, "equity": eq,
                    "margin": margin, "free_margin": eq - margin,
                    "margin_level": (eq / margin * 100) if margin else None, "unrealized": unreal,
                    "realized": realized, "deals": deals, "positions": len(self.positions), "orders": len(self.orders),
                    "t_engine_ms": t, "delay_s": self.delay_s, "initial_balance": self.initial_balance,
                    "peak_equity": self.peak_equity,
                    "drawdown_pct": (self.peak_equity - eq) / self.peak_equity * 100 if self.peak_equity else 0.0}

    @_txn
    def reset(self, balance: Optional[float] = None, leverage: Optional[float] = None) -> dict:
        """Wipe the account (positions, orders, history, equity curve) and start fresh. The clock restarts at 0."""
        bal = self.e.starting_balance if balance is None else float(balance)
        if bal <= 0:
            raise EngineError("bad_balance", "balance must be positive")
        for t in ("acct_orders", "acct_positions", "acct_deals", "acct_equity", "acct_cursor"):
            self.conn.execute(f"DELETE FROM {t}")
        self.conn.execute("UPDATE acct SET balance=?, initial_balance=?, leverage=?, t_engine_ms=0, next_id=1, peak_equity=? WHERE id=1",
                          (bal, bal, self.e.leverage if leverage is None else float(leverage), bal))
        self._load()
        return self.account()
