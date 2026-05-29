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

# Live calculation module (pure functions, used by the API and executor)
from core.risk_engine.calcs import (
    DEFAULT_RISK_LIMITS,
    DEFAULT_STRESS_SCENARIOS,
    LimitBreach,
    bs_greeks,
    bs_price,
    implied_volatility,
    position_greeks,
    aggregate_portfolio_greeks,
    parametric_var,
    expected_shortfall,
    stress_test_portfolio,
    calculate_margin,
    calculate_drawdown,
    check_risk_limits,
    should_auto_kill,
    position_concentration,
    days_to_expiry,
    time_to_expiry_years,
)

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
    # Live calculation functions (calcs.py)
    "DEFAULT_RISK_LIMITS",
    "DEFAULT_STRESS_SCENARIOS",
    "LimitBreach",
    "bs_greeks",
    "bs_price",
    "implied_volatility",
    "position_greeks",
    "aggregate_portfolio_greeks",
    "parametric_var",
    "expected_shortfall",
    "stress_test_portfolio",
    "calculate_margin",
    "calculate_drawdown",
    "check_risk_limits",
    "should_auto_kill",
    "position_concentration",
    "days_to_expiry",
    "time_to_expiry_years",
]
