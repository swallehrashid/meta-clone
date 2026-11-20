# Delayed-Feed Scalping Lab

A personal trading lab: a live forex feed replayed a few minutes behind real time, shown in a **MetaTrader 5 phone look-alike**
(`/`), with a **back office** (`/admin`, login) where you set the delay, plug in bots, and train them with or without a peek at the
next candle. A grader shows what every possible scalp would have done. See `BUILD_PLAN (1).md` for the plan.

* **`/` the terminal** — Quotes / Charts / Trade / History / Messages exactly like MT5 on a phone, light theme. No settings of its
  own: it shows the delayed market and a paper account, and you trade by tapping SELL / BUY. Everything about *how* it behaves
  (delay, pairs, timeframe, lot size, time zone, Ask line) is decided in the back office.
* **`/admin` the back office** — sign in, then: **Terminal** (settings), **Bot lab** (run a bot on the terminal's account, replay,
  train), **Chart lab** (the original lab view: labels, Peek, practice calls, reports).

Status: Phases 1–7 built, plus the MT5-style terminal, the back office, and the forward-data bot lab (below).
Earlier phases (collector, delay stream + candles, grader/labels/features, web chart + practice calls, baseline/oracle/report, paper-trading account). Phase 7: the bot harness (interface, replay + delayed-live runner, risk limits, grading against labels/oracle) with example bots — a null benchmark and a demo rule, **not** a strategy.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd web && npm install && npm run build && cd ..          # builds web/dist, served by the Python server
LAB_ADMIN_PASSWORD='choose-one' .venv/bin/python run_stack.py --sim    # collector + labeler + web server
#   terminal: http://127.0.0.1:8000        back office: http://127.0.0.1:8000/admin   (user: admin)
```
Without `LAB_ADMIN_PASSWORD` a random admin password is generated on first start and **printed once** on the console
(stored hashed in `data/admin.json`); change it any time with `.venv/bin/python -m tools.set_admin_password`.

`--sim` uses a realistic *simulated* market (`collector/market_model.py`, no broker needed); `--sim-simple` is the old toy
feed with sine waves (tests only). Without either, the collector uses the real MetaTrader5 package, which only exists for
Windows — see "Real data on Ubuntu" below.

### Months of (simulated) history in one command
```bash
.venv/bin/python -m tools.make_history --weeks 9            # ~14M ticks into data/ticks.db in about 2 minutes
.venv/bin/python -m grader.labeler_worker --once            # label them (~10 min); then run the report / engine checks on them
```
The model is a martingale with realistic volatility, spreads, activity by session and pair correlations — **no exploitable
structure by construction**, so an honest analysis of it finds only chance-level effects (checked in `tests/test_market_model.py`
and on the 9-week set). It is for exercising the lab, never evidence about real markets.

### Use it from your phone
```bash
.venv/bin/python run_stack.py --sim --host 0.0.0.0 --port 8000     # then open http://<this-machine-ip>:8000
```
Everything works over plain http on your LAN. **Installing it as an app ("Add to Home Screen") needs https**
(browsers only allow service workers/installable PWAs in a secure context; `localhost` counts). For a VPS put
Caddy or nginx with a TLS certificate in front of port 8000 (or use Tailscale's `tailscale serve`).

## The terminal (`/`)
A replica of **MetaTrader 5 for Android**, built from screenshots and a screen recording of the real app, so that practising here
carries straight over to MT5. Pure black theme by default (Settings → Choose theme: Light / Dark / Pure black), fonts Roboto and
Roboto Condensed (loaded from Google Fonts so a phone's own system font never replaces them; without internet the system sans-serif is used).
Every button does something - `e2e/terminal.mjs` taps all of them, one by one, and fails on any that does not.

| Where | What it does |
|---|---|
| Quotes | Advanced / simple view; **+** → Add symbol (search, Forex / Indexes / Metals folders); **pencil** → Selected symbols (drag to reorder, bin to delete); tap a row → New order / Chart / Properties / Market statistics / view mode. |
| Charts toolbar | Crosshair (finger moves it; labels like `Tue 17 Nov '26 05:25`), Indicators, timeframe (M1 M5 M15 M30 H1 H4 D1 W1 MN + chart settings), **red/blue ring** = order panel, **red/blue panel** = One Click Trading (asks for acceptance first). `…` = chart menu, tap the symbol to switch. |
| One Click Trading | SELL / BUY boxes coloured by tick direction, volume ⌄ / ⌃ dropdowns (−/+0.01, 0.1, 0.5 and presets), tap the volume to type it. |
| Order panel | Market Execution, Buy/Sell Limit, Buy/Sell Stop, **Buy/Sell Stop Limit**; volume in lots or units, margin slider, Margin ≈ / Free, price, stop limit price, Add Stop Levels, fill policy, **expiration GTC / Today / Specified**, comment, price ladder. Collapsed it sits under the chart with SL / TP / → and the order, stop-limit, SL and TP lines are **draggable on the chart**. |
| Indicators | Moving Average (SMA/EMA/SMMA/LWMA), Bollinger Bands, Envelopes, Parabolic SAR, Standard Deviation, RSI, MACD, Stochastic, ATR, CCI, Momentum, Williams %R, DeMarker, Volumes, Awesome Oscillator - computed and drawn, oscillators in their own windows; parameters and colour per indicator, hide / delete all. |
| Chart settings | Bars / candles / line, volumes, OHLC, trade levels, Ask line, period separators, grid, colour scheme. |
| Trade | Balance / Equity / Margin / Free margin / Margin level; tap a row for details, **press and hold** for Close position (MT5's yellow "Close … with profit/loss"), Modify position, Trade, Chart, Bulk Operations; pending orders: Modify / Delete / Chart. Sort menu, new order. |
| History | POSITIONS / ORDERS / DEALS, symbol filter (the "All symbols" line), profit in money or points ($ icon), sort menu per tab, period (Today / Last week / Last month / Last 3 months / Custom), tap rows for details, report icon. |
| Report | Summary, Profit & Loss, Long & Short, Symbols, Risks - computed from your trades; print and download. |
| Messages | MQID, search, Register / Sign in (open MQL5.com). |
| Drawer (☰) | Account + Manage accounts; Trade, News, Mailbox, Journal (what the terminal did, MT5 wording), Settings, Economic calendar, Traders Community, MQL5 Algo Trading, User guide, About. |
| Settings | Every MT5 option: advanced quotes, order sounds, one click trading, confirmation of trades on chart, account info in bottom menu, MetaQuotes ID, vibration, ringtone, auto-download, OTP, **lock screen (PIN)**, news, language, tablet interface, always-on display, theme. |

Settings live on the device (localStorage); the delay, the tradable pairs and the account stay on the server. News, calendar and
community have no data source in the lab and say so. The phone's back button closes the top-most sheet, menu or page.

## The back office (`/admin`)
* **Terminal** — delay (presets or `90s`, `7m`, `1h`), default timeframe, one-click lot size, server time zone, Ask line on/off, which pairs
  are quoted, the account label, reset of the paper account. Saved server-side; the terminal follows within seconds, without a reload.
  *Lengthening* the delay pauses trading until the chart catches up with the account clock (the account never moves backwards).
* **Bot lab** — see below. **Chart lab** — the original lab (Peek, labels, calls, report link). All of it, `/report`, `/labels`, `/calls`,
  `/api/bot` and Peek (`/ws/peek`) require the admin login; the terminal, its quotes feed and `/api/trade/*` do not.
* Sign-in is a signed, httpOnly, SameSite=Strict cookie for 12 h; failed logins are rate-limited (5 per minute per address).
  Put it behind https if you expose it beyond your LAN.

## Bots, forward data and training
Every bot run is **blind** (it sees what you see) unless you explicitly give it **forward data**: `forward_s > 0` lets it see the *next* `forward_s`
seconds — in live mode that is the minute the delayed chart is hiding (so it can never exceed the terminal's delay), in a replay any length.
`ctx.forward(symbol)` is a `ForwardView` (`bot/interface.py`): ticks strictly after the decision time, `quote_at(s)`, `extremes(s)`, `candles(tf)`,
`next_candle(tf)`. Rules that keep it honest:
* forward data is off unless asked for, on every launch form, in the API (`forward_s`) and on the CLI;
* **every order of a forward run is tagged `peeked`**, so its results are kept apart from blind (clean) ones everywhere (Bot lab, Trade stats);
* a *teacher* bot (`needs_forward = True`) refuses to run blind; the normal view (`ctx.view`) stays fenced at the decision time even in a forward run.

Bots: `forward_teacher` (sees the next minute(s), takes the profitable side, stop just beyond the worst move — an upper bound that makes money by
construction; **not a strategy**), `learned` (blind; driven by a trained model), `random_entry` (null benchmark), `momentum_breakout` (demo rule).

**The training procedure** (Bot lab → *Train*, or `python -m bot.train --symbols EURUSD,GBPUSD --train-from 2026-09-21 --train-to 2026-10-09 --test-from 2026-10-12 --test-to 2026-10-16 --name m1`):
1. **Watch** — replay the forward-fed teacher over the training days; store every decision next to the entry-time features.
2. **Analyse** — how often it trades, which features go with its choices.
3. **Learn** — a small logistic model (`bot/model.py`, plain JSON in `data/models/`) predicts "would the teacher buy / sell here?" from the features alone; it is
   validated on the last 20 % of the training days (AUC; **direction** AUC among traded minutes is the number that matters — the per-side AUCs are inflated by volatility alone).
4. **Test blind** — on days *after* the training period: the learned bot (blind), the random bot, and the teacher (forward, marked peeked). Judge the blind bot only against the random one.
Jobs run in the background on a read-only sandbox of the history (your ticks and paper account are never touched), one at a time, and every run is kept so you can compare iterations.
* **On simulated data the correct answer is "no skill"** (it is a martingale with no structure): the teacher earns ~+1 pip/trade with hindsight, the blind bot ≈ random. That is
  the pipeline working, not failing. It only means something on real data (see "Real data on Ubuntu").

## Layout
```
collector/  MT5 (or simulated) ticks -> SQLite          core/     config, db, DataView (no-lookahead), candles, pips
grader/     grader, features, labeler worker            server/   FastAPI: /ws/stream, /ws/peek, /labels, /features, /calls, /report
analysis/   baseline, oracle, report (HTML + JSON)             engine/   paper-trading account (orders, fills, P&L, margin)
tools/      make_history, verify_engine, engine_reference, sandbox        bot/      interface, runner, live, evaluate, strategies/
web/        React + Vite + Lightweight Charts (PWA): src/terminal (MT5 look-alike), src/admin (back office)     tests/    pytest (backend)   web/e2e/  Playwright (real Chrome)
```

## Paper trading (Phase 6)
In the terminal, BUY / SELL place real (paper) orders at the *delayed* price; in the back office's **Chart lab** the original lab UI also has
**PRACTICE / TRADE** modes, SL/TP in pips and draggable SL / TP / pending-order lines. Everything survives a server restart.
* The account runs on its own clock that follows the chart (`now − delay`) and **never moves backwards**: if you lengthen the
  delay, trading is paused (banner) until the chart catches up. A background task keeps the clock moving, so SL/TP/pending
  orders trigger even with the browser closed.
* Buys open at the ask and close at the bid, sells the reverse; slippage (`costs.extra_slippage_pips`) is charged on every fill;
  commission half at open, half at close. Market orders fill with the quote in force at order time (+ `engine.latency_ms`);
  pending orders fill at the tick that touches them; SL/TP fill at the tick price (gaps lose more). Margin = lots × 100,000 ×
  base price / leverage (no hedge offset); below `engine.stop_out_pct` the worst position is force-closed. No swaps.
* Trades are tagged `manual|bot` and `clean|peeked`. API: `/api/trade/*` (`engine/account.py` is what a bot will call:
  `place_order`, `modify`, `close`, `positions_view`, `orders_view`, `account`).
* **Verifying it:** `python -m tools.verify_engine --trades 1500` replays random trades over the whole database through the engine
  and through an independent slow reference, and checks the engine against the Phase 3 grader. It opens the history **read-only**
  and keeps the engine in memory, so it cannot touch your data or your paper account. This proves the engine is *internally
  correct*; it cannot prove parity with a real MT5 account (needs a real feed).

## Bots (Phase 7)
A bot is a pure function from "what the market looked like up to time t" to a list of actions. It never gets the engine, a
database handle, the grader, labels or Peek data (`tests/test_bot_architecture.py` enforces the import rules), so it cannot look
ahead. (The one deliberate exception is an explicit forward-data run, above; its orders are all tagged `peeked`.) Write one by subclassing `bot.interface.Bot` (see `bot/strategies/`), implement `decide(ctx)`, register it in
`bot/strategies/__init__.py` — the UI's parameter form is generated from its `PARAMS`.

* **Replay** stored days (fast, on a throw-away in-memory account; the history file is opened read-only):
  ```bash
  .venv/bin/python -m bot.run --list
  .venv/bin/python -m bot.run --bot momentum_breakout --from 2026-09-21 --to 2026-10-16 --out run.html --json run.json
  ```
* **Delayed live** on the paper account: back office → **Bot lab** → *Run on the terminal* (or the old Trades sheet → **Bot** tab in the Chart lab). It decides on the delayed
  clock exactly as you would, at every minute of chart time, whoever advances the clock; it resumes after a server restart.
* Both modes call the same `BotRunner.step`, so they take identical decisions (tested, including with UI polls and manual orders
  interleaved). A future "MT5 demo with real fills" mode would be another executor behind the same step — not built (needs a real feed).
* **Risk limits** are enforced outside the bot (open positions, size per order, total size, orders/hour, daily loss with auto-flatten)
  and a kill switch halts a bot that crashes or is repeatedly refused by the engine. Bot trades are always `source=bot`, never peeked.
* **Grading** (`bot/evaluate.py`) compares a run with the labels and the oracle: *selection edge* (are its entries better than average?),
  *… ex-spread*, **direction edge** (its side vs the opposite side of the same entry), oracle lift and exit effect. All z-scores are
  **cluster-robust** (trades in the same market hour count as one): a naive test overstates certainty by ~1.5x on these data.
* **Read the grading honestly.** On the simulated market the demo momentum rule and its exact mirror both show a large "selection edge" and
  oracle lift — they select cheap, volatile minutes, which helps in either direction. Only the *direction edge* and the ex-spread edge speak to
  skill; both are ~0 here, as they must be on a market with no edge. `random_entry` is the null benchmark any real strategy must beat.

## Baseline, oracle and report (Phase 5)
```bash
.venv/bin/python -m analysis.report --from 2025-03-01 --to 2025-03-31                       # whole range, all symbols
.venv/bin/python -m analysis.report --last-days 5 --session ny --max-spread 1.5 --symbol EURUSD,GBPUSD
.venv/bin/python -m analysis.report --from 2025-03-03 --to 2025-03-07 --direction buy --horizon 120 --oracle run --json out.json
```
Writes one self-contained HTML page (default `reports/report_<from>_<to>.html`; phone friendly) and optionally the JSON.
The same report is served live at `/report?...` (HTML) and `/api/report?...` (JSON) with the same filters
(`symbols`, `date_from`, `date_to`, `session`, `max_spread`, `direction`, `horizon`, `oracle`).
* Dates are calendar days in the **display timezone**; `--to` is inclusive; `--utc` switches to UTC dates. `--session ny`
  = `sessions.ny_open`..`ny_session_end`, or `HH:MM-HH:MM` (may wrap midnight).
* **Baseline** = taking every entry. **Oracle** = the entries that worked best in hindsight (`analysis.oracle` in the config:
  `top_decile`, `run`, `fixed_tp`) — an answer key, not a strategy. Per feature bucket: n, average net pips per horizon,
  win %, Δ vs baseline, z, the share of oracle entries in the bucket and the **lift** (oracle share / bucket share).
* Buy and sell are separate populations. Buckets with fewer than `analysis.min_bucket_n` (100) samples are flagged unreliable.
* Caveats the report prints itself: entries overlap (so z-scores are optimistic) and many buckets are tested at once
  (so some will look significant by chance). The **z ex-spread** column adds the entry spread back to every outcome: spread varies with
  liquidity and many features proxy for it, so on net pips a bucket can look "bad" just because it is expensive. Judge price
  edge by that column. **Never present results on simulated data as findings.**

## Tests
```bash
.venv/bin/python -m pytest                      # backend (~335; test_engine_parity.py alone takes ~90 s)
cd web && npx vitest run && npx tsc --noEmit    # frontend logic + types
# against a running stack (uses real Chrome at phone size):
LAB_ADMIN_PASSWORD=... LAB_URL=http://127.0.0.1:8000 node e2e/terminal.mjs   # the MT5 terminal: screens, one-click trading, obeys the admin settings (RESETS the paper account)
LAB_ADMIN_PASSWORD=... LAB_URL=http://127.0.0.1:8000 node e2e/admin.mjs      # login wall, settings, bot lab (forward vs blind, replay, train)
# the original lab suites now run through /admin -> Chart lab, so they need the admin password too:
LAB_ADMIN_PASSWORD=... LAB_URL=http://127.0.0.1:8000 node e2e/run.mjs        # 48 end-to-end checks
LAB_ADMIN_PASSWORD=... LAB_URL=http://127.0.0.1:8000 node e2e/trade.mjs      # 41 paper-trading checks (touch-drags SL/TP; RESETS the server's paper account)
LAB_ADMIN_PASSWORD=... BOT_DB=/path/COPY.db LAB_PYTHON=.venv/bin/python node e2e/bot.mjs   # bot checks; starts its own collector+server on a COPY of the db (~8 min)
LAB_URL=http://127.0.0.1:8000 node e2e/pwa.mjs        # manifest / icons / service worker
LAB_ADMIN_PASSWORD=... LAB_URL=http://127.0.0.1:8000 SOAK_MIN=30 node e2e/soak.mjs   # a 30-minute simulated phone session
```

## Conventions worth knowing (all configurable in `config.yaml` unless noted)
* Order types: market, limit, stop and **stop limit** (a stop that, once reached, places a limit order at its stop-limit price); pending orders can
  expire (GTC / today in the terminal's time zone / a given time) and are then marked `expired`. Timeframes M1-D1 are built on the server; W1 and MN
  (calendar weeks / months) are built by the terminal from D1.
* Times are UTC milliseconds everywhere; bid and ask are both kept; buys fill at ask, sells at bid; P&L is in pips.
* Entry = first tick at/after the entry time; horizons are measured from the fill; `extra_slippage_pips` is charged on
  each fill; SL fills at the tick price (gaps lose more), TP at its level. Entries while the market is closed, or whose
  outcome window crosses a market close, are ungradable and get no label.
* Labels reveal what happened *after* an entry by design, so `/labels` only serves entries at or before the delayed clock
  (`now - delay`); entries up to the live edge need an explicit `peeked=1`.
* Only `grader/` may read ticks past a cutoff; everything else reads through `core.dataview.DataView`
  (enforced by `tests/test_architecture.py`).

## Real data on Ubuntu
The `MetaTrader5` Python package is Windows-only. The collector talks to the broker through the small `BrokerClient`
interface in `collector/mt5_client.py`, so a Linux data source (MT5 under Wine with a bridge, or another tick API) is a
drop-in replacement. Until one is chosen, use `--sim`.
