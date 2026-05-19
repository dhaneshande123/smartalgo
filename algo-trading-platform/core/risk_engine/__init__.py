"""Risk engine components for the algo trading platform.

Core modules
------------
- :class:`PositionTracker` / :class:`PositionState` — position tracking from trade fills
- :class:`RiskManager` / :class:`RiskLimits` / :class:`RiskCheckResult` — pre/post-trade risk checks
- :class:`MarginCalculator` — SPAN-like margin calculation for NSE F&O

Supporting modules
------------------
- :class:`DrawdownMonitor` / :class:`DrawdownState`
- :class:`CircuitBreaker` and related types
- :class:`GreeksAggregator`
"""

from core.risk_engine.drawdown_monitor import DrawdownMonitor, DrawdownState
from core.risk_engine.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerEvent,
    CircuitBreakerState,
)
from core.risk_engine.greeks_aggregator import GreeksAggregator
from core.risk_engine.margin_calculator import MarginCalculator
from core.risk_engine.position_tracker import PositionState, PositionTracker
from core.risk_engine.risk_manager import RiskCheckResult, RiskLimits, RiskManager

__all__ = [
    # Core risk engine
    "MarginCalculator",
    "PositionState",
    "PositionTracker",
    "RiskCheckResult",
    "RiskLimits",
    "RiskManager",
    # Supporting modules
    "DrawdownMonitor",
    "DrawdownState",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerEvent",
    "CircuitBreakerState",
    "GreeksAggregator",
]
