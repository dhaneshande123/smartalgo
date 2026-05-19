# CLAUDE.md

## Project Overview

Institutional-grade algorithmic options trading platform for the Indian stock market (NSE/BSE). Python 3.12+ backend with a React (Vite) dashboard. Currently in active development — the API serves mock data via `MockDataGenerator` while core modules are being built out.

## Environment

- Platform: Windows 11 with Git Bash (use Unix shell syntax)
- Python: 3.12+ (required)
- Node: required for the dashboard (Vite + React + Tailwind)
- Known issue: SSL certificate errors on this machine block broker API and yfinance calls (corporate network). Use VPN or mobile hotspot for live data.

## Quick Start

```bash
# Backend
cd algo-trading-platform
pip install -r requirements.txt
uvicorn core.api:app --reload --host 0.0.0.0 --port 8080

# Dashboard
cd dashboard
npm install
npm run dev
```

Copy `.env.example` to `.env` and fill in broker API keys before running in live/paper mode.

## Project Structure

```
algo-trading-platform/
├── core/                   # Platform core
│   ├── api.py              # FastAPI REST + WebSocket server
│   ├── config.py           # YAML config loader (singleton)
│   ├── constants.py        # Enums and constants
│   ├── models.py           # Pydantic domain models (Instrument, Order, etc.)
│   ├── logging.py          # Structured logging setup
│   ├── auth/               # JWT auth + middleware
│   ├── backtest/           # Backtest engine, data loader, performance
│   ├── broker_gateway/     # Broker adapters (Zerodha, AngelOne, Shoonya, Dhan)
│   ├── cache/              # Redis + in-memory caching
│   ├── database/           # TimescaleDB models, migrations, repos
│   ├── event_bus/          # In-memory / Redis event bus
│   ├── greeks_engine/      # Black-Scholes pricing, IV solver, payoff
│   ├── market_data/        # Tick normalizer, candle builder, option chain
│   ├── monitoring/         # Prometheus metrics, health checks, alerts
│   ├── notifications/      # Telegram + Discord notifiers
│   ├── oms/                # Order management (bracket orders, audit trail)
│   ├── pnl_engine/         # P&L calculator, trade book
│   ├── risk_engine/        # Risk manager, circuit breaker, margin, drawdown
│   ├── strategy_engine/    # Strategy runner, scheduler, parameter store
│   └── websocket/          # WebSocket routes + connection manager
├── strategies/
│   ├── base_strategy.py    # Abstract base class all strategies inherit
│   └── examples/           # Iron condor, straddle seller, ORB, VWAP, etc.
├── dashboard/              # React + Vite + Tailwind frontend
├── config/platform.yaml    # Master config (broker, risk, strategies, alerts)
├── data/                   # Data feeds, storage, replay engine
├── analytics/              # Greeks P&L, performance, reports
├── backtest/               # Backtest data loader, engine, slippage models
├── tests/                  # pytest (unit / integration / stress)
├── infra/                  # Docker, nginx, Prometheus configs
├── docs/                   # Documentation
├── Dockerfile              # Multi-stage Python 3.12-slim
├── docker-compose.yml      # Full stack (app, TimescaleDB, Redis, Prometheus, Grafana)
├── pyproject.toml          # Project metadata + tool config
└── requirements.txt        # Python dependencies
```

## Key Architecture Decisions

- **Broker gateway pattern**: All brokers implement a common base class (`core/broker_gateway/base.py`). Primary/backup broker configured in `config/platform.yaml`.
- **Strategy lifecycle**: Strategies extend `strategies/base_strategy.py` and receive a `StrategyContext` for order placement, position queries, and greeks — no direct coupling to platform internals.
- **Config singleton**: `core/config.py` loads `config/platform.yaml` once. Environment variables are interpolated with `${VAR:-default}` syntax. Reset the singleton in tests via `cfg._config = None`.
- **Event-driven**: An in-memory (or Redis-backed) event bus in `core/event_bus/` decouples modules.
- **Platform modes**: `live`, `paper`, `backtest` — controlled via `platform.mode` in the YAML config.

## Supported Brokers

Zerodha (Kite Connect), AngelOne (SmartAPI), Shoonya/Finvasia, Dhan HQ. API keys go in `.env`.

## Coding Standards

- Formatter: **Black** (line-length 100, target py312)
- Linter: **Ruff** (line-length 100, target py312)
- Type checker: **mypy** (strict mode)
- All async code uses `asyncio` / `aiohttp` / `websockets`

## Testing

```bash
pytest                          # run all tests
pytest -m unit                  # unit tests only
pytest -m integration           # integration tests only
pytest -m stress                # stress tests
pytest -v --tb=short            # verbose with short tracebacks (default)
```

Tests live in `tests/` with `conftest.py` providing shared fixtures (instruments, config). The config singleton is auto-reset between tests.

## Running with Docker

```bash
docker-compose up -d            # full stack
docker-compose -f docker-compose.dev.yml up  # dev mode
```

Stack includes: app, TimescaleDB, Redis, Prometheus, Grafana, nginx.

## Important Files

- `config/platform.yaml` — all runtime config (risk limits, strategy params, broker selection, NSE market hours)
- `.env` / `.env.example` — broker API keys and database credentials (never commit `.env`)
- `core/api.py` — main FastAPI app entry point
- `strategies/base_strategy.py` — strategy interface contract

## NSE Market Context

- Market hours: 09:15–15:30 IST, pre-open 09:00–09:08
- Auto square-off at 15:15 (broker buffer)
- Weekly expiries: NIFTY (Thu), BANKNIFTY (Wed), FINNIFTY (Tue)
- Option/future tick size: 0.05
- MWPL ban threshold: 95%
