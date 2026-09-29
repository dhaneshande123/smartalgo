# CLAUDE.md — SmartAlgo Trading Platform

## Project Overview

Institutional-grade algorithmic options trading platform for Indian markets (NSE/BSE).
**Python 3.12+ / FastAPI backend** with a **React (Vite + Tailwind) dashboard**.
Live market data via **Fyers API v3** (WebSocket + REST). Paper trading fully functional.

**GitHub**: https://github.com/dhaneshande123/smartalgo.git
**Branch**: `main`

## Latest Session (September 2026) — Fly-High deploy fix + performance overhaul

### Critical Fly-High Deploy Fix (September 28, 2026)

**Fly-High deploy payload field-name mismatch**: `vwap_engine.py` used `side/option_type/strike/
entry_price` in `deploy_payload.legs[]`, but `deploy_strategy()` in `api.py` reads
`action/type/offset/premium` (matching the scalper's convention). Every Fly-High trade was built as
SELL/ATM/CE/zero-premium regardless of the actual signal. Fixed: `vwap_engine.py` now emits
`action/type/offset/premium` matching `deploy_strategy()`. Also added `lot_size` to the payload.

**Auto-deploy `sig.action` AttributeError**: `run_flyhigh_auto_cycle` referenced `sig.action` but
`FlyHighSignal` has `direction`. The error fired after deploy succeeded, silently swallowing success
tracking. Fixed: `sig.action` → `sig.direction`.

**Reconnect interval regression**: `_reconnect_fyers_feed` called
`start_background_refresh(interval=0.5)` instead of `3.0`, triggering a 429 rate-limit storm
(~120/200 req/min for index refresh alone) after every reconnect. Fixed: `0.5` → `3.0`.

### Performance Overhaul (September 28, 2026)

**SQLite write removed from GET endpoint**: `list_deployed_strategies` called `save_strategy()`
for every strategy on every 1-second poll, blocking the async event loop with synchronous SQLite
I/O 5+ times/sec. Removed — persistence happens only on mutations.

**Parallel candle fetches**: `_scalper_fetch_market` fetched daily + intraday candles sequentially
(8s+8s worst case). Now uses `asyncio.gather` for parallel fetch (halves latency).

**Frontend polling reduction**: Reduced aggregate polling load by ~60%:
- Global default: 2s → 5s, staleTime 1s → 2s
- Deployed strategies / P&L: 1s → 3s
- Scalper signal / OI signals: 2s → 5s
- Positions / PnL / market depth: 2s → 5s
- Paper trading (5 hooks): 2s → 5s

**Tick freshness check**: `get_cached_tick()` consumers now check `last_tick_age_seconds < 60`
before using WS tick as "live". Market indices endpoint reports `source: "fyers_cached"` when
ticks are stale. Fly-High signal falls back to chain spot when WS tick is >60s old.

**Honest health badge**: `/api/health` now reports `fyers_degraded` when connected but REST is
unhealthy (`rest_healthy=False`). Header badge shows amber "Degraded" state with tooltip.

**Config debounce**: Fly-High config inputs now use local state + 600ms debounce instead of
firing a mutation on every keystroke.

**Signal invalidation after deploy**: `useDeployFlyHigh.onSuccess` now invalidates `flyhighSignal`,
preventing stale deploy button for up to 3s after deploy.

**Files modified**:
- `core/vwap_engine.py` — deploy_payload field names (type/action/offset/premium/lot_size)
- `core/api.py` — reconnect interval, parallel candle fetch, SQLite write removal, health badge,
  tick freshness, auto-deploy logging fix
- `dashboard/src/main.jsx` — global polling default 2s→5s
- `dashboard/src/hooks/useApi.js` — per-hook polling reductions, signal invalidation after deploy
- `dashboard/src/pages/FlyHigh.jsx` — config debounce
- `dashboard/src/components/Layout/Header.jsx` — degraded feed state

## Previous Session (August 2026) — Critical fixes + Fly-High VWAP strategy

### Bug Fixes (August 31, 2026)

**Paper positions disappearing**: Split-brain between `_deployed_strategies` (SQLite-persisted)
and `PaperBroker` (in-memory only). Option chain deploys to `_deployed_strategies` but Paper
Trading page only read from PaperBroker. Also returned 409 when no paper session was active.
Fixed: `paper_trading_positions()` now merges both sources via shared `_parse_position()` helper,
409 block removed, frontend retry changed from `false` to `1`.

**429 Rate Limit storm**: Background index refresh at 0.5s consumed ~120/200 req/min budget alone.
Combined with frontend polling at 1s for indices + option chain + executor chain refresh, total
easily exceeded 200 req/min. Fixed: backend refresh interval 0.5s → 3.0s (saves ~100 req/min),
frontend polling 1s → 3s, 429 retry with exponential backoff (3 retries, 2s/4s/6s) on
`get_option_chain`, executor now shares `_fyers_chain_cache` instead of making independent calls.

**ADX flickering in Scalper UI**: Two-part — backend early-return response omitted `adx` field
entirely (returned only `signal: null`), and frontend conditionally hid badge when `adx` was null.
Fixed: backend early-return now includes `adx: 0.0, regime: "unknown", direction: "", regime_detail: {}`;
frontend badge always renders with fallback `(sig?.adx ?? 0)`.

**Fyers WebSocket disconnect (no auto-recovery)**: SDK singleton pattern (`FyersDataSocket._instance`)
prevented creating fresh WebSocket — "new" connections silently reused the dead instance. SDK's
internal reconnect doesn't call user's `on_close` callback, so `_ws_connected` stayed True.
Fixed: `stop_websocket_stream()` clears `FyersDataSocket._instance = None` before each start,
SDK reconnect disabled (`reconnect=False`, we handle it), stale-tick detection (>60s with no tick
during market hours → dead), REST health tracking via `_consecutive_rest_failures`. Watchdog in
`dashboard_executor._maybe_reconnect_fyers()` detects REST down + WS flag down + stale ticks.

**Files modified for fixes**:
- `core/fyers_live_feed.py` — singleton clearing, stale-tick tracking, 429 retry, REST health
- `core/api.py` — refresh intervals 0.5→3.0, ADX early-return fields, paper positions merge
- `core/strategy_engine/dashboard_executor.py` — watchdog rewrite, chain cache sharing
- `dashboard/src/hooks/useApi.js` — polling 1s→3s, retry policy
- `dashboard/src/pages/Scalper.jsx` — ADX badge always-render
- `dashboard/src/pages/PaperTrading.jsx` — positions always-poll (no isActive gate)

### Fly-High Strategy (VWAP Crossover)

New strategy tab: **Fly-High** — VWAP crossover on 5-minute candles for intraday momentum entries.

**Entry logic**: When a 5-min candle closes above/below VWAP (crossover detection using candles[-2]
and candles[-3]), with ADX gate (default ≥20), take a 1-ITM option position (one strike below ATM
for CE, one above for PE). Skips the first 5-min candle (09:15-09:20 noise). SL = previous candle
low/high, capped at `max_sl_points` (default 30). Position sizing by Rs-risk (default Rs 3000).
Max 2 trades/day. Entry window 09:20-14:30.

**Exit plan**: Book 50% at target (+60% default), trail remainder with 25% give-back, structural
stop if VWAP recross, premium floor -40% backstop.

**New files**:
- `core/vwap_engine.py` — `compute_vwap_series()`, `detect_crossover()`, `select_itm_strike()`,
  `generate_signal()`, `DEFAULT_CONFIG`, `FlyHighSignal` dataclass
- `dashboard/src/pages/FlyHigh.jsx` — full page: VWAP status bar, signal card, deploy button,
  blockers list, crossover detail, performance stats, editable config, recent trades

**New endpoints**: `GET /api/flyhigh/signal/{symbol}`, `GET /api/flyhigh/config`,
`POST /api/flyhigh/config`, `POST /api/flyhigh/deploy`, `GET /api/flyhigh/performance`

**Wired into**: App.jsx route, Sidebar (Rocket icon after Scalper), client.js, useApi.js hooks

### Live-data hardening + Fly-High auto-deploy (September 10, 2026)

Triggered by a live session where the header showed "Connected" but Scalper/Fly-High showed
"insufficient data", VWAP/ADX flickered, and no trades could form.

**Root cause — expired token, misleading UI**: The Fyers access token had expired, so
`_live_feed.is_connected` was False and everything silently fell back to **mock data**. The header
badge read `!isError && health` (just "is the API server up?"), NOT the actual feed state — so it
showed green "Connected" over mock data. Fixed: header now reads `health.components.market_data_feed`
and shows three honest states — green **"Live Data"** (`fyers_live`), amber **"Mock Data"** (feed
down, simulated), red **"Disconnected"** (API down). This is the key UX fix so a dead feed is obvious.

**Silent candle-fetch failures**: `_scalper_fetch_market` wrapped `get_candles` in a 3s
`asyncio.wait_for` with `except Exception: pass` — but the 429 retry inside `get_candles` sleeps
2s/4s/6s, so any rate-limited candle fetch was killed by the 3s timeout before a retry could
complete, and swallowed with zero logging. Fixed: timeout 3s → 8s, real `logger.warning` on every
failure path, candle failures now increment `_consecutive_rest_failures` (feeds the watchdog),
429 retry cadence tightened to 1.5s/3s/4s, and guard checks in `get_candles` now log why they bail.

**VWAP/ADX flickering**: ~1 in 6 signal polls got an empty candle fetch → `vwap=0, adx=0` → UI
reset to "—". Fixed with a **last-good candle cache** (`_last_good_candles` in api.py): when a poll
gets an empty/short series, serve the previous good one (seconds old, fine for a 5-min strategy).
Stabilizes both Fly-High and Scalper.

**Fly-High auto-deploy**: New `run_flyhigh_auto_cycle()` (api.py) mirroring `run_scalper_auto_cycle`,
driven by `DashboardExecutor._maybe_flyhigh_auto` every ~25s during market hours (`FLYHIGH_AUTO_INTERVAL`).
Reads `vwap_engine` config `auto_deploy`/`auto_symbols`. Guards: one RUNNING Fly-High per underlying,
max_trades_per_day cap, kill-switch aware, all signal gates still apply (force=False). Toggle on the
Fly-High page header (Zap pill) + active banner. Default OFF — paper-validate first. Note: the manual
Deploy button is correctly disabled when there's no valid signal; auto-deploy is the hands-free path.

**Fly-High P&L display**: `/api/flyhigh/performance` now returns `open_pnl` (live, from RUNNING
trades), `net_pnl` (open + closed), and `open_positions[]`. New live P&L strip on the Fly-High page
shows net / open / realized P&L plus each running position.

**Files modified**:
- `core/api.py` — `_last_good_candles` cache, candle timeout 3→8s + logging, `run_flyhigh_auto_cycle`,
  performance `open_pnl`/`net_pnl`/`open_positions`/`auto_deploy`
- `core/fyers_live_feed.py` — candle-fetch logging, REST-failure tracking on candles, faster 429 retry
- `core/strategy_engine/dashboard_executor.py` — `_maybe_flyhigh_auto` + `FLYHIGH_AUTO_INTERVAL`
- `dashboard/src/components/Layout/Header.jsx` — honest 3-state feed badge (Live/Mock/Disconnected)
- `dashboard/src/pages/FlyHigh.jsx` — auto-deploy toggle + banner, live P&L strip

## Previous Session (July 2026) — Multi-profile scalper + Fyers reliability

### Multi-Profile Scalper (July 6, 2026)

Added 3 scalper profiles — **Expiry**, **Daily**, **Momentum** — with a profile-switcher UI.

**Architecture**: single flat `_scalper_config` dict with a `profile` key. Switching profiles
applies a preset (`PROFILE_PRESETS` in `scalper_engine.py`) that overwrites profile-specific
keys (ADX threshold, book/trail/stop %, strike offsets, entry cutoff) while preserving shared
config (risk_per_trade, min_oi, auto_deploy toggle). Exit params are baked into each strategy's
`risk_params` at deploy time — switching profiles mid-position is safe.

**Profiles**:
- **Expiry** (default): S/R breakout, 2/1/0 OTM strikes, +50% book, -35% floor, ADX>20 strict
- **Daily**: S/R breakout, 1/0/0 OTM (closer to ATM), +30% book, -25% floor, ADX>22 strict
- **Momentum**: ATM candle momentum (body > 60% ATR + 1.5x vol spike, no S/R breakout needed),
  always ATM strikes, +20% quick book, -20% floor, ADX>12 balanced (lower because candle IS
  the confirmation)

**Files modified**:
- `core/scalper_engine.py` — `PROFILE_PRESETS`, `PROFILE_OFFSETS`, `_detect_momentum_candle()`,
  `select_strike(offsets=)`, `generate_signal` profile branching, `scalp_profile` in risk_params
- `core/api.py` — `POST /api/scalper/profile` endpoint, performance `?profile=` filter
- `dashboard/src/pages/Scalper.jsx` — profile pill selector, dynamic header, live ADX badge,
  profile in config strip
- `dashboard/src/api/client.js` + `hooks/useApi.js` — `switchScalperProfile` + hook

**Auto-deploy** works for all 3 profiles (executor reads current config's `auto_deploy` flag;
profile switch preserves it). Default OFF for daily/momentum — paper-validate first.

**Future enhancements** (not yet implemented, in priority order):
1. ~~VWAP Mean Reversion~~ (done — implemented as Fly-High VWAP crossover strategy, Aug 2026)
2. GEX (Gamma Exposure) levels — add to `compute_sr_levels()` for smarter breakout targets
3. OI Change Rate — supplementary signal filter (delta OI confirms direction)
4. IV vs Realized Vol gap — gate for buy vs sell decision

### Fyers reliability + P&L/scalp UX fixes (July 2-3, 2026)

Triggered by live paper-trading a Tuesday NIFTY expiry: Fyers kept dropping mid-session with
no way to recover short of a backend restart, Connect Fyers timed out, and two UX asks
(P&L always green, scalp window later) needed wiring.

Root causes found (not guessed — traced the actual code paths):
- **`DashboardStrategyExecutor` had a stale-reference bug**: `core/api.py`'s `_reconnect_fyers_feed`
  assigned `dashboard_executor.live_feed = new_feed`, but the executor only ever read
  `self._live_feed` internally — a name mismatch. Every reconnect (manual or auto) silently
  kept the executor on the **old, dead feed object** even though Settings showed "Connected."
  Fixed with a `live_feed` property/setter on `DashboardStrategyExecutor`.
- **No auto-reconnect existed anywhere.** `_ws_connected`/`is_connected` were checked but never
  acted on — a dropped feed stayed dropped until a manual click. Added a watchdog inside the
  executor's tick loop (`_maybe_reconnect_fyers`): retries the stored token every 30s, backs off
  to every 5 min after 5 consecutive failures (avoids hammering Fyers when the token is genuinely
  expired), and separately restarts just the WebSocket if it dies while the REST session is fine
  (`FyersLiveFeed.ws_connected` new property, distinct from `is_connected`).
- **Reconnect button was invisible when actually needed.** `Settings.jsx` only rendered the
  Reconnect button inside the *connected* branch — the disconnected view showed only the full
  re-login form. Added a Reconnect option to the disconnected branch too (shown whenever a token
  is on file).
- **"Network connection timed out" on Connect Fyers**: `_exchange_auth_code` (OAuth token
  exchange) called the Fyers SDK with no timeout — on the corporate network's SSL-inspecting
  proxy (see Known Issues below) this hung indefinitely. Now runs with a 20s join timeout and
  surfaces an actionable message instead of hanging.
- **Staying connected through a system lock**: not a code fix — the backend runs independent of
  the browser tab already. Real fix is Windows power config (disable sleep-on-lock + network
  adapter power-saving) so the always-on watchdog above has something to keep alive. See
  "Staying Connected Through Lock" below.

Also shipped:
- **P&L always shown green** regardless of sign, across Dashboard/Scalper/P&L Analytics/Trade
  Analytics/Paper Trading/AI Signals/DeployedStrategiesPnL — user preference, explicit ask.
  Deliberately NOT applied to Win Rate, Max Drawdown, Profit Factor, or Charges — those are
  risk/quality signals, not P&L, and forcing them green would hide real information.
- **Scalp entry window extended**: `entry_cutoff` in `core/scalper_engine.py` DEFAULT_CONFIG
  moved 14:30 → **15:10** (catches the ~15:00 late-session spike window) while leaving the
  15:15 EOD `SQUARE_OFF_TIME` in `dashboard_executor.py` untouched — user chose the safer
  option over a full 15:20 cutoff after being shown that 15:20 entries would get force-exited
  almost immediately by the unchanged 15:15 square-off.

**Not yet verified live**: all backend fixes need a `uvicorn` restart to take effect (the running
paper session was left untouched mid-fix to avoid disrupting it). Frontend fixes are live via
Vite HMR. Next session: confirm the watchdog actually self-heals a real drop, and confirm the
extended scalp window fires on the next NIFTY Tuesday expiry.

## Previous Session (June 2026) — "simple but powerful" refactor

Direction: simplify to a focused cockpit, add an expiry-day scalper, validate paper-first.
Honest framing agreed with user: you can't beat HFT desks (Jane Street/Citadel) on speed —
the retail edge is niches too small for institutions (expiry scalping, thin OTM strikes) plus
discipline + risk management. Nothing here is *proven* yet; next step is live paper validation
on an expiry day, then read the scalp performance panel before any tuning or going live.

Shipped this session:
- **Dashboard → cockpit**: hero P&L summary + deployed strategies front-and-center.
- **Sidebar decluttered**: MAIN group + collapsible MORE (all routes kept).
- **Expiry Scalper** (`core/scalper_engine.py`, `/scalper`): S/R-driven OTM option buying,
  strict regime gate, partial-book + trail exits, ₹2k risk sizing. Paper-validate first.
- **Strategy verification**: fixed 3 decorative entry conditions (BB_POSITION, BB_WIDTH, IS_EXPIRY_DAY).
- **AI auto-deploy**: opt-in toggle + 60s background loop (kill-switch aware, dedup, capped).
- **SENSEX support** end-to-end; per-symbol expiry weekdays — **NIFTY=Tuesday, SENSEX=Thursday**.
- **Fyers rate-limit fix** (429 → empty chains): chain TTL 3s, executor chain refresh throttled 3s.
- **Fyers Reconnect** button (Settings → API Keys) — retries stored token, no re-login.
- **Scalp performance panel**: win rate, avg R, profit factor, exit-reason attribution.

Key gotchas this session (see sections below for detail): Fyers tokens expire daily (Reconnect);
do NOT lower the chain cache TTLs (429s); option-chain symbols must be in `OPTION_CHAIN_SYMBOLS`.

## Environment

- Platform: Windows 11 with Git Bash (use Unix shell syntax in all commands)
- Python: 3.12+ (required)
- Node: 18+ for the dashboard (Vite + React + Tailwind)
- Known issue: SSL certificate errors on corporate network — use VPN or mobile hotspot
- Unicode (Rs symbol) causes cp1252 encoding errors in Python print — avoid in logs

### Staying Connected Through Lock

The trading engine (Fyers feed, executor, order placement) runs in the `uvicorn` backend
process, independent of any browser tab. Locking Windows does not stop it by itself. To keep
it alive through a lock reliably:
1. Settings → Power & sleep → set **Sleep** to Never (at least while plugged in).
2. Device Manager → Network adapters → your adapter → Power Management → uncheck
   "Allow the computer to turn off this device to save power."
3. Keep the `uvicorn` terminal window open (minimized, not closed) — closing it kills the
   process regardless of power settings.

With the July 2026 auto-reconnect watchdog (see Latest Session above), a brief network hiccup
during lock now self-heals instead of requiring a manual Reconnect click afterward.

## Quick Start

```bash
# Backend (port 8080)
cd algo-trading-platform
uvicorn core.api:app --host 0.0.0.0 --port 8080

# Dashboard (port 5173)
cd dashboard && npm run dev
```

Copy `.env.example` to `.env` and fill in Fyers API credentials.

## Architecture

### Data Flow: Strategy Deploy -> Entry -> P&L
```
AI Signal Engine / Strategy Builder / Manual
        | POST /api/strategies/deploy
  _deployed_strategies dict (in-memory, persisted to SQLite)
        | DashboardExecutor (background task, every 5s)
        | evaluates entry_conditions against live indicators
        | when conditions met -> places entry orders via PaperBroker
  _refresh_strategy_pnl() (every ~1s)
        | reads LTP from _fyers_chain_cache
        | computes gross P&L, deducts charges/slippage
  Frontend polls /api/deployed-strategies every 1s
```

### Two Strategy Systems
1. **`_deployed_strategies`** dict — In-memory in `core/api.py` (~line 1944). AI/Builder/manual strategies. Persisted to SQLite. Endpoint: `GET /api/deployed-strategies`
2. **`PaperTradingManager._active_strategies`** — Session-scoped paper strategies. Endpoint: `GET /api/paper-trading/strategies` (merges both sources)

### Option Chain Cache
`_fyers_chain_cache` — per-underlying option chain. Cache TTL: 3.0s. Used by P&L refresh, LTP updates, Risk Engine Greeks. Executor refreshes chains for all RUNNING strategies every tick.

### Fyers Rate Limiting (IMPORTANT)
- Total API budget: ~200 req/min from Fyers; option chain is the rate-limited call.
- Current safe usage: option chain `_CHAIN_CACHE_TTL=10.0s`, indices `_INDICES_CACHE_TTL=5.0s`,
  candle cache `_CANDLE_CACHE_TTL=30.0s`, background refresh interval `10.0s`,
  signal-level cache `_FLYHIGH_SIGNAL_CACHE_TTL=15.0s`, frontend signal poll `15s`.
  Executor chain refresh shares `_fyers_chain_cache` instead of independent calls.
  429 retry with exponential backoff (3 retries, 2s/4s/6s waits) on `get_option_chain`.
- **Do NOT reduce these TTLs/intervals** — previous lower values caused persistent
  `429 request limit reached` → empty chains (0 strikes) for ALL symbols.
- Bursty testing (many curls + restarts) can trip Fyers' limit into a cooldown; wait ~30s.
- Historical candle API is a separate bucket but shares the same limit.
- Access tokens expire daily — use Settings → **Reconnect** (stored token) or **Connect Fyers** (re-auth).
- Live chain requires the symbol in `OPTION_CHAIN_SYMBOLS` (fyers_live_feed): NIFTY/BANKNIFTY/
  FINNIFTY/MIDCPNIFTY/SENSEX/BANKEX. Missing symbol → empty chain (spot only).

## Key Modules

| Module | File | Purpose |
|--------|------|---------|
| **Fyers Live Feed** | `core/fyers_live_feed.py` | WebSocket + REST for live quotes, option chains, candles. Cache-first candle fetching with 429 retry. |
| **Risk Engine** | `core/risk_engine/calcs.py` | Black-Scholes Greeks, VaR, stress test, margin, limits, drawdown (941 lines) |
| **Risk Engine (classes)** | `core/risk_engine/*.py` | Circuit breaker, drawdown monitor, Greeks aggregator, margin calculator, position tracker, risk manager |
| **OI Signal Engine** | `core/oi_signal_engine.py` | 4-factor OI signal generation: buildup, PCR, max pain, S/R breach. Confidence scoring + stability filter. |
| **Scalper Engine** | `core/scalper_engine.py` | Expiry-day OTM option-buying. S/R levels (CPR, PDH/PDL, VWAP, ORB, round numbers, OI walls), STRICT regime gate (ADX + confirmed breakout close + volume), time-based strike selection, liquidity filter, ₹-risk sizing. Stateless; chain-format agnostic (`normalize_chain_rows`). |
| **VWAP Engine** | `core/vwap_engine.py` | Fly-High strategy: VWAP crossover on 5-min candles, ADX gate, 1-ITM strike selection, capped SL (prev candle low/high), Rs-risk sizing. Reuses `normalize_chain_rows` from scalper. |
| **AI Signal Engine** | `core/ai_signal_engine.py` | Strategy recommendations from live market data |
| **Strategy Fit Matrix** | `core/strategy_fit.py` | Maps regimes/IV/ADX to optimal strategy types |
| **Market Regime** | `core/market_regime.py` | Classifies market: TRENDING_UP/DOWN, RANGING, HIGH_VOL |
| **Indicators Engine** | `core/indicators.py` | 25+ indicators via TA-Lib (150+ C-lib) with numpy fallback. RSI, MACD, BB, Supertrend, ADX, VWAP, ATR, Stochastic, CCI, Williams %R, MFI, Aroon, Ichimoku, Keltner, Donchian, CMF, candlestick patterns |
| **Condition Evaluator** | `core/condition_evaluator.py` | Evaluates entry conditions against live indicator values |
| **Dashboard Executor** | `core/strategy_engine/dashboard_executor.py` | Background lifecycle: entry/exit/risk checks every 5s |
| **Charges Calculator** | `core/charges.py` | Indian F&O charges: brokerage, STT, exchange, GST, SEBI, stamp, slippage |
| **State Store** | `core/state_store.py` | SQLite: strategies, trades, P&L, settings, equity snapshots, risk events, candle cache |
| **Backtest Engine** | `core/backtest/engine.py` | Event-driven backtesting with simulated broker |
| **VectorBT Engine** | `core/backtest/vectorbt_engine.py` | Vectorized backtesting (100x faster): RSI, MACD, BB, Supertrend, EMA strategies. Indian charges model. |
| **Optuna Optimizer** | `core/backtest/optimizer.py` | TPE-based parameter optimization: auto-tunes indicator params to maximize Sharpe/Sortino/return |
| **QuantStats Reports** | `core/backtest/reports.py` | Professional tearsheets, metrics, snapshot, strategy comparison |
| **Backtest Data Loader** | `core/backtest/fyers_data_loader.py` | Chunked Fyers historical download with 429 retry + SQLite cache |
| **Symbol Master** | `core/symbol_master.py` | Dynamic lot sizes from Fyers symbol master |
| **Paper Broker** | `core/paper_trading/paper_broker.py` | Simulated broker with slippage, commission, order matching |

## API Endpoints — Real Data Status

### Fully Real (no mocks)
- `GET /api/market/option-chain/{symbol}` — Live Fyers chain (3s cache), OI change included
- `GET /api/market/oi-signals/{symbol}` — 4-factor OI signal engine (buildup+PCR+maxpain+S/R) with confidence scoring, stability filter, deploy payload
- `GET /api/scalper/signals/{symbol}` — expiry-day scalper: live S/R levels, regime gate, strike pick, deploy-ready payload (warm-cache + bounded fetch, <0.5s)
- `GET|POST /api/scalper/config` — scalper config (risk/trade, regime, exits, filters)
- `POST /api/scalper/deploy` — deploy a scalp to paper (regenerates signal; `force` bypasses gate)
- `GET|POST /api/auto-deploy/config` — AI auto-deploy loop toggle (enabled, symbols, min_confidence, max_per_cycle)
- `POST /api/auto-deploy/execute` — manual one-shot auto-deploy of ready signals (paper)
- `GET /api/scalper/performance` — closed-scalp analytics: win rate, avg R, profit factor, expectancy, avg hold, and exit-reason attribution (target/trail/structural/stop/eod/manual). `_bucket_exit_reason` maps raw reasons to buckets.
- `GET /api/flyhigh/signal/{symbol}` — VWAP crossover signal: direction, strike, SL, target, premium, blockers
- `GET|POST /api/flyhigh/config` — Fly-High strategy config (ADX threshold, SL cap, risk/trade, entry window)
- `POST /api/flyhigh/deploy` — Deploy Fly-High signal to paper trading
- `GET /api/flyhigh/performance` — Fly-High closed-trade analytics
- `POST /api/fyers/reconnect` — retry live feed with the stored token (no re-login)
- `GET /api/market/candles/{symbol}` — Cache-first: SQLite -> Fyers -> mock last resort
- `GET /api/risk/*` (11 endpoints) — Real Risk Engine: VaR, Greeks, stress test, margin, limits, kill switch, breaches, audit
- `GET /api/portfolio/greeks` — Real Black-Scholes via Risk Engine
- `GET /api/portfolio/positions` — Real from deployed strategies (paper mode)
- `GET /api/portfolio/pnl` — Real from deployed strategies
- `GET /api/portfolio/margin` — Real from Risk Engine margin calculator
- `GET /api/deployed-strategies` — Real in-memory + SQLite persisted
- `GET /api/paper-trading/*` — Real PaperBroker data
- `GET /api/orders` — Real from SQLite trade_log
- `GET /api/indicators/{symbol}` — Real from Fyers candles + local calculation
- `GET /api/pnl/summary|by-strategy|charges|trade-book|equity-curve` — Real from deployed strategies + SQLite
- `GET /api/greeks/iv-surface/{symbol}` — Real IV back-solved from Fyers chain via Black-Scholes
- `POST /api/backtest/fetch-history` — Bulk download to SQLite candle cache
- `GET /api/backtest/cache-status` — SQLite candle inventory
- `GET /api/vbt/strategies` — List available vectorbt strategy types
- `POST /api/vbt/backtest` — Run vectorized backtest (vectorbt + TA-Lib + Indian charges)
- `POST /api/vbt/optimize` — Optuna TPE parameter optimization (max 200 trials)
- `GET /api/vbt/objectives` — List optimization objectives (Sharpe, Sortino, etc.)
- `POST /api/vbt/report` — QuantStats metrics/tearsheet/snapshot from equity curve
- `POST /api/vbt/compare` — Side-by-side comparison of multiple backtest results
- `GET /api/indicators/available` — List all 25+ indicators (TA-Lib status)

### Template/Catalog (not mock, but pre-configured)
- `GET /api/strategies` — 16 strategy templates with simulated stats (disclaimer shown on UI). Deploy button routes to real PaperBroker.
- `GET /api/strategy-signals` — AI signal generation from live data (real calculations, template strategies)

### Monitoring (real, psutil-based)
- `GET /api/monitoring/health` — Real CPU/memory (psutil), Fyers status, executor ticks, DB size, kill switch state
- `GET /api/monitoring/alerts` — Real risk events from SQLite audit log (breaches, kill switch, limit changes)
- `POST /api/monitoring/alerts/{id}/acknowledge` — Logs acknowledgement to SQLite
- `GET /api/monitoring/metrics` — Real CPU, memory, WebSocket count, trade count, executor throughput
- `GET /api/monitoring/metrics/history` — Equity curve from SQLite snapshots

### No Mock Endpoints Remain
All 16 pages use real data. The only "simulated" content is the Strategy catalog
(16 pre-configured templates with disclaimer banner).

## Frontend Pages — Current State

| Page | Route | Data Source | Status |
|------|-------|-------------|--------|
| Dashboard | `/` | usePnLSummary + useDeployedStrategies + WS ticker + Risk | **REAL** — COCKPIT: hero P&L summary + deployed strategies front-and-center, quick actions, equity curve, risk snapshot |
| Scalper | `/scalper` | Scalper Engine (S/R + regime + strike) | **REAL** — expiry-day OTM buying, live S/R rail, partial-book+trail deploy, force-override |
| Fly-High | `/flyhigh` | VWAP Engine (crossover + ADX + 1-ITM) | **REAL** — VWAP crossover on 5-min candles, signal card, deploy, config editor, performance |
| Market Data | `/market` | Fyers option chain + OI Analysis + OI Signals (4-factor engine + deploy) | **REAL** (3s refresh, OI change arrows, signals 5s) |
| Charts | `/charts` | Fyers candles via lightweight-charts (TradingView) | **REAL** (interactive zoom/pan/crosshair) |
| Portfolio | `/portfolio` | deployed_strategies + Risk Engine margin | **REAL** (zeros when empty) |
| Strategies | `/strategies` | Template catalog + real deploy | **TEMPLATE** (disclaimer banner) |
| Builder | `/builder` | Visual leg config + conditions | **REAL** |
| Risk | `/risk` | Risk Engine (11 endpoints) + limits editor | **REAL** (auto-kill, audit log) |
| Orders | `/orders` | SQLite trade_log | **REAL** (empty when no trades) |
| Backtest | `/backtest` | VectorBT + Optuna + Event-Driven | **REAL** (3-tab UI: VectorBT, Optimizer, Event-Driven) |
| P&L Analytics | `/pnl` | deployed_strategies + SQLite | **REAL** |
| IV Surface | `/iv-surface` | Fyers chain + Black-Scholes IV back-solve | **REAL** |
| Paper Trading | `/paper` | PaperBroker + deployed strategies | **REAL** |
| AI Signals | `/ai-signals` | Live Fyers data + AI engine | **REAL** |
| Trade Analytics | `/trade-analytics` | SQLite trade_log + pnl_snapshots | **REAL** |
| Monitoring | `/monitoring` | psutil + WebSocket + executor + SQLite risk events | **REAL** |
| Settings | `/settings` | Fyers credential management | **REAL** |

## Frontend Features

### UI Theme System (3 themes)
- **Classic** — Default dark/light theme. Purple accent (#7c3aed). Glass-morphic cards with backdrop-blur.
- **Pro (Latest UI)** — Professional fintech theme. Sky-blue/teal accent (#38BDF8). Solid cards, Inter font, deep navy backgrounds. Scoped under `html.pro`.
  - CSS: `dashboard/src/styles/pro-theme.css` (~450 lines)
  - Toggle: "Latest UI" / "Classic" button (Zap icon) in header
  - Design: Institutional Bloomberg-like. Colors: bg #020617, cards #0F172A, sidebar #060C1A, accent #38BDF8, profit #34D399, loss #F87171
  - Fonts: Inter (sans), JetBrains Mono (numbers). Loaded via Google Fonts in `index.html`.
- **Sci-Fi** — Neon cyberpunk theme. Cyan accent (#00f0ff). Animated grid background, glow effects. Scoped under `html.scifi`.
  - CSS: `dashboard/src/styles/scifi-theme.css` (1,174 lines)
  - Toggle: "Sci-Fi" / "Classic" button (Sparkles icon) in header
- Context: `dashboard/src/context/ThemeContext.jsx` — `uiStyle` state ('classic'|'scifi'|'pro')
- Both Pro and Sci-Fi force dark mode. Light/dark toggle disabled when either is active.
- Persisted to `localStorage.smartalgo-ui-style`.
- UI/UX Pro Max skill installed at `~/.claude/skills/ui-ux-pro-max/` — design intelligence with 67 styles, 96 palettes, 57 font pairings.

### OI Analysis Tab (Market Data page)
- Tab: "Option Chain" / "OI Analysis" / "OI Signals" switcher in card header
- Component: `dashboard/src/pages/MarketData/OIAnalysis.jsx`
- Shows: PCR, Net Call/Put OI Change, Max Pain, Support/Resistance, per-strike activity classification, top strikes, OI change bar chart

### OI Signal Engine (Market Data page)
- Module: `core/oi_signal_engine.py` — 4-factor analysis with weighted scoring
- Component: `dashboard/src/pages/MarketData/OISignals.jsx`
- Factors: Buildup Analysis (35%), PCR Extreme (25%), Max Pain Gravity (20%), S/R Breach (20%)
- Confidence scoring (0-100%), stability filter (signal must persist 2+ ticks = "CONFIRMED")
- Strike recommendation: ATM at >80% confidence, 1 OTM at 60-80%, 2 OTM below 60%
- One-click deploy to paper trading with auto SL/Target/MaxHold
- Deploy requires: confidence >= 60% AND status == CONFIRMED
- VIX adjustment: high VIX reduces confidence (volatile markets = less predictable)
- API: `GET /api/market/oi-signals/{symbol}` — refreshes every 5s on frontend

### Beginner Descriptions
- 100+ tooltips/subtitles across all pages (title attributes + inline text)
- Dashboard, Market Data, OI Analysis, Risk, AI Signals, Paper Trading, Portfolio

## Database Schema

SQLite (`data/platform_state.db`) — WAL mode, thread-safe

| Table | Purpose |
|-------|---------|
| `deployed_strategies` | Strategy JSON data, persisted across restarts |
| `pnl_snapshots` | Historical P&L per strategy |
| `trade_log` | Entry/exit trades with timestamp, price, reason |
| `paper_sessions` | Paper trading session state |
| `settings` | Key-value store (risk_limits, kill_switch_active, kill_switch_meta) |
| `equity_snapshots` | Portfolio equity every 60s (drives drawdown) |
| `risk_events` | Audit log of breaches, kill switch, limit changes |
| `candles_cache` | Historical OHLCV candles (PK: symbol+resolution+ts) |

## Risk Engine Architecture

### Conservative Auto-Kill Policy
- WARN at 80% of any limit (debounced 60s per limit)
- Auto-kill at 100% of `max_daily_loss` OR `max_drawdown_pct` only
- Other limits (Greeks, concentration) WARN only

### Kill Switch
- Persisted in SQLite settings (survives restart)
- Blocks `POST /api/strategies/deploy` and `POST /api/auto-deploy/execute`
- Reset via `DELETE /api/risk/kill-switch`
- On activation: squares off all RUNNING/ENTERED strategies

### Greeks Computation
- Fyers does NOT provide Greeks — computed locally via Black-Scholes
- IV per strike back-solved via Newton-Raphson from option market price
- Falls back to India VIX if per-strike IV unavailable

## Important Patterns & Gotchas

### P&L Refresh for Exited Strategies
When server restarts, option chain cache is empty. `_refresh_strategy_pnl()` skips EXITED/STOPPED strategies to preserve `realized_pnl`.

### Charges Model (Indian F&O)
Brokerage Rs 20/order, STT 0.0625% SELL, Exchange 0.053%, GST 18%, SEBI 0.0001%, Stamp 0.003% BUY, Slippage 0.5%

### Duplicate Prevention
`execute_auto_deploy` checks for RUNNING strategies with same `strategy_class` before deploying.

### Condition Evaluator Coverage (verified June 2026)
`core/condition_evaluator.py` must support every indicator referenced in
`strategy_fit.py` entry_conditions, else those conditions return "indicator
unavailable" -> fail -> with ALL trigger the strategy never enters via manual
deploy (AI-deployed strategies bypass conditions via `ai_deployed=True`).
Audit fixed 3 gaps: **BB_POSITION** (%B), **BB_WIDTH** (alias for BB_BANDWIDTH),
**IS_EXPIRY_DAY** (reads nearest expiry from `_fyers_chain_cache` via
`scalper_engine.is_expiry_day`). IV_RANK returns None until IV history
accumulates from chain polling — by design; AI scorer treats None as neutral 50.
KNOWN BUG (fix in auto-deploy work): `ai_signal_engine.build_deploy_payload`
hardcodes `"underlying": "NIFTY"` — ignores the global underlying selector.

### Field Name Normalization
PaperBroker returns Decimal strings with different field names. API layer normalizes to frontend-expected names.

### Position Signed Quantity
`quantity` is signed: +ve = BUY, -ve = SELL. Frontend expects unsigned qty + separate side field.

## NSE Market Context

- Market hours: 09:15-15:30 IST, pre-open 09:00-09:08
- Auto square-off at 15:15 (broker buffer)
- **Weekly expiries (post-rationalisation 2024-25): NIFTY = Tuesday, BSE SENSEX = Thursday.**
  Encoded in `scalper_engine.WEEKLY_EXPIRY_WEEKDAY` (NIFTY=1/Tue, SENSEX=3/Thu).
  `is_expiry_day(chain, symbol)` uses the chain's actual expiry date as primary
  (handles holiday shifts), weekday rule as fallback. NOTE: older mock-chain
  helpers in api.py still use a hardcoded Thursday formula — not used by the scalper.
- Lot sizes: NIFTY=75, BANKNIFTY=30, SENSEX=20 (dynamic from Fyers symbol master)
- Strike steps: NIFTY=50, BANKNIFTY=100, SENSEX=100
- SENSEX (`BSE:SENSEX-INDEX`) supported across selector, scalper, AI signals, mock data.

## What's Been Built

1. Fyers API v3 integration (quotes, option chains, candles, WebSocket)
2. Live/Paper mode toggle with safety gates
3. Dynamic lot sizes from Fyers symbol master
4. Strategy Builder with visual leg configuration + entry conditions
5. Indicators engine (RSI, MACD, BB, Supertrend, ADX, VWAP, ATR)
6. Market regime classifier
7. AI signal engine with strategy-fit matrix
8. Auto-deploy with duplicate prevention
9. Dashboard executor (background strategy monitoring + entry/exit)
10. Paper trading with PaperBroker (slippage, commission, order matching)
11. Realistic charges model (all Indian F&O regulatory charges)
12. SQLite state persistence (strategies, trades, P&L, settings)
13. Risk Engine: Black-Scholes Greeks, VaR, stress test, margin, limits, auto-kill
14. Risk limits editor (frontend) with persistence
15. Kill switch (real: squares off positions, blocks deploys, persists)
16. Equity snapshots every 60s (drives drawdown tracking)
17. Risk audit log (compliance trail)
18. Option chain OI change columns (up/down arrows) + sticky headers
19. OI Analysis tab (Market Maker activity: PCR, support/resistance, activity classification)
20. 3-theme UI system: Classic (glass, purple), Pro (solid, teal/fintech), Sci-Fi (neon, cyan) — header toggles
21. Beginner-friendly descriptions (100+ tooltips across all pages)
22. TradingView interactive charts (lightweight-charts: zoom, pan, crosshair)
23. SQLite candle cache + bulk historical data downloader
24. Fyers rate limit handling (70/min usage vs 200 limit, 429 retry)
25. IV Surface with real Black-Scholes IV back-solve from option chain
26. Mock data removed from: Dashboard, Portfolio, Orders, P&L Analytics
27. Monitoring page: real psutil CPU/memory, Fyers status, executor health, SQLite risk events as alerts
28. Trade Analytics: gross vs net win/loss classification, insight card, low-sample warning
29. IV Surface: real Black-Scholes IV back-solve from Fyers option chain
30. P&L Analytics: all 5 endpoints wired to deployed strategies + SQLite (no mock)
31. Strategies page: disclaimer banner for simulated returns
32. **MILESTONE: Zero mock data remains across all 16 pages**
33. OI Signal Engine: 4-factor (buildup + PCR + max pain + S/R) signal generation with confidence scoring, stability filter, strike recommendation, and one-click paper deploy
34. TA-Lib integration: 25+ indicators with C-library speed (Stochastic, CCI, Williams %R, MFI, Aroon, Ichimoku, Keltner, Donchian, CMF, candlestick patterns) + batch compute_all_series() for vectorized backtesting
35. VectorBT engine: 5 built-in strategies (RSI, MACD, BB, Supertrend, EMA crossover) with Indian F&O charges model, SL/TP support, equity curves
36. Optuna optimizer: TPE-based parameter optimization with 6 objectives (Sharpe, Sortino, return, Calmar, win rate, profit factor)
37. QuantStats reports: professional metrics (CAGR, drawdown, skew, kurtosis), HTML tearsheets, strategy comparison
38. Backtest page UI: 3-tab layout (VectorBT / Optimizer / Event-Driven). VectorBT tab: 5-strategy selector cards, parameter tuning, SL/TP, equity curve + metrics + QuantStats analytics + monthly returns heatmap. Optimizer tab: Optuna TPE with 6 objectives, trial history table, best params display. Event-Driven tab: original bar-by-bar replay preserved.
39. CSV Export: Client-side CSV download utility (`utils/exportCsv.js`) with formatters for trades, P&L, backtest results, equity curves. Download buttons on Orders (order book, trade book), Trade Analytics (trade log, strategy breakdown), P&L Analytics (trade book, strategy P&L snapshot), and Backtest (VBT results + equity curve).
40. Notification Center: Bell icon in header (`components/common/NotificationCenter.jsx`) with unread badge, dropdown panel showing risk alerts and trade events. Severity-coded (CRITICAL/BREACH/WARN/INFO) with icons. Desktop browser notifications for critical alerts via Notification API. Read/unread state persisted to localStorage. Backend logs STRATEGY_DEPLOY and STRATEGY_STOP events to risk_events table. Acknowledge button per alert. Links to /monitoring for full history.
41. Multi-Underlying Support: Global `UnderlyingContext` (`context/UnderlyingContext.jsx`) with NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY. Compact pill selector in Header (NIFTY|BANK|FIN|MIDCP). Selection persisted to localStorage. Wired into: MarketData (option chain + OI), IVSurface (IV heatmap), Strategies (deploy with underlying), AISignals (regime/signals/recs), Backtest (symbol dropdowns). API client and hooks updated to pass symbol parameter. Charts and StrategyBuilder already had their own selectors.
42. Dashboard cockpit redesign (June 2026): hero P&L summary (Net/Realized/Unrealized/Charges/Win-Rate from `/api/pnl/summary`) + deployed strategies front-and-center, compact index ticker, quick-action tiles, equity curve, risk snapshot. Margin display clamped to "100%+" (paper sizing isn't margin-aware).
43. Sidebar restructure: decluttered into MAIN group (Dashboard, AI Signals, Scalper, Market Data, Charts, Strategies, Paper Trading, Backtest) + collapsible MORE group (P&L, Orders, Portfolio, Risk, Builder, IV Surface, Trade Analytics, Monitoring, Settings). All routes preserved.
48. Scalper auto-arm (hands-free, separate from AI auto-deploy): `_scalper_config.auto_deploy` + `run_scalper_auto_cycle()` driven by `DashboardExecutor._maybe_scalper_auto` every ~25s. Deploys a scalp the instant a STRICT signal forms (force=False — expiry/window/regime gates still apply). Guards: one RUNNING scalp per underlying (no stacking), max_trades_per_day cap, kill-switch aware. Toggle on the Scalper page (`auto_deploy` + `auto_symbols`). Default OFF. NOTE: AI Signals auto-deploy (#45) deploys the strategy_fit TEMPLATES (iron condor/straddle/etc.), NOT scalps — they are independent systems.
45. AI Auto-Deploy loop (June 2026): opt-in toggle on AI Signals page. `_auto_deploy_config` (enabled/symbols/min_confidence/max_per_cycle) drives `DashboardExecutor._maybe_auto_deploy` — every ~60s during market hours, deploys ready signals (>=70% conf) to paper via shared `_execute_auto_deploy_core` (dedup + kill-switch aware + per-cycle cap). Fixed `build_deploy_payload` NIFTY hardcode (now respects underlying). Endpoints `GET|POST /api/auto-deploy/config`.
46. SENSEX support: added to frontend selector (`UNDERLYINGS`), header pill, mock data (`INDEX_BASE`). Backend mapping (`BSE:SENSEX-INDEX`), strike step 100, lot size 20 already existed. Scalper + AI signals work for SENSEX. Per-symbol expiry weekdays: NIFTY=Tuesday, SENSEX=Thursday (`scalper_engine.WEEKLY_EXPIRY_WEEKDAY`).
47. Scalp performance + exit-attribution panel (`/api/scalper/performance` + Scalper page card): win rate, avg R (P&L/risk), profit factor, expectancy, avg hold, best/worst, and a breakdown bar of WHICH exit fired (target/trail/breakeven/structural/premium-floor/time/eod/manual) with per-bucket count + P&L + win-rate. Tells you if the exit plan is the edge or the leak. Also: `force` deploy now synthesizes a direction (spot vs VWAP) + protective stop so the manual-override button works in flat conditions (liquidity filter still applies).
49. **Fly-High VWAP Strategy** (`core/vwap_engine.py` + `/flyhigh` page): VWAP crossover on 5-min candles with ADX gate, 1-ITM strike selection, capped SL, partial book + trail exits. Full UI: signal card, deploy, config editor, performance stats.
50. **Fyers reliability hardening (Aug 2026)**: SDK singleton clearing, stale-tick detection (>60s = dead), REST health tracking, 429 retry with backoff, rate budget optimization (0.5s→3s refresh, 1s→3s frontend polling), paper positions merge (split-brain fix), ADX early-return fields.
44. Expiry-Day Scalper (`core/scalper_engine.py` + `/scalper` page): S/R-driven OTM option buying for the 1->50 Rs gamma moves. Levels: CPR/PDH-PDL/VWAP/ORB/round-numbers/OI-walls. STRICT regime gate (ADX>=20 + confirmed breakout close + volume) filters chop. Time-based strike (OTM early -> ATM late), liquidity filter (min OI/vol, max spread%), Rs-risk sizing (default Rs2000/trade). Exit plan in `DashboardExecutor._manage_scalp`: book 50% at +50% -> stop to breakeven -> trail (give-back 30%), structural stop (spot reclaims level), premium-floor -35% backstop, late-session tightening, EOD square-off. Paper-validate first. Endpoint is warm-cache + bounded-fetch (<0.5s) to stay under the 10s frontend axios timeout.

## Trading Intelligence Stack (Installed June 2026)

### Python Libraries
- **vectorbt 1.0.0** — Vectorized backtesting, portfolio simulation, indicator analysis
- **optuna 4.9.0** — Hyperparameter optimization for strategy tuning
- **quantstats 0.0.81** — Portfolio analytics, tearsheets, benchmark comparison
- **TA-Lib 0.6.8** — 150+ technical indicators (C library with Python wrapper)
- **finstack-mcp 0.10.0** — MCP server for Indian (NSE/BSE) + global financial data

### MCP Servers (configured in `.mcp.json`)
- **sqlite** — Direct SQL access to `data/platform_state.db` for analytics
- **filesystem** — File-level access to the project directory
- **memory** — Persistent knowledge graph for cross-session context
- **finstack** — Indian market data (NSE/BSE quotes, fundamentals, analytics)

### Claude Code Skills (in `~/.claude/skills/`)
- **ui-ux-pro-max/** — Design intelligence (67 styles, 96 palettes, fintech patterns)
- **vectorbt-backtesting-skills/** — 5 skills: backtest, optimize, quick-stats, setup, strategy-compare
- **claude-trading-skills/** — 62 trading/quant skills (options-pricing, risk-management, regime-detection, walk-forward-validation, etc.)

## Remaining Work

### Priority Items
- **Backtest engine wiring**: Event-driven engine still uses CSV files. VectorBT engine reads from SQLite cache. Frontend Backtest page UI complete (3 tabs).
- ~~**Multi-underlying**: BANKNIFTY/FINNIFTY support across all pages~~ (done — global UnderlyingContext + header selector)

### Future Enhancements
- Telegram/Discord notifications (in-app notification center done — external channels pending)
- ~~Trade log CSV/Excel export~~ (done — client-side CSV export on 4 pages)
- Historical OI tracking (store OI snapshots every 5 min)
- Strategy P&L attribution (delta P&L vs theta P&L vs vega P&L)
- Intraday equity chart from SQLite snapshots
- Strategy templates library (save/reload configs)
