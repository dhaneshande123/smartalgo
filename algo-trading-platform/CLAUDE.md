# CLAUDE.md — SmartAlgo Trading Platform

## Project Overview

Institutional-grade algorithmic options trading platform for Indian markets (NSE/BSE).
**Python 3.12+ / FastAPI backend** with a **React (Vite + Tailwind) dashboard**.
Live market data via **Fyers API v3** (WebSocket + REST). Paper trading is fully functional
with real-time P&L, charges, and slippage. Mock data generator has been retired.

**GitHub**: https://github.com/dhaneshande123/smartalgo.git
**Branch**: `main`

## Environment

- Platform: Windows 11 with Git Bash (use Unix shell syntax in all commands)
- Python: 3.12+ (required)
- Node: 18+ for the dashboard (Vite + React + Tailwind)
- Known issue: SSL certificate errors on this machine block broker API and yfinance calls (corporate network). Use VPN or mobile hotspot for live data.

## Quick Start

```bash
# Backend (port 8080)
cd algo-trading-platform
pip install -r requirements.txt
uvicorn core.api:app --host 0.0.0.0 --port 8080

# Dashboard (port 5173)
cd dashboard
npm install
npm run dev
```

Copy `.env.example` to `.env` and fill in Fyers API credentials (`FYERS_APP_ID`, `FYERS_SECRET_KEY`, `FYERS_ACCESS_TOKEN`).

## Architecture — How It All Fits Together

### Data Flow: Strategy Deploy → Entry → P&L

```
AI Signal Engine / Strategy Builder / Manual
        ↓ POST /api/strategies/deploy
  _deployed_strategies dict (in-memory)
        ↓ saved to SQLite (state_store)
  DashboardExecutor (background task, runs every ~2s)
        ↓ evaluates entry_conditions against live indicators
        ↓ when conditions met → places entry orders via PaperBroker
  _refresh_strategy_pnl() (runs every ~1s)
        ↓ reads LTP from _fyers_chain_cache
        ↓ computes gross P&L, deducts charges/slippage
        ↓ updates strat["pnl"], strat["positions"][*]["ltp"]
  Frontend polls /api/deployed-strategies every 1s
        ↓ DeployedStrategiesPnL component renders live cards
```

### Two Strategy Systems (Important!)

1. **`_deployed_strategies` dict** — Global in-memory dict in `core/api.py` (~line 1944).
   Stores AI-deployed, Strategy-Builder, and manual strategies. Persisted to SQLite.
   Endpoint: `GET /api/deployed-strategies`

2. **`PaperTradingManager._active_strategies`** — Separate dict for session-scoped paper
   strategies deployed via the Paper Trading page's "Deploy Strategy" dropdown.
   Endpoint: `GET /api/paper-trading/strategies` (now merges both sources)

### Option Chain Cache

`_fyers_chain_cache` (dict) stores the latest Fyers option chain data per underlying.
Used by: P&L refresh, position LTP updates, strike snapping.
Auto-fetched when strategies deploy (`_auto_fetch_chain_for_strategies` background task).

## Key Custom Modules (built in this project)

| Module | File | Purpose |
|--------|------|---------|
| **Fyers Live Feed** | `core/fyers_live_feed.py` | WebSocket + REST for live quotes, option chains, candles |
| **AI Signal Engine** | `core/ai_signal_engine.py` | Generates strategy recommendations from live market data |
| **Strategy Fit Matrix** | `core/strategy_fit.py` | Maps regimes/IV/ADX to optimal strategy types |
| **Market Regime** | `core/market_regime.py` | Classifies market as TRENDING_UP/DOWN, RANGING, HIGH_VOL, etc. |
| **Indicators Engine** | `core/indicators.py` | RSI, MACD, Bollinger, Supertrend, ADX, VWAP, ATR |
| **Condition Evaluator** | `core/condition_evaluator.py` | Evaluates entry conditions against live indicator values |
| **Dashboard Executor** | `core/strategy_engine/dashboard_executor.py` | Background task that monitors and executes deployed strategies |
| **Charges Calculator** | `core/charges.py` | Indian F&O charges: brokerage, STT, exchange fees, GST, SEBI, stamp duty, slippage |
| **Risk Engine** | `core/risk_engine/calcs.py` | Black-Scholes Greeks, parametric VaR, Taylor stress test, NSE margin approximation, limit checker, drawdown |
| **State Store** | `core/state_store.py` | SQLite persistence for strategies, trades, P&L snapshots, settings (WAL mode) |
| **Symbol Master** | `core/symbol_master.py` | Dynamic lot sizes from Fyers symbol master |
| **Paper Broker** | `core/paper_trading/paper_broker.py` | Simulated broker with slippage, commission, order matching |
| **Paper Trading Mgr** | `core/paper_trading/paper_trading_manager.py` | Session lifecycle, price feeds, strategy deployment |

## Key API Endpoints

### Market Data
- `GET /api/market-data/quotes/{symbol}` — Live quotes from Fyers
- `GET /api/option-chain/{symbol}` — Full option chain with Greeks
- `GET /api/market-data/candles/{symbol}` — OHLCV candles

### Strategy Lifecycle
- `POST /api/strategies/deploy` — Deploy a strategy (paper/live)
- `GET /api/deployed-strategies` — List all deployed strategies with live P&L (polled every 1s)
- `POST /api/strategies/{id}/stop` — Stop a deployed strategy
- `DELETE /api/deployed-strategies/clear-history` — Remove EXITED/STOPPED strategies
- `POST /api/auto-deploy/execute` — AI auto-deploy (with duplicate prevention)

### Paper Trading
- `POST /api/paper-trading/start` — Start paper session
- `POST /api/paper-trading/order` — Place a paper trade (from option chain)
- `GET /api/paper-trading/positions` — Normalized positions with live LTP, side, P&L
- `GET /api/paper-trading/orders` — Order history with normalized timestamps
- `GET /api/paper-trading/strategies` — Merged paper + AI-deployed strategies

### Intelligence
- `GET /api/market-regime` — Current regime classification
- `GET /api/strategy-signals` — Live indicator values + strategy signals
- `GET /api/auto-deploy/recommendations` — AI strategy recommendations
- `GET /api/indicators/{symbol}` — Raw indicator values

### Risk Engine (real, no mocks)
- `GET /api/risk/metrics` — Live VaR, drawdown, margin, Greeks, kill-switch status
- `GET /api/risk/stress-test` — Taylor-expansion stress test across 10 scenarios
- `GET /api/risk/limits` — Configured risk limits (defaults + SQLite overrides)
- `POST /api/risk/limits` — Update + persist risk limits
- `GET /api/risk/drawdown` — Drawdown from persisted equity snapshots
- `GET /api/risk/circuit-breakers` — Live breaker states (daily_loss, drawdown, Greeks, margin, open_strategies)
- `POST /api/risk/kill-switch` — Engage kill switch + square off all open strategies
- `DELETE /api/risk/kill-switch` — Reset kill switch
- `GET /api/risk/greeks-aggregation` — Real Black-Scholes Greeks per strategy + portfolio
- `GET /api/risk/margin-calculator` — NSE F&O margin approximation (SPAN + Exposure)
- `GET /api/risk/breaches` — Live limit breaches (WARN at 80%, BREACH at 100%)
- `GET /api/risk/audit-log` — Full audit trail of risk events

### Analytics
- `GET /api/trade-analytics` — Net/gross P&L, Sharpe, win rate, drawdown, equity curve
- `GET /api/trade-log` — All trade entries/exits from SQLite
- `GET /api/pnl/history` — Historical P&L snapshots

### Trading Mode
- `GET /api/trading/mode` — Current mode (paper/live)
- `POST /api/trading/mode` — Switch mode (requires confirm=true for live)

## Frontend Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Overview: capital, P&L, positions, live strategy P&L cards |
| Market Data | `/market-data` | Option chain with paper trade buttons, Greeks display |
| Charts | `/charts` | TradingView-style charts with indicator overlays |
| Portfolio | `/portfolio` | Unified position view (paper + deployed strategy legs) |
| Strategies | `/strategies` | Strategy catalog with regime-fit scoring |
| Builder | `/builder` | Visual strategy builder with entry conditions |
| Risk | `/risk` | Risk metrics, margin, exposure |
| Orders | `/orders` | Order book and history |
| Backtest | `/backtest` | Strategy backtesting interface |
| P&L Analytics | `/pnl-analytics` | P&L breakdown charts |
| IV Surface | `/iv-surface` | Implied volatility surface visualization |
| Paper Trading | `/paper-trading` | Paper session management, positions with Exit button, deployed strategies |
| AI Signals | `/ai-signals` | Live AI recommendations, one-click deploy, regime display |
| Trade Analytics | `/trade-analytics` | Detailed analytics: equity curve, daily P&L, charges breakdown |
| Monitoring | `/monitoring` | System health, WebSocket stats |
| Settings | `/settings` | Fyers API credentials management |

## Important Patterns & Gotchas

### P&L Refresh for Exited Strategies
When server restarts, option chain cache is empty. `_refresh_strategy_pnl()` has a guard
at the top: if `status` is EXITED or STOPPED, skip recalculation and preserve `realized_pnl`.
Without this, LTP falls back to entry_price, making gross~0, then charges push P&L negative.

### Charges Model (Indian F&O)
- Brokerage: Rs 20/order (flat)
- STT: 0.0625% on SELL side premium
- Exchange transaction fee: 0.053%
- GST: 18% on (brokerage + exchange fee)
- SEBI fee: 0.0001%
- Stamp duty: 0.003% on BUY side
- Slippage: 0.5% of premium (DEFAULT_SLIPPAGE_PCT)

### Duplicate Prevention
`execute_auto_deploy` checks `_deployed_strategies` for RUNNING strategies with the same
`strategy_class` before deploying. Prevents duplicates from repeated clicks.

### Field Name Normalization
PaperBroker returns Decimal strings with different field names (`average_price`, `pnl_unrealized`).
The `/api/paper-trading/positions` endpoint normalizes to frontend-expected names
(`avg_price`, `pnl`, `side`, `strategy`) with float types.

### Position Signed Quantity
`Position.quantity` is signed: +ve = LONG/BUY, -ve = SHORT/SELL.
Frontend expects unsigned `quantity` + separate `side` field. Normalization in API layer.

### PowerShell Gotchas
- `$pid` is read-only in PowerShell — use `$p` instead
- Unicode (Rs symbol) causes cp1252 encoding errors in Python print — avoid in logs
- Use `Invoke-WebRequest` instead of `curl` in PowerShell for API testing

## NSE Market Context

- Market hours: 09:15-15:30 IST, pre-open 09:00-09:08
- Auto square-off at 15:15 (broker buffer)
- Weekly expiries: NIFTY (Thu), BANKNIFTY (Wed), FINNIFTY (Tue)
- Lot sizes: NIFTY=65, BANKNIFTY=30 (dynamic from Fyers symbol master)
- Option/future tick size: 0.05
- Strike steps: NIFTY=50, BANKNIFTY=100

## Database

- **SQLite** (`data/platform_state.db`) — WAL mode, thread-safe
  - Tables: `deployed_strategies`, `pnl_snapshots`, `trade_log`, `settings`, `equity_snapshots`, `risk_events`
  - Module: `core/state_store.py`, singleton via `get_store()`
  - Loaded at startup, saved on deploy/stop/shutdown
  - Risk limits and kill-switch state stored in `settings` table (keys: `risk_limits`, `kill_switch_active`, `kill_switch_meta`)

## Risk Engine Architecture

### Conservative Auto-Kill Policy
- WARN alert at 80% of any limit (debounced 60s per limit)
- Auto-engage kill switch at 100% of `max_daily_loss` OR `max_drawdown_pct` only
- Other limits (Greeks, position size, concentration) only WARN, never auto-kill

### Risk Flow
```
DashboardExecutor tick (every 5s)
  → aggregate_portfolio_greeks (Black-Scholes per leg)
  → calculate_drawdown (from equity_snapshots)
  → check_risk_limits → list of LimitBreach
  → fire WebSocket alerts (debounced 60s)
  → if should_auto_kill → engage kill switch + square off all
  → save_equity_snapshot every 60s
```

### Kill Switch Persistence
- Stored in `settings.kill_switch_active` (survives restart)
- Blocks `POST /api/strategies/deploy` and `POST /api/auto-deploy/execute`
- Reset via `DELETE /api/risk/kill-switch`

### Greeks Computation
- Fyers does NOT provide Greeks in option chain — computed locally via Black-Scholes
- IV per strike back-solved via Newton-Raphson from option market price
- Falls back to India VIX (chain-wide) if per-strike IV unavailable
- All Greeks signed by position side: BUY = +qty, SELL = -qty

## Git Workflow

- All changes on `main` branch
- Commit messages: descriptive, multi-line for complex changes
- `.gitignore` excludes: `.env`, `data/platform_state.db*`, `node_modules/`, `dist/`

## What's Been Built (completed)

1. Fyers API v3 integration (live quotes, option chains, candles, WebSocket)
2. Live/Paper mode toggle with safety gates
3. Dynamic lot sizes from Fyers symbol master
4. Strategy Builder with visual leg configuration
5. Entry conditions builder (indicator-based triggers)
6. Indicators engine (RSI, MACD, BB, Supertrend, ADX, VWAP, ATR)
7. Market regime classifier (TRENDING_UP/DOWN, RANGING, HIGH_VOL, etc.)
8. AI signal engine with strategy-fit matrix
9. Auto-deploy with duplicate prevention
10. Dashboard executor (background strategy monitoring + entry/exit)
11. Paper trading with PaperBroker (slippage, commission, order matching)
12. Option chain paper trading with Exit button
13. Realistic charges model (all Indian F&O regulatory charges)
14. SQLite state persistence (survives restarts)
15. Trade Analytics dashboard (equity curve, daily P&L, Sharpe, charges breakdown)
16. WebSocket alerts system
17. DeployedStrategiesPnL live cards (Dashboard + Paper Trading)

## Potential Next Steps

- Backtesting integration with historical Fyers data
- Telegram/Discord notifications for trade alerts
- Multi-underlying support (BANKNIFTY, FINNIFTY)
- Strategy performance comparison dashboard
- Live mode with real Fyers order placement
- Greeks-based position management (delta hedging)
- Advanced risk metrics (VaR, expected shortfall)
