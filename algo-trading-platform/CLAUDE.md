# CLAUDE.md — SmartAlgo Trading Platform

## Project Overview

Institutional-grade algorithmic options trading platform for Indian markets (NSE/BSE).
**Python 3.12+ / FastAPI backend** with a **React (Vite + Tailwind) dashboard**.
Live market data via **Fyers API v3** (WebSocket + REST). Paper trading fully functional.

**GitHub**: https://github.com/dhaneshande123/smartalgo.git
**Branch**: `main`

## Environment

- Platform: Windows 11 with Git Bash (use Unix shell syntax in all commands)
- Python: 3.12+ (required)
- Node: 18+ for the dashboard (Vite + React + Tailwind)
- Known issue: SSL certificate errors on corporate network — use VPN or mobile hotspot
- Unicode (Rs symbol) causes cp1252 encoding errors in Python print — avoid in logs

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
- Total API budget: ~200 req/min from Fyers
- Current usage: ~70 req/min (option chain 3s cache=20, indices 2s=30, indicators 5s=12, regime 10s=6, candles cached=2)
- **Do NOT reduce cache TTLs below current values** — will cause 429 rate limit errors
- Historical candle API is separate bucket but shares the same limit

## Key Modules

| Module | File | Purpose |
|--------|------|---------|
| **Fyers Live Feed** | `core/fyers_live_feed.py` | WebSocket + REST for live quotes, option chains, candles. Cache-first candle fetching with 429 retry. |
| **Risk Engine** | `core/risk_engine/calcs.py` | Black-Scholes Greeks, VaR, stress test, margin, limits, drawdown (941 lines) |
| **Risk Engine (classes)** | `core/risk_engine/*.py` | Circuit breaker, drawdown monitor, Greeks aggregator, margin calculator, position tracker, risk manager |
| **AI Signal Engine** | `core/ai_signal_engine.py` | Strategy recommendations from live market data |
| **Strategy Fit Matrix** | `core/strategy_fit.py` | Maps regimes/IV/ADX to optimal strategy types |
| **Market Regime** | `core/market_regime.py` | Classifies market: TRENDING_UP/DOWN, RANGING, HIGH_VOL |
| **Indicators Engine** | `core/indicators.py` | RSI, MACD, Bollinger, Supertrend, ADX, VWAP, ATR |
| **Condition Evaluator** | `core/condition_evaluator.py` | Evaluates entry conditions against live indicator values |
| **Dashboard Executor** | `core/strategy_engine/dashboard_executor.py` | Background lifecycle: entry/exit/risk checks every 5s |
| **Charges Calculator** | `core/charges.py` | Indian F&O charges: brokerage, STT, exchange, GST, SEBI, stamp, slippage |
| **State Store** | `core/state_store.py` | SQLite: strategies, trades, P&L, settings, equity snapshots, risk events, candle cache |
| **Backtest Engine** | `core/backtest/engine.py` | Backtesting with simulated broker |
| **Backtest Data Loader** | `core/backtest/fyers_data_loader.py` | Chunked Fyers historical download with 429 retry + SQLite cache |
| **Symbol Master** | `core/symbol_master.py` | Dynamic lot sizes from Fyers symbol master |
| **Paper Broker** | `core/paper_trading/paper_broker.py` | Simulated broker with slippage, commission, order matching |

## API Endpoints — Real Data Status

### Fully Real (no mocks)
- `GET /api/market/option-chain/{symbol}` — Live Fyers chain (3s cache), OI change included
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

### Template/Catalog (not mock, but pre-configured)
- `GET /api/strategies` — 16 strategy templates with simulated stats (disclaimer shown on UI). Deploy button routes to real PaperBroker.
- `GET /api/strategy-signals` — AI signal generation from live data (real calculations, template strategies)

### Still Mock (to be fixed)
- `GET /api/monitoring/*` — System health, alerts (hardcoded fake metrics)
- WebSocket `/ws/alerts` — Hardcoded alert templates

## Frontend Pages — Current State

| Page | Route | Data Source | Status |
|------|-------|-------------|--------|
| Dashboard | `/` | WebSocket + deployed strategies + Risk Engine | **REAL** (all mock removed) |
| Market Data | `/market` | Fyers option chain + OI Analysis tab | **REAL** (3s refresh, OI change arrows) |
| Charts | `/charts` | Fyers candles via lightweight-charts (TradingView) | **REAL** (interactive zoom/pan/crosshair) |
| Portfolio | `/portfolio` | deployed_strategies + Risk Engine margin | **REAL** (zeros when empty) |
| Strategies | `/strategies` | Template catalog + real deploy | **TEMPLATE** (disclaimer banner) |
| Builder | `/builder` | Visual leg config + conditions | **REAL** |
| Risk | `/risk` | Risk Engine (11 endpoints) + limits editor | **REAL** (auto-kill, audit log) |
| Orders | `/orders` | SQLite trade_log | **REAL** (empty when no trades) |
| Backtest | `/backtest` | SQLite candle cache + engine | **PARTIAL** (data pipeline built, engine wiring pending) |
| P&L Analytics | `/pnl` | deployed_strategies + SQLite | **REAL** |
| IV Surface | `/iv-surface` | Fyers chain + Black-Scholes IV back-solve | **REAL** |
| Paper Trading | `/paper` | PaperBroker + deployed strategies | **REAL** |
| AI Signals | `/ai-signals` | Live Fyers data + AI engine | **REAL** |
| Trade Analytics | `/trade-analytics` | SQLite trade_log + pnl_snapshots | **REAL** |
| Monitoring | `/monitoring` | Mock data (not yet fixed) | **MOCK** |
| Settings | `/settings` | Fyers credential management | **REAL** |

## Frontend Features

### Sci-Fi UI Theme Toggle
- Toggle: "New UI" / "Classic" button in header
- CSS: `dashboard/src/styles/scifi-theme.css` (1,174 lines) scoped under `html.scifi`
- Context: `dashboard/src/context/ThemeContext.jsx` — `uiStyle` state ('classic'|'scifi')
- Forces dark mode when active. Persisted to localStorage.

### OI Analysis Tab (Market Data page)
- Tab: "Option Chain" / "OI Analysis" switcher in card header
- Component: `dashboard/src/pages/MarketData/OIAnalysis.jsx`
- Shows: PCR, Net Call/Put OI Change, Max Pain, Support/Resistance, per-strike activity classification, top strikes, OI change bar chart

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

### Field Name Normalization
PaperBroker returns Decimal strings with different field names. API layer normalizes to frontend-expected names.

### Position Signed Quantity
`quantity` is signed: +ve = BUY, -ve = SELL. Frontend expects unsigned qty + separate side field.

## NSE Market Context

- Market hours: 09:15-15:30 IST, pre-open 09:00-09:08
- Auto square-off at 15:15 (broker buffer)
- Weekly expiries: NIFTY (Thu), BANKNIFTY (Wed), FINNIFTY (Tue)
- Lot sizes: NIFTY=75, BANKNIFTY=30 (dynamic from Fyers symbol master)
- Strike steps: NIFTY=50, BANKNIFTY=100

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
20. Sci-Fi UI theme toggle (Classic/New UI)
21. Beginner-friendly descriptions (100+ tooltips across all pages)
22. TradingView interactive charts (lightweight-charts: zoom, pan, crosshair)
23. SQLite candle cache + bulk historical data downloader
24. Fyers rate limit handling (70/min usage vs 200 limit, 429 retry)
25. IV Surface with real Black-Scholes IV back-solve from option chain
26. Mock data removed from: Dashboard, Portfolio, Orders, P&L Analytics

## Remaining Work

### Priority Items
- **Backtest engine wiring**: Connect `core/backtest/engine.py` to SQLite candle cache (data pipeline built, engine integration pending)
- **Monitoring page**: Replace mock health/alerts with real psutil + WebSocket counts
- **Multi-underlying**: BANKNIFTY/FINNIFTY support across all pages

### Future Enhancements
- Telegram/Discord notifications
- Trade log CSV/Excel export
- Historical OI tracking (store OI snapshots every 5 min)
- Strategy P&L attribution (delta P&L vs theta P&L vs vega P&L)
- Intraday equity chart from SQLite snapshots
- Strategy templates library (save/reload configs)
