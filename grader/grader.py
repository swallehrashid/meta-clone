"""The grader: "if I had entered here, what would have happened?"

This and `labeler_worker.py` are the ONLY modules allowed to read ticks after a cutoff
(hard rule 1). Nothing that decides anything (features, practice calls, the future bot)
may import this module - tests/test_architecture.py enforces that.

Conventions (all configurable in config.yaml unless noted)
  * Entry: first tick at or after `entry_t_ms`; buy fills at ASK, sell at BID. If that tick
    is more than `grader.entry_max_wait_s` later, the entry is ungradable (market closed /
    data hole) and no label is produced.
  * Horizons and the fixed-mode hold are measured from the FILL tick's time.
  * Exit side: bid for buys, ask for sells. A horizon P&L marks the quote in force at
    fill_time + h (the last tick at or before it).
  * Costs: `extra_slippage_pips` is charged on EACH fill (so twice per round trip) plus
    the commission converted to pips. P&L, MFE and MAE are all net of spread, slippage
    and commission. MFE/MAE are the max/min net unrealised P&L over every tick in
    [fill, fill + longest time-exit horizon], the fill tick included (so MFE <= -cost
    when price never improves on the entry).
  * Fixed mode: SL/TP are distances in pips from the entry FILL price, checked on the exit
    side starting with the fill tick itself (a spread wider than SL stops out at once).
    TP fills at the TP level; SL fills at the tick price (a gap through the stop loses more
    than SL). No hit by `max_hold_s` -> 'timeout', marked at fill + max_hold_s.
  * A label is 'pending' until the stored data reaches fill + longest window, then 'final'.
    A window that is not entirely inside market hours is ungradable (returns None).
All times are UTC milliseconds; all prices are in pips for analysis (hard rules 3-5).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from typing import Callable, Optional, Tuple

import numpy as np

from collector.market_hours import market_open
from core import db
from core.config import Config
from core.pips import pip_size
from grader.costs import commission_pips

HORIZON_COLS = {30: "pnl_30s", 60: "pnl_60s", 120: "pnl_120s", 300: "pnl_300s"}


@dataclass
class Label:
    symbol: str
    entry_t_ms: int
    direction: str
    status: str = "pending"                 # 'pending' | 'final'
    entry_price: Optional[float] = None
    spread_pips: Optional[float] = None
    pnl_30s: Optional[float] = None
    pnl_60s: Optional[float] = None
    pnl_120s: Optional[float] = None
    pnl_300s: Optional[float] = None
    mfe_pips: Optional[float] = None
    mae_pips: Optional[float] = None
    t_to_mfe_ms: Optional[int] = None
    t_to_mae_ms: Optional[int] = None
    fixed_outcome: Optional[str] = None     # 'tp' | 'sl' | 'timeout'
    fixed_pnl_pips: Optional[float] = None
    fixed_exit_ms: Optional[int] = None     # absolute UTC ms

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GradeParams:
    pip: float
    horizons_s: Tuple[int, ...]
    slippage_pips: float = 0.0
    commission_pips: float = 0.0
    entry_max_wait_ms: int = 10_000
    fixed_enabled: bool = False
    sl_pips: float = 3.0
    tp_pips: float = 4.5
    max_hold_s: int = 300

    @property
    def te_window_ms(self) -> int:
        return max(self.horizons_s) * 1000

    @property
    def window_ms(self) -> int:
        """How far past the fill the data must reach for the label to be final."""
        w = self.te_window_ms
        return max(w, self.max_hold_s * 1000) if self.fixed_enabled else w

    @property
    def cost_pips(self) -> float:
        return 2 * self.slippage_pips + self.commission_pips

    @staticmethod
    def from_config(cfg: Config, digits: int, commission: float = 0.0) -> "GradeParams":
        g, fm = cfg.grader, cfg.grader.fixed_mode
        return GradeParams(
            pip=pip_size(digits), horizons_s=tuple(g.time_exit_horizons_s),
            slippage_pips=cfg.costs.extra_slippage_pips, commission_pips=commission,
            entry_max_wait_ms=int(g.entry_max_wait_s * 1000), fixed_enabled=fm.enabled,
            sl_pips=fm.sl_pips, tp_pips=fm.tp_pips, max_hold_s=fm.max_hold_s)

    def reach_ms(self) -> int:
        """Ticks needed after the entry time: window + the longest tolerated entry wait."""
        return self.window_ms + self.entry_max_wait_ms


def _r(x: float) -> float:
    return round(float(x), 6)


def grade_arrays(t: np.ndarray, bid: np.ndarray, ask: np.ndarray, symbol: str,
                 entry_t_ms: int, direction: str, p: GradeParams, data_end_ms: Optional[int],
                 is_open: Callable[[int], bool] = market_open) -> Optional[Label]:
    """Grade one entry against sorted tick arrays.

    `data_end_ms` is the time of the newest tick stored for the symbol (which may lie
    beyond the arrays). Returns a Label ('pending' or 'final'), or None if ungradable.
    """
    if direction not in ("buy", "sell"):
        raise ValueError(f"direction must be 'buy' or 'sell', got {direction!r}")
    buy = direction == "buy"
    lab = Label(symbol, entry_t_ms, direction)
    if not is_open(entry_t_ms):
        return None                                  # nothing fills while the market is closed

    i0 = int(np.searchsorted(t, entry_t_ms, side="left"))
    if i0 >= len(t):
        # No tick in the loaded range. If stored data runs past the longest tolerated wait,
        # the entry sits in a hole (market closed / data gap): ungradable. Otherwise the
        # quote simply has not arrived yet.
        if data_end_ms is not None and data_end_ms - entry_t_ms > p.entry_max_wait_ms:
            return None
        return lab
    t_e = int(t[i0])
    if t_e - entry_t_ms > p.entry_max_wait_ms:
        return None                                  # no quote near the entry time
    entry = float(ask[i0] if buy else bid[i0])
    lab.entry_price = entry
    lab.spread_pips = _r((ask[i0] - bid[i0]) / p.pip)

    end_needed = t_e + p.window_ms
    if data_end_ms is None or data_end_ms < end_needed:
        return lab                                   # outcome window not fully stored yet
    if not (is_open(entry_t_ms) and is_open(end_needed)):
        return None                                  # window crosses a market close

    cost = p.cost_pips
    j = int(np.searchsorted(t, t_e + p.te_window_ms, side="right"))
    # net unrealised P&L (pips) if closed at each tick of the time-exit window
    exit_px = bid[i0:j] if buy else ask[i0:j]
    unreal = ((exit_px - entry) if buy else (entry - exit_px)) / p.pip - cost
    k_mfe, k_mae = int(np.argmax(unreal)), int(np.argmin(unreal))
    lab.mfe_pips, lab.mae_pips = _r(unreal[k_mfe]), _r(unreal[k_mae])
    lab.t_to_mfe_ms, lab.t_to_mae_ms = int(t[i0 + k_mfe] - t_e), int(t[i0 + k_mae] - t_e)

    def mark(at_ms: int) -> float:
        """Net P&L (pips) marking the quote in force at `at_ms`."""
        k = int(np.searchsorted(t, at_ms, side="right")) - 1
        px = float(bid[k] if buy else ask[k])
        return ((px - entry) if buy else (entry - px)) / p.pip - cost

    for h in p.horizons_s:
        setattr(lab, HORIZON_COLS[h], _r(mark(t_e + h * 1000)))

    if p.fixed_enabled:
        hold_ms = p.max_hold_s * 1000
        jf = int(np.searchsorted(t, t_e + hold_ms, side="right"))
        px = bid[i0:jf] if buy else ask[i0:jf]
        d = np.round(((px - entry) if buy else (entry - px)) / p.pip, 6)   # favourable pips
        tp_hit = np.flatnonzero(d >= p.tp_pips)
        sl_hit = np.flatnonzero(d <= -p.sl_pips)
        k_tp = int(tp_hit[0]) if len(tp_hit) else None
        k_sl = int(sl_hit[0]) if len(sl_hit) else None
        if k_tp is not None and (k_sl is None or k_tp < k_sl):
            lab.fixed_outcome, lab.fixed_pnl_pips = "tp", _r(p.tp_pips - cost)
            lab.fixed_exit_ms = int(t[i0 + k_tp])
        elif k_sl is not None:
            lab.fixed_outcome, lab.fixed_pnl_pips = "sl", _r(d[k_sl] - cost)
            lab.fixed_exit_ms = int(t[i0 + k_sl])
        else:
            lab.fixed_outcome, lab.fixed_pnl_pips = "timeout", _r(mark(t_e + hold_ms))
            lab.fixed_exit_ms = t_e + hold_ms
    lab.status = "final"
    return lab


# --------------------------------------------------------------------- database access
def load_arrays(conn: sqlite3.Connection, symbol: str, lo_ms: int, hi_ms: int):
    rows = conn.execute("SELECT t_ms, bid, ask FROM ticks WHERE symbol=? AND t_ms BETWEEN ? AND ? "
                        "ORDER BY t_ms, seq", (symbol, lo_ms, hi_ms)).fetchall()
    a = np.array(rows, dtype=np.float64).reshape(-1, 3)
    return a[:, 0].astype(np.int64), a[:, 1], a[:, 2]


def data_end(conn: sqlite3.Connection, symbol: str) -> Optional[int]:
    return conn.execute("SELECT MAX(t_ms) FROM ticks WHERE symbol=?", (symbol,)).fetchone()[0]


def params_for(conn: sqlite3.Connection, cfg: Config, symbol: str, entry_t_ms: int) -> GradeParams:
    digits = db.get_digits(conn, symbol)
    comm = commission_pips(conn, cfg, symbol, entry_t_ms, pip_size(digits))
    return GradeParams.from_config(cfg, digits, comm)


def grade(conn: sqlite3.Connection, symbol: str, entry_t_ms: int, direction: str,
          cfg: Config) -> Optional[Label]:
    """Grade a single entry straight from the database (None = ungradable)."""
    p = params_for(conn, cfg, symbol, entry_t_ms)
    t, bid, ask = load_arrays(conn, symbol, entry_t_ms, entry_t_ms + p.reach_ms())
    return grade_arrays(t, bid, ask, symbol, entry_t_ms, direction, p, data_end(conn, symbol))


# --------------------------------------------------------------------- label storage
LABEL_COLS = db.LABEL_COLS



def store_labels(conn: sqlite3.Connection, labels) -> None:
    rows = [tuple(getattr(l, c) for c in LABEL_COLS) for l in labels]
    with conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO labels ({','.join(LABEL_COLS)}) "
            f"VALUES ({','.join('?' * len(LABEL_COLS))})", rows)
