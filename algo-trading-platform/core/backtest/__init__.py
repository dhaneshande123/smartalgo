"""
Backtest Engine — event-driven backtesting for the algo trading platform.

Provides a complete simulation environment that replays historical data
through strategies using a simulated broker with configurable slippage
and commission models, then computes institutional-grade performance
analytics.
"""

from core.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from core.backtest.performance import PerformanceAnalyzer
from core.backtest.simulated_broker import SimulatedBroker

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "PerformanceAnalyzer",
    "SimulatedBroker",
]
