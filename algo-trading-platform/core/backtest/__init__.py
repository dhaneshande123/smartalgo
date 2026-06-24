"""
Backtest Engine — event-driven + vectorized backtesting for the algo trading platform.

Two engines:
1. Event-driven (BacktestEngine) — bar-by-bar replay with SimulatedBroker
2. Vectorized (VectorBTEngine) — 100x faster parameter sweeps via vectorbt

Plus: Optuna optimizer and QuantStats report generation.
"""

from core.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from core.backtest.performance import PerformanceAnalyzer
from core.backtest.simulated_broker import SimulatedBroker
from core.backtest.vectorbt_engine import VBTBacktestConfig, VBTBacktestResult, VectorBTEngine
from core.backtest.optimizer import OptimizationConfig, OptimizationResult, StrategyOptimizer
from core.backtest.reports import generate_metrics, generate_html_tearsheet, generate_snapshot, compare_strategies

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "PerformanceAnalyzer",
    "SimulatedBroker",
    "VBTBacktestConfig",
    "VBTBacktestResult",
    "VectorBTEngine",
    "OptimizationConfig",
    "OptimizationResult",
    "StrategyOptimizer",
    "generate_metrics",
    "generate_html_tearsheet",
    "generate_snapshot",
    "compare_strategies",
]
