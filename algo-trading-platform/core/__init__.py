"""
Algo Trading Platform — Core Engine

Institutional-grade algorithmic options trading platform for Indian markets (NSE/BSE).

Modules
-------
- **models**           — Pydantic v2 data models (Order, Trade, Position, Instrument, etc.)
- **config**           — Platform configuration with YAML/env support
- **constants**        — IST timezone, lot sizes, strike steps, market hours
- **event_bus**        — Async pub/sub event bus with dead-letter queue
- **broker_gateway**   — Multi-broker abstraction (Zerodha, Angel One, Shoonya, Dhan)
- **market_data**      — Real-time and historical market data feeds
- **greeks_engine**    — Black-Scholes, Binomial, Monte Carlo pricing; IV solver; IV surface; payoff
- **oms**              — Order management, smart routing, slicing, bracket/cover orders
- **risk_engine**      — Position tracking, risk limits, margin calc, drawdown, circuit breakers
- **strategy_engine**  — Strategy lifecycle, scheduling, parameter management
- **pnl_engine**       — P&L calculation, trade book, transaction charges
- **backtest**         — Event-driven backtesting with simulated broker and performance analytics
- **monitoring**       — Health checking, alerting, metrics collection
- **api**              — FastAPI REST API with 50+ endpoints
"""

__version__ = "1.0.0"
