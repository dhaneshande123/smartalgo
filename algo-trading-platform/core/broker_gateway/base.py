"""
Broker Gateway — Unified abstract interface for all broker integrations.

All broker implementations (Zerodha, Angel One, Shoonya, Dhan) inherit from
BaseBroker and implement its methods. Strategies interact exclusively through
this interface, making them broker-agnostic.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from core.models import (
    Instrument,
    MarginInfo,
    OptionChain,
    Order,
    OrderResponse,
    Position,
    Trade,
)


class BaseBroker(ABC):
    """Abstract base class for all broker integrations.

    Implementations must handle: authentication, token refresh, session
    management, rate limiting, WebSocket reconnection with exponential
    backoff, and order status reconciliation.
    """

    name: str = "base"

    # ── Connection Lifecycle ──────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the broker API. Handle authentication."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close all connections and clean up resources."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if the broker session is active and valid."""
        ...

    # ── Order Management ──────────────────────────────────────────────

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResponse:
        """Submit an order to the exchange via this broker."""
        ...

    @abstractmethod
    async def modify_order(self, order_id: str, modifications: dict[str, Any]) -> OrderResponse:
        """Modify an existing open order (price, quantity, trigger, etc.)."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an open order."""
        ...

    @abstractmethod
    async def cancel_all_orders(self) -> list[OrderResponse]:
        """Cancel all open orders. Used by kill switch."""
        ...

    # ── Position & Account ────────────────────────────────────────────

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Fetch all current positions from the broker."""
        ...

    @abstractmethod
    async def get_order_book(self) -> list[Order]:
        """Fetch today's complete order book from the broker."""
        ...

    @abstractmethod
    async def get_trade_book(self) -> list[Trade]:
        """Fetch today's executed trades from the broker."""
        ...

    @abstractmethod
    async def get_margins(self) -> MarginInfo:
        """Fetch current margin/funds information."""
        ...

    # ── Market Data ───────────────────────────────────────────────────

    @abstractmethod
    async def get_ltp(self, instruments: list[str]) -> dict[str, float]:
        """Get last traded price for a list of instrument symbols/tokens.

        Args:
            instruments: List of instrument identifiers (broker-specific tokens or symbols).

        Returns:
            Mapping of instrument identifier to LTP.
        """
        ...

    @abstractmethod
    async def get_option_chain(self, symbol: str, expiry: date) -> OptionChain:
        """Fetch the full option chain for a symbol and expiry.

        Args:
            symbol: Underlying symbol (e.g., "NIFTY", "BANKNIFTY").
            expiry: Expiry date.
        """
        ...

    @abstractmethod
    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]:
        """Fetch the master instrument list from the broker.

        Args:
            exchange: Optional exchange filter (e.g., "NFO", "NSE").
        """
        ...

    # ── WebSocket / Streaming ─────────────────────────────────────────

    @abstractmethod
    async def subscribe_ticks(self, instruments: list[str], callback: Any) -> None:
        """Subscribe to real-time tick data via WebSocket.

        Args:
            instruments: List of instrument tokens to subscribe.
            callback: Async callable invoked with each tick.
        """
        ...

    @abstractmethod
    async def unsubscribe_ticks(self, instruments: list[str]) -> None:
        """Unsubscribe from tick data for given instruments."""
        ...

    @abstractmethod
    async def subscribe_order_updates(self, callback: Any) -> None:
        """Subscribe to real-time order status updates.

        Args:
            callback: Async callable invoked on each order update.
        """
        ...

    # ── Utilities ─────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<Broker: {self.name}>"
