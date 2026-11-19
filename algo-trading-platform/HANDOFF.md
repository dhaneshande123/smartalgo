# HANDOFF.md — SmartAlgo Trading Platform

Last updated: 2026-11-19

---

## Project Goal

SmartAlgo is an institutional-grade algorithmic options trading platform for Indian markets (NSE/BSE). It provides real-time market data, automated signal generation, paper trading, risk management, and a 16-page React dashboard. The platform currently operates in **paper trading mode** using Fyers API v3 for live market data. The primary active strategies are an expiry-day **Scalper** (S/R breakout) and **Fly-High** (VWAP crossover on 5-min candles).

The retail edge thesis: niches too small for institutional HFT desks (expiry scalping, thin OTM strikes) combined with discipline and risk management. Nothing is proven yet — the platform is in live paper-validation phase.

---

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Backend | Python / FastAPI | 3.12+ / 0.109+ |
| Frontend | React / Vite / Tailwind CSS | 18.2 / 5.0 / 3.3 |
| Live Data | Fyers API v3 (WebSocket + REST) | — |
| Database | SQLite (WAL mode) | Built-in |
| Charts | TradingView lightweight-charts | 5.2 |
| State Mgmt | TanStack React Query | 5.8 |
| Indicators | TA-Lib (C library, 150+ indicators) with numpy fallback | — |
| Backtesting | vectorbt + Optuna (TPE optimizer) + QuantStats | — |
| Risk | Custom Black-Scholes Greeks, VaR, stress testing | — |
| Icons | Lucide React | 0.294 |
| HTTP Client | Axios | 1.6 |

---

## Folder Structure

```
algo-trading-platform/
├── core/                          # Backend (Python)
│   ├── api.py                     # FastAPI app — ALL endpoints (~9200 lines, monolith)
│   ├── vwap_engine.py             # Fly-High strategy: VWAP crossover + ADX gate
│   ├── scalper_engine.py          # Expiry Scalper: S/R breakout + regime gate
│   ├── fyers_live_feed.py         # Fyers WebSocket + REST, candle cache, 429 retry
│   ├── indicators.py              # 25+ indicators (TA-Lib with numpy fallback)
│   ├── oi_signal_engine.py        # 4-factor OI signals (buildup/PCR/max pain/S/R)
│   ├── ai_signal_engine.py        # AI strategy recommendations from live data
│   ├── market_regime.py           # Market classification: trending/ranging/volatile
│   ├── strategy_fit.py            # Maps regimes to optimal strategies
│   ├── condition_evaluator.py     # Evaluates entry conditions vs live indicators
│   ├── charges.py                 # Indian F&O charges model (STT, GST, slippage)
│   ├── symbol_master.py           # Dynamic lot sizes from Fyers symbol master
│   ├── state_store.py             # SQLite persistence (strategies, trades, candles)
│   ├── config.py                  # Platform configuration
│   ├── constants.py               # Shared constants
│   ├── auth/                      # JWT authentication (middleware, models)
│   ├── backtest/                  # Backtesting engines
│   │   ├── engine.py              # Event-driven backtester
│   │   ├── vectorbt_engine.py     # Vectorized backtester (100x faster)
│   │   ├── optimizer.py           # Optuna TPE parameter optimization
│   │   ├── reports.py             # QuantStats tearsheets
│   │   ├── fyers_data_loader.py   # Chunked historical download + SQLite cache
│   │   └── simulated_broker.py    # Simulated order matching
│   ├── risk_engine/               # Risk management
│   │   ├── calcs.py               # Black-Scholes Greeks, VaR, stress test (~941 lines)
│   │   ├── circuit_breaker.py     # Auto-kill on limit breach
│   │   ├── drawdown_monitor.py    # Drawdown tracking
│   │   ├── greeks_aggregator.py   # Portfolio-level Greeks
│   │   ├── margin_calculator.py   # Margin requirements
│   │   └── risk_manager.py        # Orchestrator
│   ├── strategy_engine/
│   │   ├── dashboard_executor.py  # Background lifecycle: entry/exit/risk every 5s
│   │   ├── parameter_store.py     # Strategy parameter persistence
│   │   └── runner.py / scheduler.py
│   ├── paper_trading/             # Paper broker (simulated fills, slippage)
│   ├── broker_gateway/            # Multi-broker adapters (Zerodha, Angel, Dhan, Shoonya)
│   ├── greeks_engine/             # IV solver, IV surface, payoff diagrams
│   ├── oms/                       # Order management (bracket, slicer, smart router)
│   ├── pnl_engine/                # P&L calculation, charges, trade book
│   ├── monitoring/                # Health, alerts, metrics (psutil-based)
│   ├── notifications/             # Telegram + Discord alerting
│   ├── market_data/               # Tick normalizer, candle builder, order book
│   ├── cache/                     # Redis + market data cache (not actively used)
│   ├── database/                  # SQLAlchemy models, migrations (not actively used)
│   ├── event_bus/                 # Pub/sub event system
│   └── websocket/                 # WebSocket routes for frontend streaming
│
├── dashboard/                     # Frontend (React)
│   ├── src/
│   │   ├── App.jsx                # Router + route guards (auth-gated)
│   │   ├── main.jsx               # Entry point, BrowserRouter, React Query (5s default)
│   │   ├── api/client.js          # Axios client, all API call functions
│   │   ├── hooks/useApi.js        # React Query hooks for every endpoint
│   │   ├── context/
│   │   │   ├── ThemeContext.jsx    # 3-theme system (Classic/Pro/Sci-Fi)
│   │   │   └── UnderlyingContext.jsx  # Global symbol selector (NIFTY/BANKNIFTY/etc)
│   │   ├── contexts/AuthContext.jsx   # JWT auth state
│   │   ├── pages/                 # 16+ page components (see Pages section)
│   │   ├── styles/
│   │   │   ├── pro-theme.css      # Pro theme (~450 lines)
│   │   │   └── scifi-theme.css    # Sci-Fi theme (~1174 lines)
│   │   └── utils/exportCsv.js     # CSV export utility
│   ├── tailwind.config.js
│   ├── vite.config.js
│   └── package.json
│
├── strategies/                    # Strategy templates/examples
│   ├── base_strategy.py
│   └── examples/                  # 10 example strategies (iron condor, gamma scalp, etc)
│
├── tests/                         # Test suite (mostly stubs)
│   ├── conftest.py
│   └── unit/test_core/            # Unit tests per module
│
├── data/
│   └── platform_state.db          # SQLite database (created at runtime)
│
├── config/platform.yaml           # Platform configuration
├── .env.example                   # Environment variable template
├── .env                           # Active environment (not committed)
├── requirements.txt               # Python dependencies
├── docker-compose.yml             # Docker setup (not actively used)
├── CLAUDE.md                      # Detailed developer reference (~615 lines)
└── HANDOFF.md                     # This file
```

---

## What Is Completed

### Core Trading Features
- **Fly-High strategy** (option-chart VWAP): evaluates the 1-ITM CE and 1-ITM PE **5-min option charts** directly, with no NIFTY filter. Entry = VWAP cross-up + SuperTrend(10, 3) UP + ADX(14) >= 10 on the option chart. If CE and PE fire on the same candle, the signal is skipped. SL = the signal candle low (premium). Target 1:1: book 50%, then the runner trails the option SuperTrend line (never below entry). Rs-risk sizing, entry window 09:20-15:15, max 2 trades/day
- **Expiry Scalper**: S/R breakout (CPR, PDH/PDL, VWAP, ORB, round numbers, OI walls), 3 profiles (Expiry/Daily/Momentum), STRICT regime gate (ADX + confirmed breakout + volume), partial-book + trail exits
- **Hands-free execution**:
  - Fly-High: one **Deploy Strategy** button arms it for today (`POST /api/flyhigh/deploy`, persisted in SQLite setting `flyhigh_armed`). It must be re-armed each trading day. The 25s backend cycle enters and exits on its own. **Stop Strategy** (`POST /api/flyhigh/stop`) halts new entries, and an open position stays managed.
  - Scalper: 60s auto-deploy toggle.
  - Both are kill-switch aware, dedup-protected and capped.
- **Fly-High signal log**: `GET /api/flyhigh/signal-log/{symbol}` is a read-only list of today's CE/PE cross-ups. It shows the forward-simulated outcome (Target Hit / SL Hit / open), the blocked reason, and whether each one was traded
- **Paper trading**: Full PaperBroker with simulated fills, slippage, commission

### Risk Management
- Black-Scholes Greeks (locally computed — Fyers doesn't provide them)
- IV back-solved per strike via Newton-Raphson from market prices
- VaR, stress testing, margin calculation
- Kill switch (persisted in SQLite, survives restart, blocks all deploys)
- Auto-kill at 100% of max_daily_loss or max_drawdown_pct
- Circuit breaker, drawdown monitor, audit log

### Market Data
- Fyers WebSocket + REST with auto-reconnect watchdog
- Option chain cache (10s TTL), indices cache (5s), candle cache (30s), signal cache (15s)
- 429 rate limit handling: exponential backoff (3 retries, 2s/4s/6s), budget-aware caching
- Last-good candle cache to prevent VWAP/ADX flickering on empty fetches
- SQLite candle cache for historical data
- OI signal engine: 4-factor analysis (buildup 35%, PCR 25%, max pain 20%, S/R 20%)

### Backtesting
- VectorBT engine (vectorized, 100x faster): RSI, MACD, BB, Supertrend, EMA strategies
- Event-driven backtester
- Optuna TPE parameter optimization (max 200 trials)
- QuantStats reports (tearsheets, metrics, comparisons)
- Fyers historical data downloader with SQLite cache

### Dashboard (16 pages, all using real data)
- Dashboard (cockpit: hero P&L, deployed strategies, equity curve, risk snapshot)
- Fly-High (Deploy/Stop Strategy, CE & PE chart panels with VWAP/SuperTrend/ADX checks, running positions with SL/target/trail + Exit, read-only signal log, performance, config editor)
- Scalper (3-profile switcher, S/R rail, signal card, performance analytics)
- Market Data (option chain, OI Analysis, OI Signals with 1-click deploy)
- Charts (TradingView interactive: zoom/pan/crosshair)
- Portfolio, Strategies, Builder, Risk, Orders, Backtest (3-tab), P&L Analytics, IV Surface, Paper Trading, AI Signals, Trade Analytics, Monitoring, Settings
- 3-theme system: Classic (purple), Pro (sky-blue Bloomberg-like), Sci-Fi (neon cyberpunk)
- Honest feed state badge: Live Data (green) / Mock Data (amber) / Disconnected (red)
- 100+ beginner tooltips

### Indian Market Specifics
- Full F&O charges model: brokerage Rs 20/order, STT, exchange, GST, SEBI, stamp, slippage
- Per-symbol expiry weekdays: NIFTY=Tuesday, SENSEX=Thursday
- Market hours: 09:15-15:30 IST, pre-open 09:00-09:08
- SENSEX support end-to-end
- Dynamic lot sizes from Fyers symbol master

---

## What Is In Progress

| Feature | Files | Status |
|---------|-------|--------|
| Fly-High option-chart rewrite | `core/vwap_engine.py`, `core/api.py`, `dashboard/src/pages/FlyHigh.jsx` | Nov 19 2026 — moved from the NIFTY chart to the CE/PE option charts; Deploy/Stop arm-for-today replaces the auto-deploy toggle and manual per-signal deploy. Simulation-verified; needs a live session |
| Fly-High exit enforcement | `core/strategy_engine/dashboard_executor.py` (`_manage_flyhigh`), `core/api.py` | Fixed Nov 19 2026 — SL/target were display-only (no `scalp` flag, fell through to generic Rs-P&L checks). Now premium SL, 1:1 partial book, breakeven + option-SuperTrend trail, floor and max-hold all fire. Simulation-verified; needs live session |
| P&L accuracy | `core/api.py`, `core/charges.py`, `core/strategy_engine/dashboard_executor.py` | Fixed double-charge bug + partial exit tracking, monitoring for edge cases |
| Fyers auto-reconnect | `core/fyers_live_feed.py`, `core/strategy_engine/dashboard_executor.py` | Watchdog implemented, needs extended live session testing |

---

## Pending Tasks (Priority Order)

1. **Live market validation** — Run a full trading session and confirm: after **Deploy Strategy** the backend enters on a CE/PE option-chart setup without manual intervention, the signal log shows correct outcomes and `taken` flags, the SuperTrend trail ratchets, kill switch triggers and blocks correctly, **Fly-High exits fire on their own** (Today's Trades should show `premium_sl_stop` / `target_hit` / `trail_stop` instead of only `manual_stop`)
2. **Backtest engine wiring** — VectorBT engine exists but isn't wired to the Fly-High / Scalper strategies specifically. Need to plug the VWAP crossover logic into the vectorized backtester for parameter optimization
3. **Multi-underlying support** — Currently trades NIFTY primarily. BANKNIFTY, FINNIFTY, SENSEX data flows work but strategy auto-deploy only runs for configured symbols. Need concurrent strategy execution per underlying
4. **Trade log export** — CSV/Excel export of trade history for tax and audit purposes. `exportCsv.js` utility exists but isn't wired to all trade tables
5. **Notifications** — Telegram and Discord notifiers exist (`core/notifications/`) but aren't connected to signal/trade events. Wire up for: signal alerts, trade entry/exit, kill switch activation, daily P&L summary
6. **Real broker integration** — Broker gateway adapters exist for Zerodha, Angel One, Dhan, Shoonya (`core/broker_gateway/`) but are not wired into the execution flow. Currently paper-only via PaperBroker
7. **Test coverage** — Test files exist under `tests/` but are mostly stubs. Critical paths (signal generation, P&L calculation, risk engine) need unit tests
8. **`core/api.py` refactor** — Single 9200-line monolith. Strategy endpoints, market data, risk, paper trading, backtesting all in one file. Should be split into routers/modules

---

## Known Bugs

| Bug | Severity | Details |
|-----|----------|---------|
| GitHub push 403 | Low | Token expiration causes `HTTP 403` on push. Misleading "Everything up-to-date" message. Fix: re-authenticate with a fresh PAT |
| SSL errors on corporate network | Low | yfinance/NSE API calls fail due to SSL certificate inspection. Workaround: use VPN or mobile hotspot |
| `ai_signal_engine.build_deploy_payload` hardcodes NIFTY | Medium | Ignores the global underlying selector. All AI-deployed strategies target NIFTY regardless of selection |
| IV_RANK returns None initially | Low | Until IV history accumulates from chain polling. AI scorer treats None as neutral 50 — by design |
| Unicode Rs symbol in Python logs | Low | `₹` causes cp1252 encoding errors in Windows terminal. Avoid in `print()` statements |
| Fyers token daily expiry | Expected | Access tokens expire every day. Must reconnect via Settings page or restart with fresh token |

---

## How to Build / Run / Test

### Prerequisites
- Python 3.12+
- Node.js 18+
- TA-Lib C library (optional — numpy fallback works)
- Fyers API credentials (for live data; mock data serves as fallback)

### Backend

```bash
cd algo-trading-platform
pip install -r requirements.txt
uvicorn core.api:app --host 0.0.0.0 --port 8080
```

The backend creates `data/platform_state.db` (SQLite) on first run.

### Frontend

```bash
cd algo-trading-platform/dashboard
npm install
npm run dev
```

Vite dev server runs on port 5173. HMR is active for instant feedback.

### Production Build

```bash
cd algo-trading-platform/dashboard
npm run build    # outputs to dashboard/dist/
npm run preview  # preview production build
```

### Running Tests

```bash
cd algo-trading-platform
pytest tests/ -v
```

Note: most tests are stubs. No CI pipeline enforces passing tests currently.

### Docker (not actively used)

```bash
docker-compose up
```

`docker-compose.yml` and `docker-compose.dev.yml` exist but are not the primary dev workflow.

---

## Environment Variables

Copy `.env.example` to `.env`. The platform currently uses only Fyers credentials — other broker vars are scaffolded for future use.

### Required for live data
| Variable | Purpose |
|----------|---------|
| `FYERS_APP_ID` | Fyers API app ID (from Fyers developer portal) |
| `FYERS_SECRET_KEY` | Fyers API secret |
| `FYERS_ACCESS_TOKEN` | Fyers access token (expires daily, can be refreshed via OAuth in Settings page) |

### Scaffolded (not actively used)
| Variable | Purpose |
|----------|---------|
| `ZERODHA_API_KEY`, `ZERODHA_API_SECRET`, `ZERODHA_ACCESS_TOKEN`, `ZERODHA_TOTP_SECRET` | Zerodha Kite Connect |
| `ANGELONE_API_KEY`, `ANGELONE_API_SECRET`, `ANGELONE_ACCESS_TOKEN`, `ANGELONE_TOTP_SECRET` | Angel One SmartAPI |
| `SHOONYA_API_KEY`, `SHOONYA_API_SECRET` | Shoonya / Finvasia |
| `DHAN_API_KEY`, `DHAN_API_SECRET` | Dhan HQ |
| `TIMESCALEDB_HOST`, `TIMESCALEDB_USER`, `TIMESCALEDB_PASSWORD` | TimescaleDB (not used, SQLite is primary) |
| `REDIS_HOST`, `REDIS_PASSWORD` | Redis cache (not used) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alerts |
| `DISCORD_WEBHOOK_URL` | Discord alerts |
| `DASHBOARD_SECRET` | JWT signing secret for dashboard auth |

---

## Key Design Decisions

### 1. Single-file API monolith (`core/api.py` — 9200 lines)
All endpoints live in one file. This was expedient for rapid iteration but is the #1 technical debt item. Splitting into FastAPI routers would improve maintainability. The file contains: strategy deploy/management, Fly-High + Scalper endpoints, market data proxying, risk engine endpoints, paper trading, backtesting, P&L, monitoring, and auto-deploy background tasks.

### 2. In-memory strategy state with SQLite persistence
`_deployed_strategies` dict lives in-memory in `api.py` for sub-millisecond access during the executor's 5s tick loop. SQLite persists on mutations only (not on reads). This avoids blocking the async event loop with synchronous I/O but means a crash loses at most one tick cycle of state.

### 3. Paper-first, no live broker execution
All order execution goes through `PaperBroker`. Broker gateway adapters (Zerodha, Angel, Dhan, Shoonya) are scaffolded but intentionally not wired in. The philosophy is: validate the signal quality in paper mode before risking real capital.

### 4. Fyers as sole data provider
The platform is built around Fyers API v3 for all live data (quotes, option chains, candles, historical). This creates a single point of failure but simplifies the data pipeline. Rate limit budget: ~200 req/min total, managed via aggressive caching (chain 10s, indices 5s, candles 30s, signals 15s).

### 5. Local Greeks computation
Fyers does not provide Greeks. All Greeks (Delta, Gamma, Theta, Vega, Rho) are computed locally via Black-Scholes. IV is back-solved per strike using Newton-Raphson from option market prices. Falls back to India VIX when per-strike IV is unavailable.

### 6. Fly-High runs server-side once armed (no deploy-timing gap)
VWAP crossover detection is point-in-time. The cross only appears on the last closed candle for one period (~5 min). Because the deploy decision now lives in the 25s backend cycle (`run_flyhigh_auto_cycle`), a signal can no longer be missed while waiting for a click. That is why the old 330s active-signal cache and the late-entry path were removed. Each `signal_candle_ts` is entered at most once.

### 7. Charges model uses Indian F&O actuals
Brokerage Rs 20/order, STT 0.0625% sell-side, Exchange 0.053%, GST 18% on (brokerage + exchange), SEBI 0.0001%, Stamp 0.003% buy-side, Slippage 0.1% (reduced from 0.5% — was unrealistic for liquid NIFTY ATM options).

### 8. Three-theme UI system
Classic (default dark/light), Pro (Bloomberg-like fintech), Sci-Fi (neon cyberpunk). Themes are CSS-scoped (`html.pro`, `html.scifi`), persisted to localStorage, and both Pro and Sci-Fi force dark mode. The user cares about professional fintech aesthetics.

### 9. Daily P&L reset
Both backend and frontend filter strategies to today only (IST date comparison). No historical P&L accumulation in the dashboard views — historical data lives in SQLite and the P&L Analytics page.

### 10. Kill switch persisted in SQLite
Survives server restarts. When activated: blocks all deploys, squares off all running positions, logs to audit trail. Reset requires explicit `DELETE /api/risk/kill-switch` call.
