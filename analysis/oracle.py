"""Oracle: the entries that worked best in hindsight. Not a trader - an answer key.

It marks which entries a perfect-hindsight picker would have taken, so the report can ask
"what did the market look like just BEFORE those entries?". Nothing here is usable as a signal:
it ranks entries by outcomes that were unknowable at entry time.

Modes (config `analysis.oracle`)
  top_decile  entries at/above the `quantile` of net pips at `horizon_s` (default: top 10% at 60 s)
  run         mfe >= run_mfe_pips AND mae > -run_mae_pips. Stored labels only keep the extreme
              points of the path, so "reached +X before -Y" cannot be proven from them; this
              stricter test (reached +X and NEVER touched -Y inside the window) is exact and
              order-independent, at the price of excluding some valid runs.
  fixed_tp    fixed SL/TP mode only: the TP was hit before the SL (exact order).
Apply per population (e.g. buys and sells separately).
"""
from __future__ import annotations

from typing import Tuple

import pandas as pd

from analysis.data import HORIZON_COLS
from core.config import OracleCfg


def oracle_mask(df: pd.DataFrame, o: OracleCfg) -> Tuple[pd.Series, dict]:
    """Boolean mask of oracle entries in `df`, plus a description of the rule used."""
    if df.empty:
        return pd.Series(False, index=df.index), {"mode": o.mode, "text": "no data", "threshold": None}
    if o.mode == "top_decile":
        col = HORIZON_COLS[o.horizon_s]
        thr = float(df[col].quantile(o.quantile))
        mask = df[col] >= thr
        top = round((1 - o.quantile) * 100, 4)
        return mask.fillna(False), {"mode": o.mode, "threshold": thr,
                                    "text": f"top {top:g}% of entries by net pips at {o.horizon_s}s (>= {thr:.2f} pips)"}
    if o.mode == "run":
        mask = (df["mfe_pips"] >= o.run_mfe_pips) & (df["mae_pips"] > -o.run_mae_pips)
        return mask.fillna(False), {"mode": o.mode, "threshold": None,
                                    "text": f"went >= {o.run_mfe_pips:g} pips in favour and never {o.run_mae_pips:g} pips against within the window"}
    if o.mode == "fixed_tp":
        if "fixed_outcome" not in df or df["fixed_outcome"].notna().sum() == 0:
            raise ValueError("oracle mode 'fixed_tp' needs fixed SL/TP labels (grader.fixed_mode.enabled)")
        return (df["fixed_outcome"] == "tp"), {"mode": o.mode, "threshold": None, "text": "take-profit hit before stop-loss (fixed mode)"}
    raise ValueError(f"unknown oracle mode {o.mode!r}")
