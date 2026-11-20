# BUILD_PLAN.md — Delayed-Feed Scalping Lab

A personal trading lab: a live forex feed played back a few minutes behind real time,
on an MT5-style chart, with a grader that shows what every possible scalp would have
done, and (later) an MT5-like paper trading account. A trading bot comes after all of
this is working and is NOT part of this plan beyond the interface it will plug into.

Build one phase at a time. Do not start a phase until the previous phase's
"Done when" checks pass.

---

## 0. Goals and non-goals

**Goals**
- Watch the market live but delayed (1, 5, 15 min, or any value), candles printing tick by tick.
- Know instantly how any entry would have played out, using the data between the delayed
  clock and real time ("the outcome window").
- Learn which conditions come before good scalps, using real spreads.
- Eventually: place, move, and delete trades on the delayed chart like an MT5 account,
  see positions and P&L update tick by tick, and later run a bot through the same account.
- View it on phone and laptop.

**Non-goals (for now)**
- No bot logic. Only the interface it will use (Phase 7, placeholder).
- No real-money order execution.
- No hosted multi-user product. This is a personal tool.

---

## 1. Hard rules (apply to every phase)

1. **No lookahead in decision code.** Anything that "decides" (features, the user's
   practice calls, the future bot) only sees data with timestamp <= the decision time.
   Enforce this in code with a `DataView(cutoff)` object that refuses to return later
   data. Only the grader and the "peek" view may read past the cutoff, and they must
   be clearly separate modules.
2. **The browser never receives data past the delayed cutoff** unless the user has
   turned on Peek. Peek is an explicit, logged action.
3. **All stored timestamps are UTC, in milliseconds.** Convert broker server time to
   UTC once, in the collector. Display time is chosen in the UI (default: New York).
4. **Bid and ask are both kept everywhere.** Buys open at ask and close at bid; sells
   open at bid and close at ask. Never grade or fill on mid price.
5. **Prices are in pips for analysis.** pip = 0.0001 for xxxUSD/xxxEUR-type pairs,
   0.01 for JPY pairs. Derive from symbol digits, don't hardcode per pair.
6. **Config, not constants.** Symbols, delays, horizons, SL/TP, and costs live in
   `config.yaml`.

---

## 2. Architecture

```
[Windows machine]                      [Server: same machine at first]          [Any device]
MT5 terminal (demo account)            FastAPI app                               Web app (React +
   │                                     ├── delay stream (websocket)            Lightweight Charts)
   ▼                                     ├── candle builder                      - MT5-style chart
collector.py ──► ticks DB (SQLite) ──►   ├── grader + labels                     - delay selector
                                         ├── analysis/report endpoints           - labels overlay
                                         └── paper engine (Phase 6)              - practice calls
                                                                                 - account (Phase 6)
```

- Start with everything on one Windows machine. Later the server can move to a VPS;
  only the collector needs Windows (MT5 Python package is Windows-only).
- Phone access: open the web app over the local network first; later via the VPS.
  Make it installable as a PWA.

### Stack
- Python 3.11+: `MetaTrader5`, `pandas`, `numpy`, `fastapi`, `uvicorn`, `websockets`,
  `pyyaml`, `pydantic`. SQLite in WAL mode.
- Frontend: React + Vite + TradingView Lightweight Charts (keep the required
  TradingView attribution visible).

### Folder layout
```
/collector        collector.py, mt5_client.py, time_offset.py
/core             config.py, db.py, dataview.py, pips.py, candles.py
/server           app.py, stream.py, api_labels.py, api_reports.py, api_account.py
/grader           grader.py, features.py, labeler_worker.py
/analysis         baseline.py, oracle.py, report.py
/engine           (Phase 6) account.py, orders.py, fills.py, pnl.py
/bot              (Phase 7, later) interface.py only
/web              React app
/tests            one test file per module
config.yaml
BUILD_PLAN.md
```

---

## 3. Config (`config.yaml`) — starting values, all editable

```yaml
symbols: [EURUSD, GBPUSD, EURJPY, GBPJPY]   # use the exact broker names (some add suffixes like EURUSDm)
symbol_suffix: ""
display_timezone: America/New_York

collector:
  poll_ms: 250
  backfill_hours_on_start: 24

delay:
  default_seconds: 300
  presets_seconds: [60, 300, 900]

candles:
  timeframes: [M1, M5, M15, H1]
  price_side: bid            # MT5 charts are bid-based

grader:
  step_seconds: 60           # grade an entry every N seconds of market time
  time_exit_horizons_s: [30, 60, 120, 300]   # "no SL/TP" mode: hold, then check P&L
  fixed_mode:
    enabled: false           # turn on once the strategy's SL/TP are decided
    sl_pips: 3.0
    tp_pips: 4.5
    max_hold_s: 300

costs:
  commission_per_lot_round_trip: 0.0   # set to broker's real value
  extra_slippage_pips: 0.0

sessions:                    # in display timezone, used for features and filters
  london_open: "03:00"
  ny_open: "08:00"
  ny_session_end: "11:00"
```

---

## 4. Data model (SQLite)

```sql
-- raw ticks, the source of truth
CREATE TABLE ticks (
  symbol     TEXT    NOT NULL,
  t_ms       INTEGER NOT NULL,   -- UTC milliseconds
  bid        REAL    NOT NULL,
  ask        REAL    NOT NULL,
  flags      INTEGER,
  seq        INTEGER NOT NULL,   -- order within same t_ms, to keep duplicates-in-time
  PRIMARY KEY (symbol, t_ms, seq)
);

-- collector health
CREATE TABLE collector_log (
  t_ms INTEGER, symbol TEXT, event TEXT, detail TEXT   -- 'gap', 'reconnect', 'offset_change'
);

-- grader output: one row per (symbol, entry time, direction)
CREATE TABLE labels (
  symbol      TEXT, entry_t_ms INTEGER, direction TEXT,   -- 'buy' | 'sell'
  entry_price REAL, spread_pips REAL,
  -- time-exit mode (no SL/TP)
  pnl_30s REAL, pnl_60s REAL, pnl_120s REAL, pnl_300s REAL,
  mfe_pips REAL, mae_pips REAL,             -- best/worst unrealised move within max horizon
  t_to_mfe_ms INTEGER, t_to_mae_ms INTEGER,
  -- fixed SL/TP mode (nullable until enabled)
  fixed_outcome TEXT,                        -- 'tp' | 'sl' | 'timeout'
  fixed_pnl_pips REAL, fixed_exit_ms INTEGER,
  status TEXT,                               -- 'pending' | 'final'
  PRIMARY KEY (symbol, entry_t_ms, direction)
);

-- features at entry time, computed with DataView(cutoff=entry_t_ms) only
CREATE TABLE features (
  symbol TEXT, t_ms INTEGER, name TEXT, value REAL,
  PRIMARY KEY (symbol, t_ms, name)
);

-- the user's practice calls on the delayed chart
CREATE TABLE calls (
  id INTEGER PRIMARY KEY, symbol TEXT, t_ms INTEGER, direction TEXT,
  note TEXT, peeked INTEGER DEFAULT 0
);
```

Candles are not stored; they are built from ticks on demand and cached in memory.
If SQLite gets slow, export older days to Parquet (one file per symbol per day).

---

## Phase 1 — Tick collector

**Build**
- `mt5_client.py`: initialize MT5, select symbols, reconnect on failure.
- `time_offset.py`: MT5 tick times come back in the broker's server time. Estimate the
  offset as `round((latest_tick_time - utc_now) / 3600)` hours while the market is
  active. Re-check every hour (DST changes it). Log changes to `collector_log`.
- `collector.py`: loop every `poll_ms`:
  - for each symbol, `copy_ticks_from(symbol, last_seen_server_ms, N, COPY_TICKS_ALL)`
  - drop ticks already stored (same t_ms and same position in that millisecond),
    convert to UTC, insert in one transaction
  - on start, backfill `backfill_hours_on_start` from MT5 history
  - detect gaps: no ticks for > 60 s while the market is open → log a 'gap'
- Weekend/market-closed handling: idle quietly, don't log gaps.

**Done when**
- Runs through a full New York session (08:00–11:00 NY) without crashing.
- For a 1-hour window, tick count per symbol matches what MT5 returns for the same
  window within 1%.
- Spot check: 10 random ticks match MT5's tick history exactly (time, bid, ask).
- Stopping and restarting the collector produces no duplicate and no missing ticks.

---

## Phase 2 — Delay stream and candle builder

**Build**
- `dataview.py`: `DataView(symbol, cutoff_ms)` returns ticks/candles <= cutoff only.
  Raises if asked for later data. Every consumer except grader/peek uses this.
- `candles.py`: build M1/M5/M15/H1 OHLC from bid ticks, plus tick count and average
  spread per candle. Intraday bars align to UTC minute/hour boundaries. Include the
  forming (incomplete) candle.
- `stream.py`: websocket `/ws/stream?symbols=EURUSD,GBPUSD&delay=300&tf=M1`
  - one shared cutoff for all symbols in a connection: `cutoff = utc_now - delay`
  - on connect: send backfill (last N candles <= cutoff)
  - then every ~250 ms: send new ticks with `prev_cutoff < t <= cutoff` and the
    updated forming candle
  - delay can be changed mid-stream (re-sync with a new backfill)
- `/ws/peek`: same feed with delay = 0. Separate endpoint. The client must mark the
  session as peeked when it is opened.

**Done when**
- With delay = 300 s, the newest candle shown is always 5 minutes behind MT5 (±1 s).
- M1/M5/M15 candles match MT5's candles for the same times (open/high/low/close
  within 0.1 pip; check 20 candles per timeframe).
- A test confirms `DataView` raises on access past its cutoff.
- Switching delay 300 → 60 → 900 works without a page reload.

---

## Phase 3 — Grader (the core)

The grader answers: "if I had entered here, what would have happened?" It is the only
module allowed to read past the delayed cutoff.

**Build**
- `grader.py`: `grade(symbol, entry_t_ms, direction, cfg) -> Label`
  - entry: first tick at or after `entry_t_ms`; buy at ask, sell at bid
  - walk forward tick by tick; exit side is bid for buys, ask for sells
  - **time-exit mode (default, no SL/TP):** P&L in pips at each horizon in
    `time_exit_horizons_s`, plus MFE/MAE and when they happened, all net of spread,
    commission, and extra slippage
  - **fixed mode (when enabled):** whichever of SL or TP is hit first, or timeout at
    `max_hold_s`; record outcome, pips, exit time
  - status `pending` until the full horizon exists in the data, then `final`
- `features.py`: computed with `DataView(cutoff=entry_t_ms)` only. Starter set
  (extend freely, each one is a named function):
  - `spread_pips`
  - `minutes_since_ny_open`, `minutes_since_london_open`
  - `last_m1_direction` (+1/-1), `last_m1_range_pips`, `last_m1_body_pct`
  - `m1_closed_above_prev_high` / `m1_closed_below_prev_low` (0/1)
  - `dist_to_session_high_pips`, `dist_to_session_low_pips`
  - `ticks_last_60s` (activity proxy)
  - `range_last_15m_pips`
- `labeler_worker.py`: runs continuously; every `step_seconds` of market time,
  grades buy and sell for each symbol and stores labels + features. Also backfills
  labels for all stored history.
- API: `GET /labels?symbol=&from=&to=` and `GET /features?...`

**Done when**
- Unit tests on hand-made tick sequences: buy/sell, TP first, SL first, timeout,
  spread wider than TP, ticks at identical timestamps.
- 10 labels checked by hand against the MT5 chart look right.
- With delay 300 s and max horizon 300 s, labels for a candle are `final` by the time
  that candle appears on the delayed chart.
- A test proves features never read past the entry time (feed a DataView that logs
  every access and assert max timestamp <= entry).

---

## Phase 4 — MT5-style chart (web, phone)

**Build**
- Lightweight Charts themed like MT5 (dark background, MT5-style candle colors,
  grid, bid line, ask line toggle). Theme defined in one file so it can be changed.
- Toolbar: symbol, timeframe (M1/M5/M15/H1), delay presets + custom value,
  display timezone.
- Layout: 1, 2, or 4 charts, all on the same cutoff (for comparing correlated pairs).
- Labels overlay (toggle): markers on each graded entry, colored by result
  (e.g., green = positive at chosen horizon, red = negative, size = magnitude).
  Off by default so practice stays clean.
- **Practice calls:** Buy / Sell buttons that do NOT open a position. They store a
  call at the current delayed time, which the grader scores. Running scorecard
  (calls, average pips at each horizon, split by clean vs peeked).
- Peek toggle: opens the live chart side by side; marks the session as peeked.
- Mobile: responsive layout, large tap targets for Buy/Sell, installable as a PWA.

**Done when**
- Usable on a phone during a live session for 30 minutes without layout problems.
- A practice call shows its graded result as soon as the outcome is final.
- Calls made while Peek was on are stored with `peeked = 1`.

---

## Phase 5 — Baseline, oracle, and report

**Build**
- `baseline.py`: stats over all labels (= taking every entry): average pips per
  horizon, % positive, distribution of MFE/MAE, broken down by symbol and by time of day.
  This is the bar any rule must beat.
- `oracle.py`: the entries that worked best in hindsight (e.g., top decile of P&L at a
  chosen horizon, or MFE >= X pips before MAE <= -Y pips). Not a trader, an answer key.
- `report.py`: for each feature, bucket its values and show win rate / average pips
  per bucket vs baseline, and how often oracle entries fall in each bucket.
  Output a single HTML report and a JSON endpoint the web app can render.
- Report filters: symbol, date range, session window, max spread.

**Done when**
- One command generates the report for any date range.
- The report clearly shows baseline vs each feature bucket, with sample sizes
  (flag buckets with < 100 samples as unreliable).

---

## Phase 6 — Paper trading engine (MT5-like account)

Build only after Phases 1–5 have collected and graded at least a few weeks of data.

**Build**
- Account: balance, equity, margin (simple), currency (USD), leverage setting.
- Orders: market buy/sell, buy/sell limit, buy/sell stop. Volume in lots.
- SL and TP are **optional**. Positions can be held with neither and closed manually.
- Modify (drag SL/TP/pending price on the chart), delete pending orders,
  close, partial close, close all.
- Fills: on delayed ticks via the same DataView the chart uses. Buys at ask, sells at
  bid, plus `extra_slippage_pips`; optional simulated latency (ms) before a fill.
- P&L: per tick, converted to account currency (JPY pairs use the current USDJPY or
  cross rate). Commission charged per config.
- UI: one-click trading panel; Trade tab (open positions, live P&L, close buttons);
  History tab (closed trades, entry/exit, pips, money, duration); equity curve.
- Every trade tagged: `source` (manual | bot), `clean | peeked`.
- Engine API (the bot will use exactly this later):
  `place_order(...)`, `modify(...)`, `close(...)`, `positions()`, `orders()`, `account()`.

**Done when**
- The same trades placed on the MT5 demo and in the engine at the same market
  moments give P&L within ~0.2 pip (accounting for the delay).
- Account values survive a server restart.
- Works on phone: open, move SL, close a trade.

---

## Phase 7 — Bot (placeholder, design later)

Not built in this plan. The engine and DataView above are its interface:
- The bot receives `DataView(cutoff)` on each tick/bar and returns engine API calls.
- It never sees the grader or peek data while deciding. Its trades are graded and
  compared against labels and the oracle afterwards.
- Modes to support later: replay of stored days (fast), delayed live on the paper
  engine, and MT5 demo with real fills.

---

## Working agreement for Claude Code

- Work one phase at a time; finish its tests and "Done when" checks before moving on.
- Keep modules small with tests in `/tests`.
- Never let decision code (features, calls, future bot) bypass `DataView`.
- When unsure about a trading convention (timezones, pip size, candle alignment),
  ask instead of guessing.
