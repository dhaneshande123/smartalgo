"""
Paper Broker — a simulated broker for paper trading that mirrors the real
broker interface while using the SimulatedBroker matching engine internally.

Connects to the live (or mock) market data feed for price updates and
publishes order/trade events to the event bus, exactly like a real broker.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.backtest.simulated_broker import SimulatedBroker
from core.event_bus.bus import BaseEventBus, EventPriority, Topics
from core.models import (
    Instrument,
    MarginInfo,
    OptionChain,
    Order,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
    Trade,
)

logger = logging.getLogger(__name__)


class PaperBroker:
    """Paper trading broker that implements the same interface as real brokers.

    Uses :class:`SimulatedBroker` internally for order matching and position
    tracking.  Publishes order and trade events to the event bus so that
    downstream consumers (OMS, strategies, dashboard) work identically in
    paper and live modes.

    Args:
        initial_capital: Starting paper trading capital in INR.
        slippage_bps: Simulated slippage in basis points per fill.
        commission: Flat commission per filled order in INR.
        event_bus: Optional event bus for publishing order/trade events.
    """

    name: str = "paper"

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        slippage_bps: float = 2.0,
        commission: float = 20.0,
        event_bus: BaseEventBus | None = None,
    ) -> None:
        self._initial_capital = initial_capital
        self._event_bus = event_bus
        self._connected = False

        # Internal simulated broker for order matching
        self._sim = SimulatedBroker(
            slippage_bps=slippage_bps,
            commission=commission,
        )
        self._sim.set_capital(initial_capital)

        # Price feed task
        self._price_feed_task: asyncio.Task[None] | None = None
        self._price_callbacks: list[Any] = []

        # Session tracking
        self._session_id: str = ""
        self._session_start: datetime | None = None
        self._order_count: int = 0
        self._trade_count: int = 0

    # ── Connection Lifecycle ─────────────────────────────────────────

    async def connect(self) -> None:
        """Mark the paper broker as connected and start processing."""
        self._connected = True
        self._session_id = uuid.uuid4().hex[:12]
        self._session_start = datetime.now(timezone.utc)
        logger.info(
            "PaperBroker connected (session=%s, capital=%.0f)",
            self._session_id,
            self._initial_capital,
        )

    async def disconnect(self) -> None:
        """Disconnect the paper broker and stop any feed tasks."""
        self._connected = False
        if self._price_feed_task is not None:
            self._price_feed_task.cancel()
            try:
                await self._price_feed_task
            except asyncio.CancelledError:
                pass
            self._price_feed_task = None
        logger.info("PaperBroker disconnected (session=%s)", self._session_id)

    async def is_connected(self) -> bool:
        """Check if the paper broker session is active."""
        return self._connected

    # ── Price Feed ───────────────────────────────────────────────────

    def update_price(
        self,
        symbol: str,
        price: float,
        timestamp: datetime | None = None,
    ) -> list[Trade]:
        """Feed a new price into the simulated broker.

        This checks pending limit/SL orders and fills them if conditions are
        met.  Should be called on every incoming tick.

        Args:
            symbol: The instrument symbol.
            price: The latest traded price.
            timestamp: Optional timestamp for the price update.

        Returns:
            List of trades that were filled on this price update.
        """
        ts = timestamp or datetime.now(timezone.utc)
        self._sim.set_timestamp(ts)
        new_trades = self._sim.update_price(symbol, price, ts)

        # Publish trade events for any fills
        for trade in new_trades:
            self._trade_count += 1
            asyncio.create_task(self._publish_trade_event(trade))

        return new_trades

    async def _publish_trade_event(self, trade: Trade) -> None:
        """Publish a trade fill event to the event bus."""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish(
                topic=Topics.TRADES,
                event_type="TRADE_FILL",
                payload={
                    "trade_id": trade.trade_id,
                    "order_id": trade.order_id,
                    "symbol": trade.instrument.symbol,
                    "side": trade.side.value,
                    "quantity": trade.quantity,
                    "price": str(trade.price),
                    "timestamp": trade.timestamp.isoformat(),
                    "source": "paper_broker",
                },
                source="paper_broker",
            )
        except Exception as exc:
            logger.error("Failed to publish trade event: %s", exc)

    async def _publish_order_event(
        self, order: Order, event_type: str = "ORDER_UPDATE"
    ) -> None:
        """Publish an order status event to the event bus."""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish(
                topic=Topics.ORDERS,
                event_type=event_type,
                payload={
                    "order_id": order.order_id,
                    "symbol": order.instrument.symbol,
                    "side": order.side.value,
                    "order_type": order.order_type.value,
                    "quantity": order.quantity,
                    "price": str(order.price) if order.price else None,
                    "status": order.status.value,
                    "filled_quantity": order.filled_quantity,
                    "average_price": str(order.average_price),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "paper_broker",
                },
                source="paper_broker",
            )
        except Exception as exc:
            logger.error("Failed to publish order event: %s", exc)

    # ── Order Management ─────────────────────────────────────────────

    async def place_order(self, order: Order) -> OrderResponse:
        """Place an order in the paper trading engine.

        Validates the order, forwards it to the simulated broker for
        matching, and publishes events.

        Args:
            order: The order to place.

        Returns:
            An OrderResponse indicating success or failure.
        """
        if not self._connected:
            return OrderResponse(
                success=False,
                order_id=order.order_id,
                message="PaperBroker is not connected",
                status=OrderStatus.REJECTED,
            )

        self._order_count += 1
        self._sim.set_timestamp(datetime.now(timezone.utc))

        # Delegate to simulated broker
        response = await self._sim.place_order(order)

        # Publish order event
        updated_order = self._sim.all_orders.get(order.order_id)
        if updated_order:
            await self._publish_order_event(updated_order, "ORDER_PLACED")

        # If it was a market order that filled, also publish trade events
        if response.status == OrderStatus.FILLED:
            trades = [t for t in self._sim.trades if t.order_id == order.order_id]
            for trade in trades[-1:]:  # latest trade for this order
                self._trade_count += 1
                await self._publish_trade_event(trade)

        logger.info(
            "PaperBroker: %s order %s %s %d @ %s -> %s",
            order.order_type.value,
            order.side.value,
            order.instrument.symbol,
            order.quantity,
            order.price or "MKT",
            response.status.value if response.status else "UNKNOWN",
        )

        return response

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel a pending paper order.

        Args:
            order_id: The ID of the order to cancel.

        Returns:
            An OrderResponse indicating success or failure.
        """
        if not self._connected:
            return OrderResponse(
                success=False,
                order_id=order_id,
                message="PaperBroker is not connected",
                status=OrderStatus.ERROR,
            )

        response = await self._sim.cancel_order(order_id)

        if response.success:
            cancelled_order = self._sim.all_orders.get(order_id)
            if cancelled_order:
                await self._publish_order_event(cancelled_order, "ORDER_CANCELLED")

        return response

    async def modify_order(
        self, order_id: str, modifications: dict[str, Any]
    ) -> OrderResponse:
        """Modify a pending order (not supported in paper mode).

        Paper broker does not support order modification. Cancel and replace
        instead.
        """
        return OrderResponse(
            success=False,
            order_id=order_id,
            message="Order modification not supported in paper trading. Cancel and replace.",
            status=OrderStatus.ERROR,
        )

    async def cancel_all_orders(self) -> list[OrderResponse]:
        """Cancel all pending paper orders."""
        responses: list[OrderResponse] = []
        for order_id in list(self._sim.pending_orders.keys()):
            resp = await self.cancel_order(order_id)
            responses.append(resp)
        return responses

    # ── Position & Account ───────────────────────────────────────────

    async def get_positions(self) -> list[Position]:
        """Return all current paper positions."""
        return [
            pos for pos in self._sim.positions.values()
            if pos.quantity != 0
        ]

    async def get_all_positions(self) -> dict[str, Position]:
        """Return all positions (including closed) keyed by symbol."""
        return self._sim.positions

    async def get_order_book(self) -> list[Order]:
        """Return all orders (filled, pending, cancelled)."""
        return list(self._sim.all_orders.values())

    async def get_order_history(self) -> list[Order]:
        """Return complete order history sorted by placement time."""
        orders = list(self._sim.all_orders.values())
        return sorted(orders, key=lambda o: o.placed_at or datetime.min, reverse=True)

    async def get_trades(self) -> list[Trade]:
        """Return all executed trades."""
        return self._sim.trades

    async def get_margins(self) -> MarginInfo:
        """Return current margin / capital status."""
        equity = self._sim.equity
        used = self._initial_capital - self._sim.cash
        available = max(0.0, self._sim.cash)
        utilization = (used / self._initial_capital * 100) if self._initial_capital > 0 else 0

        return MarginInfo(
            available_cash=Decimal(str(round(available, 2))),
            used_margin=Decimal(str(round(max(0, used), 2))),
            available_margin=Decimal(str(round(available, 2))),
            total_collateral=Decimal(str(round(equity, 2))),
            utilization_pct=round(min(utilization, 100.0), 2),
        )

    async def get_option_chain(
        self, symbol: str, expiry: Any = None
    ) -> OptionChain | None:
        """Option chain is not available in paper mode."""
        return None

    # ── P&L Tracking ─────────────────────────────────────────────────

    @property
    def cash(self) -> float:
        """Current cash balance."""
        return self._sim.cash

    @property
    def equity(self) -> float:
        """Total equity (cash + MTM positions)."""
        return self._sim.equity

    @property
    def total_pnl(self) -> float:
        """Total P&L since session start."""
        return self._sim.equity - self._initial_capital

    @property
    def total_commission(self) -> float:
        """Total commissions paid."""
        return self._sim.total_commission

    @property
    def realized_pnl(self) -> float:
        """Sum of realized P&L across all positions."""
        return sum(
            float(pos.pnl_realized)
            for pos in self._sim.positions.values()
        )

    @property
    def unrealized_pnl(self) -> float:
        """Sum of unrealized P&L across all open positions."""
        total = 0.0
        for symbol, pos in self._sim.positions.items():
            if pos.quantity == 0:
                continue
            current_price = self._sim._current_prices.get(symbol, float(pos.ltp))
            avg = float(pos.average_price)
            total += (current_price - avg) * pos.quantity
        return total

    # ── Stats ────────────────────────────────────────────────────────

    def session_summary(self) -> dict[str, Any]:
        """Return a summary of the current paper trading session."""
        trades = self._sim.trades
        winning = sum(1 for t in trades if self._is_winning_trade(t))
        total_trades = len(trades)

        return {
            "session_id": self._session_id,
            "started_at": self._session_start.isoformat() if self._session_start else None,
            "initial_capital": self._initial_capital,
            "current_equity": round(self.equity, 2),
            "cash": round(self.cash, 2),
            "total_pnl": round(self.total_pnl, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "total_commission": round(self.total_commission, 2),
            "total_orders": self._order_count,
            "total_trades": total_trades,
            "winning_trades": winning,
            "losing_trades": total_trades - winning,
            "win_rate": round(winning / total_trades * 100, 2) if total_trades > 0 else 0.0,
            "open_positions": sum(
                1 for p in self._sim.positions.values() if p.quantity != 0
            ),
            "pending_orders": len(self._sim.pending_orders),
        }

    def _is_winning_trade(self, trade: Trade) -> bool:
        """Heuristic: consider a SELL trade winning if price > avg position price."""
        if trade.side == OrderSide.SELL:
            pos = self._sim.positions.get(trade.instrument.symbol)
            if pos and float(pos.average_price) > 0:
                return float(trade.price) > float(pos.average_price)
        return False

    def reset(self, capital: float | None = None) -> None:
        """Reset the paper broker to a clean state.

        Args:
            capital: New starting capital. Uses original capital if None.
        """
        cap = capital if capital is not None else self._initial_capital
        self._sim = SimulatedBroker(
            slippage_bps=self._sim._slippage_bps,
            commission=self._sim._commission,
        )
        self._sim.set_capital(cap)
        self._initial_capital = cap
        self._order_count = 0
        self._trade_count = 0
        self._session_id = uuid.uuid4().hex[:12]
        self._session_start = datetime.now(timezone.utc)
        logger.info("PaperBroker reset (session=%s, capital=%.0f)", self._session_id, cap)
