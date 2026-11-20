"""Typed loader for config.yaml. Every tunable lives in the file, never in code."""
from __future__ import annotations

from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectorCfg(_Strict):
    poll_ms: int = 250
    backfill_hours_on_start: float = 24
    batch_size: int = 50000
    backfill_chunk_minutes: int = 60
    gap_seconds: int = 60
    idle_poll_ms: int = 5000
    offset_recheck_seconds: int = 3600


class DelayCfg(_Strict):
    default_seconds: int = 300
    presets_seconds: List[int] = [60, 300, 900]
    min_seconds: int = 1       # /ws/stream refuses less: zero delay is /ws/peek only
    max_seconds: int = 86400


class ServerCfg(_Strict):
    stream_interval_ms: int = 250
    backfill_candles: int = 300
    tick_batch_limit: int = 5000   # max ticks sent per symbol per update
    heartbeat_seconds: float = 1.0


class CandlesCfg(_Strict):
    timeframes: List[str] = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    price_side: str = "bid"

    @field_validator("price_side")
    @classmethod
    def _side(cls, v: str) -> str:
        if v not in ("bid", "ask"):
            raise ValueError("price_side must be 'bid' or 'ask'")
        return v


class FixedModeCfg(_Strict):
    enabled: bool = False
    sl_pips: float = 3.0
    tp_pips: float = 4.5
    max_hold_s: int = 300


SUPPORTED_HORIZONS = (30, 60, 120, 300)   # the labels table has one pnl column per horizon


class GraderCfg(_Strict):
    step_seconds: int = 60
    time_exit_horizons_s: List[int] = [30, 60, 120, 300]
    fixed_mode: FixedModeCfg = FixedModeCfg()
    entry_max_wait_s: float = 10   # first tick must arrive this soon after the entry time
    worker_interval_s: float = 2   # labeler loop period
    chunk_minutes: int = 60        # ticks are loaded and graded in chunks of this much market time

    @field_validator("time_exit_horizons_s")
    @classmethod
    def _horizons(cls, v: List[int]) -> List[int]:
        if not v or any(h not in SUPPORTED_HORIZONS for h in v):
            raise ValueError(f"time_exit_horizons_s must be a non-empty subset of {SUPPORTED_HORIZONS} "
                             "(the labels table has one column per horizon; adding another "
                             "needs a schema change)")
        return sorted(set(v))


class OracleCfg(_Strict):
    mode: str = "top_decile"       # top_decile | run | fixed_tp
    horizon_s: int = 60            # top_decile: rank entries by net pips at this horizon
    quantile: float = 0.9          # top_decile: keep entries at or above this quantile
    run_mfe_pips: float = 4.5      # run: went at least this far in favour ...
    run_mae_pips: float = 3.0      # ... without ever going this far against (within the window)

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("top_decile", "run", "fixed_tp"):
            raise ValueError("oracle.mode must be top_decile, run or fixed_tp")
        return v

    @field_validator("horizon_s")
    @classmethod
    def _h(cls, v: int) -> int:
        if v not in SUPPORTED_HORIZONS:
            raise ValueError(f"oracle.horizon_s must be one of {SUPPORTED_HORIZONS}")
        return v

    @field_validator("quantile")
    @classmethod
    def _q(cls, v: float) -> float:
        if not 0.5 <= v < 1:
            raise ValueError("oracle.quantile must be in [0.5, 1)")
        return v


class AnalysisCfg(_Strict):
    horizon_s: int = 60            # primary horizon for win rate / deltas in the report
    min_bucket_n: int = 100        # buckets with fewer samples are flagged unreliable
    n_buckets: int = 5             # quantile bins for continuous features
    oracle: OracleCfg = OracleCfg()

    @field_validator("horizon_s")
    @classmethod
    def _h(cls, v: int) -> int:
        if v not in SUPPORTED_HORIZONS:
            raise ValueError(f"analysis.horizon_s must be one of {SUPPORTED_HORIZONS}")
        return v


class EngineCfg(_Strict):
    starting_balance: float = 10000.0
    leverage: float = 100.0
    stop_out_pct: float = 50.0        # margin level (equity / margin) below which positions are force-closed
    latency_ms: int = 0               # simulated delay before a market order fills (quote in force at order time + latency)
    min_volume: float = 0.01
    volume_step: float = 0.01
    max_volume: float = 50.0
    min_stop_pips: float = 0.0        # SL / TP must be at least this far from the price
    max_quote_age_s: float = 120.0    # refuse to fill on a quote older than this (market effectively closed)
    risk_check_ms: int = 1000         # stop-out / equity checks run at most this often (engine time)
    equity_sample_s: int = 60         # equity curve resolution while positions are open
    advance_interval_s: float = 1.0   # how often the server moves the account clock forward


class CostsCfg(_Strict):
    commission_per_lot_round_trip: float = 0.0   # account currency (USD)
    extra_slippage_pips: float = 0.0             # adverse pips charged on EACH fill (entry and exit)
    contract_size: int = 100000                  # units per lot, for pip value


class SessionsCfg(_Strict):
    london_open: str = "03:00"
    ny_open: str = "08:00"
    ny_session_end: str = "11:00"
    high_low_anchor: str = "london_open"   # session high/low are measured since the latest such open

    @field_validator("high_low_anchor")
    @classmethod
    def _anchor(cls, v: str) -> str:
        if v not in ("london_open", "ny_open"):
            raise ValueError("high_low_anchor must be 'london_open' or 'ny_open'")
        return v


class Config(_Strict):
    symbols: List[str]
    symbol_suffix: str = ""
    display_timezone: str = "America/New_York"
    db_path: str = "data/ticks.db"
    collector: CollectorCfg = CollectorCfg()
    delay: DelayCfg = DelayCfg()
    server: ServerCfg = ServerCfg()
    candles: CandlesCfg = CandlesCfg()
    grader: GraderCfg = GraderCfg()
    analysis: AnalysisCfg = AnalysisCfg()
    engine: EngineCfg = EngineCfg()
    costs: CostsCfg = CostsCfg()
    sessions: SessionsCfg = SessionsCfg()

    @property
    def broker_symbols(self) -> List[str]:
        """Symbols as the broker names them (suffix applied)."""
        return [s + self.symbol_suffix for s in self.symbols]

    def resolve_db_path(self, base: Path | None = None) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (base or DEFAULT_CONFIG_PATH.parent) / p


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config.model_validate(raw)
