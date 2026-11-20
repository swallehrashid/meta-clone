"""Labeler: grades buy and sell for every symbol every `grader.step_seconds` of market time,
stores labels + features, back-fills all stored history, and finalises pending labels as
the outcome window fills in.

    python -m grader.labeler_worker            # run continuously
    python -m grader.labeler_worker --once     # catch up and exit

Grid: entry times are multiples of step_seconds in UTC (minute-aligned for 60 s). Progress
is derived from the labels table itself (max entry per symbol), so a restart resumes
exactly where it stopped; pending labels are re-graded every pass. Entries that cannot be
graded (market closed, data hole) simply produce no row.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import threading
import time
from typing import Dict, Optional

from core import db
from core.candles import CandleCache
from core.config import Config, load_config
from core.dataview import DataView
from grader.costs import commission_pips
from grader.features import compute_features, store_features
from grader.grader import (GradeParams, data_end, grade, grade_arrays, load_arrays, params_for,
                           store_labels)
from core.pips import pip_size

log = logging.getLogger("labeler")


class Labeler:
    def __init__(self, cfg: Config, conn: sqlite3.Connection):
        self.cfg, self.conn = cfg, conn
        self.cache = CandleCache()
        self.step = cfg.grader.step_seconds * 1000

    # ------------------------------------------------------------------ one pass
    def run_once(self) -> Dict[str, int]:
        stats = {"new": 0, "finalised": 0, "dropped": 0, "pending": 0}
        for sym in self.cfg.broker_symbols:
            end = data_end(self.conn, sym)
            if end is None:
                continue
            self._regrade_pending(sym, end, stats)
            self._grade_new(sym, end, stats)
        self._grade_calls(stats)
        stats["pending"] = self.conn.execute(
            "SELECT COUNT(*) FROM labels WHERE status='pending'").fetchone()[0]
        return stats

    # ------------------------------------------------------------------ pending
    def _regrade_pending(self, sym: str, end: int, stats: dict) -> None:
        rows = self.conn.execute(
            "SELECT entry_t_ms, direction FROM labels WHERE symbol=? AND status='pending' "
            "ORDER BY entry_t_ms", (sym,)).fetchall()
        done, drop = [], []
        for t, d in rows:
            p = params_for(self.conn, self.cfg, sym, t)
            ta, ba, aa = load_arrays(self.conn, sym, t, t + p.reach_ms())
            lab = grade_arrays(ta, ba, aa, sym, t, d, p, end)
            if lab is None:
                drop.append((sym, t, d))
            elif lab.status == "final":
                done.append(lab)
        if done:
            store_labels(self.conn, done)
            stats["finalised"] += len(done)
        if drop:
            with self.conn:
                self.conn.executemany(
                    "DELETE FROM labels WHERE symbol=? AND entry_t_ms=? AND direction=?", drop)
            stats["dropped"] += len(drop)

    # ------------------------------------------------------------------ new entries
    def _grade_new(self, sym: str, end: int, stats: dict) -> None:
        first = self.conn.execute("SELECT MIN(t_ms) FROM ticks WHERE symbol=?", (sym,)).fetchone()[0]
        last = self.conn.execute("SELECT MAX(entry_t_ms) FROM labels WHERE symbol=?", (sym,)).fetchone()[0]
        step = self.step
        nxt = -(-first // step) * step if last is None else last + step
        digits = db.get_digits(self.conn, sym)
        pip = pip_size(digits)
        chunk = max(1, self.cfg.grader.chunk_minutes * 60_000 // step) * step
        base = GradeParams.from_config(self.cfg, digits)
        commission_on = self.cfg.costs.commission_per_lot_round_trip != 0

        while nxt <= end:
            grid = list(range(nxt, min(nxt + chunk, end + 1), step))
            ta, ba, aa = load_arrays(self.conn, sym, grid[0], grid[-1] + base.reach_ms())
            labels, feat_rows = [], []
            for t in grid:
                p = base
                if commission_on:
                    comm = commission_pips(self.conn, self.cfg, sym, t, pip)
                    p = GradeParams.from_config(self.cfg, digits, comm)
                pair = [grade_arrays(ta, ba, aa, sym, t, d, p, end) for d in ("buy", "sell")]
                pair = [l for l in pair if l is not None]
                if not pair:
                    continue
                labels.extend(pair)
                dv = DataView(self.conn, sym, t, self.cache, self.cfg.candles.price_side)
                feat_rows.append((t, compute_features(dv, self.cfg, digits)))
            store_labels(self.conn, labels)
            for t, feats in feat_rows:
                store_features(self.conn, sym, t, feats)
            stats["new"] += len(labels)
            nxt = grid[-1] + step
            log.info("%s: graded through %s (%d labels this chunk)", sym, grid[-1], len(labels))

    # ------------------------------------------------------------------ practice calls
    RESULT_COLS = ["entry_price", "spread_pips", "pnl_30s", "pnl_60s", "pnl_120s", "pnl_300s",
                   "mfe_pips", "mae_pips", "t_to_mfe_ms", "t_to_mae_ms", "fixed_outcome",
                   "fixed_pnl_pips", "fixed_exit_ms", "status"]

    def _grade_calls(self, stats: dict) -> None:
        """Grade every practice call that has no final result yet, at its exact call time."""
        todo = self.conn.execute(
            "SELECT c.id, c.symbol, c.t_ms, c.direction FROM calls c "
            "LEFT JOIN call_results r ON r.call_id = c.id "
            "WHERE r.call_id IS NULL OR r.status = 'pending'").fetchall()
        for cid, sym, t, d in todo:
            lab = grade(self.conn, sym, t, d, self.cfg)
            if lab is None:
                vals = {"status": "ungradable"}                      # no fill: market closed / hole
            else:
                vals = {c: getattr(lab, c) for c in self.RESULT_COLS}
            row = [cid] + [vals.get(c) for c in self.RESULT_COLS]
            with self.conn:
                self.conn.execute(
                    f"INSERT OR REPLACE INTO call_results (call_id, {','.join(self.RESULT_COLS)}) "
                    f"VALUES ({','.join('?' * (len(self.RESULT_COLS) + 1))})", row)
            if vals["status"] != "pending":
                stats["calls_graded"] = stats.get("calls_graded", 0) + 1

    # ------------------------------------------------------------------ loop
    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                s = self.run_once()
                if s["new"] or s["finalised"] or s["dropped"]:
                    log.info("pass: %s", s)
            except Exception:  # noqa: BLE001
                log.exception("labeler pass failed")
            stop.wait(self.cfg.grader.worker_interval_s)


def main() -> None:
    ap = argparse.ArgumentParser(description="Label worker")
    ap.add_argument("--config"); ap.add_argument("--db")
    ap.add_argument("--once", action="store_true", help="catch up, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    conn = db.connect(args.db or cfg.resolve_db_path())
    lab = Labeler(cfg, conn)
    if args.once:
        print(lab.run_once())
        return
    stop = threading.Event()
    try:
        lab.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
