"""
Base Strategy — Abstract class that all trading strategies must inherit from.

Provides the full strategy lifecycle, market event handlers, and a StrategyContext
for interacting with the platform (placing orders, reading positions, greeks, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ScheduledEvent:
    """Represents a time-based trigger for strategy logic."""
    name: str
    scheduled_time: datetime
    data: dict[str, Any] = field(default_factory=dict)


class StrategyContext:
    """
    Provides strategies with access to platform services.

    Injected into every strategy on initialization. Strategies use this
    to place orders, query positions, fetch greeks/IV, etc. without
    coupling to internal platform internals.

    The actual implementation is provided by the StrategyEngine at runtime.
    This class defines the interface contract.
    """

    # -- Order Management --
    async def place_order(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def modify_order(self, order_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> Any:
        raise NotImplementedError

    async def cancel_all_orders(self) -> None:
        raise NotImplementedError

    async def square_off_all(self) -> None:
        raise NotImplementedError

    # -- Position & Portfolio --
    async def get_positions(self) -> list[Any]:
        raise NotImplementedError

    async def get_portfolio_greeks(self) -> Any:
        raise NotImplementedError

    async def get_margin_info(self) -> Any:
        raise NotImplementedError

    async def get_pnl(self) -> Any:
        raise NotImplementedError

    # -- Market Data --
    async def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        raise NotImplementedError

    async def get_option_chain(self, symbol: str, expiry: Any = None) -> Any:
        raise NotImplementedError

    async def get_greeks(self, symbol: str) -> Any:
        raise NotImplementedError

    async def get_iv(self, symbol: str) -> float:
        raise NotImplementedError

    async def get_iv_percentile(self, symbol: str, window: int = 252) -> float:
        raise NotImplementedError

    async def get_candles(
        self, symbol: str, timeframe: str, count: int = 100
    ) -> list[Any]:
        raise NotImplementedError

    async def get_underlying_price(self, symbol: str) -> float:
        raise NotImplementedError

    # -- Scheduling --
    async def schedule(self, name: str, time_str: str, data: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    async def cancel_schedule(self, name: str) -> None:
        raise NotImplementedError

    # -- Alerts & Logging --
    async def alert(self, message: str, level: str = "INFO") -> None:
        raise NotImplementedError

    @property
    def log(self) -> Any:
        raise NotImplementedError

    # -- Strategy Metadata --
    @property
    def strategy_id(self) -> str:
        raise NotImplementedError

    @property
    def strategy_name(self) -> str:
        raise NotImplementedError

    @property
    def params(self) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def mode(self) -> str:
        raise NotImplementedError


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Subclass this and implement the lifecycle and event handler methods.
    The platform calls these methods at the appropriate times.

    Attributes:
        ctx: StrategyContext injected by the platform on init.
    """

    ctx: StrategyContext

    # ── Lifecycle ──────────────────────────────────────────────────────

    @abstractmethod
    async def on_init(self, context: StrategyContext) -> None:
        """Called once when the strategy is loaded. Store context, set up state."""
        ...

    async def on_start(self) -> None:
        """Called when the strategy transitions to RUNNING state."""
        pass

    async def on_stop(self) -> None:
        """Called when the strategy is stopped (graceful shutdown)."""
        pass

    async def on_pause(self) -> None:
        """Called when the strategy is paused."""
        pass

    async def on_resume(self) -> None:
        """Called when the strategy resumes from paused state."""
        pass

    async def on_error(self, error: Exception) -> None:
        """Called when an unhandled exception occurs in the strategy."""
        pass

    # ── Market Events ─────────────────────────────────────────────────

    async def on_tick(self, tick: Any) -> None:
        """Called on every market tick for subscribed instruments."""
        pass

    async def on_candle(self, candle: Any) -> None:
        """Called when a new candle is completed (1m, 5m, etc.)."""
        pass

    async def on_option_chain_update(self, chain: Any) -> None:
        """Called when the option chain data is refreshed."""
        pass

    # ── Order & Trade Events ──────────────────────────────────────────

    async def on_order_update(self, order: Any) -> None:
        """Called when an order status changes (placed, filled, rejected, etc.)."""
        pass

    async def on_trade(self, trade: Any) -> None:
        """Called when a trade is executed (fill)."""
        pass

    async def on_position_update(self, position: Any) -> None:
        """Called when a position changes (new, modified, closed)."""
        pass

    # ── Scheduling ────────────────────────────────────────────────────

    async def on_schedule(self, event: ScheduledEvent) -> None:
        """Called at scheduled times (e.g., run at 9:20 AM every day)."""
        pass

    # ── Utility ───────────────────────────────────────────────────────

    def __repr__(self) -> str:
        name = getattr(self, "ctx", None)
        strategy_name = name.strategy_name if name else self.__class__.__name__
        return f"<Strategy: {strategy_name}>"
