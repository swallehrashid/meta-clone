"""Baseline: the statistics of taking EVERY entry - the bar any rule has to beat.

All numbers are net pips (after spread, slippage and commission as configured when the labels
were graded). Note on sample sizes: entries are graded every minute with outcome windows of up to
5 minutes, so neighbouring entries overlap and the *effective* number of independent samples is
smaller than `n`; standard errors here ignore that and are therefore optimistic.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from analysis.data import HORIZON_COLS

QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)


def stats(s: pd.Series) -> dict:
    """n, mean, median, std, standard error, 95% CI of the mean, and % positive."""
    v = s.dropna().to_numpy(dtype=float)
    n = len(v)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "std": None, "se": None, "ci95": None, "win_pct": None}
    mean = float(v.mean())
    std = float(v.std(ddof=1)) if n > 1 else None
    se = std / np.sqrt(n) if std is not None else None
    return {"n": n, "mean": mean, "median": float(np.median(v)), "std": std, "se": se,
            "ci95": None if se is None else [mean - 1.96 * se, mean + 1.96 * se],
            "win_pct": float((v > 0).mean() * 100.0)}


def per_horizon(df: pd.DataFrame, horizons: List[int]) -> Dict[str, dict]:
    return {str(h): stats(df[HORIZON_COLS[h]]) for h in horizons if HORIZON_COLS[h] in df and df[HORIZON_COLS[h]].notna().any()}


def distribution(s: pd.Series, scale: float = 1.0) -> dict:
    v = s.dropna().to_numpy(dtype=float) * scale
    if len(v) == 0:
        return {"n": 0}
    out = {"n": len(v), "mean": float(v.mean())}
    out.update({f"p{int(q * 100)}": float(np.quantile(v, q)) for q in QUANTILES})
    return out


def baseline(df: pd.DataFrame, horizons: List[int], primary: int) -> dict:
    """Overall, per direction, per symbol and per local hour-of-day, plus MFE/MAE distributions."""
    hs = [h for h in horizons if HORIZON_COLS[h] in df]
    pcol = HORIZON_COLS[primary]
    out: dict = {"n": int(len(df)), "primary_horizon_s": primary, "horizons_s": hs}
    out["overall"] = per_horizon(df, hs)
    out["spread_pips"] = distribution(df["spread_pips"])
    out["by_direction"] = {d: {"n": int(len(g)), "horizons": per_horizon(g, hs)} for d, g in df.groupby("direction")}
    out["by_symbol"] = {s: {"n": int(len(g)), "horizons": per_horizon(g, hs)} for s, g in df.groupby("symbol")}
    out["by_hour"] = {int(h): {"n": int(len(g)), **stats(g[pcol])} for h, g in df.groupby("hour")} if pcol in df else {}
    out["mfe_mae"] = {
        "mfe_pips": distribution(df["mfe_pips"]), "mae_pips": distribution(df["mae_pips"]),
        "t_to_mfe_s": distribution(df["t_to_mfe_ms"], 1 / 1000), "t_to_mae_s": distribution(df["t_to_mae_ms"], 1 / 1000),
    }
    if "fixed_outcome" in df and df["fixed_outcome"].notna().any():
        fo = df["fixed_outcome"].dropna()
        out["fixed"] = {"n": int(len(fo)), "outcomes_pct": {k: float(v) for k, v in (fo.value_counts(normalize=True) * 100).items()},
                        "pnl": stats(df["fixed_pnl_pips"])}
    return out
