"""
Simulated Broker — instant-fill broker for backtesting.

Supports market, limit, SL, and SL-M orders with configurable slippage
and flat commission.  Pending limit/SL orders are checked on every price
update and filled when the trigger condition is met.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.models import (
    Instrument,
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


class SimulatedBroker:
    """Simulated broker for backtesting.

    Features:
    - Instant market order fills at current price +/- slippage.
    - Limit order fills when price touches the limit.
    - SL order fills when price touches the trigger.
    - Configurable slippage model (basis-point spread).
    - Position tracking with average-price bookkeeping.
    - Order book maintenance for pending orders.

    Args:
        slippage_bps: Slippage in basis points applied to each fill.
        commission: Flat commission charged per filled order (INR).
    """

    def __init__(
        self,
        slippage_bps: float = 1.0,
        commission: float = 20.0,
    ) -> None:
        self._slippage_bps: float = slippage_bps
        self._commission: float = commission

        self._orders: dict[str, Order] = {}
        self._pending_orders: dict[str, Order] = {}
        self._trades: list[Trade] = []
        self._positions: dict[str, Position] = {}
        self._current_prices: dict[str, float] = {}
        self._order_counter: int = 0
        self._cash: float = 0.0
        self._initial_capital: float = 0.0
        self._total_commission: float = 0.0
        self._current_timestamp: datetime = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Capital
    # ------------------------------------------------------------------

    def set_capital(self, capital: float) -> None:
        """Set the initial trading capital."""
        self._cash = capital
        self._initial_capital = capital

    def set_timestamp(self, ts: datetime) -> None:
        """Advance the broker's simulated clock."""
        self._current_timestamp = ts

    # ------------------------------------------------------------------
    # Order API
    # ------------------------------------------------------------------

    async def place_order(self, order: Order) -> OrderResponse:
        """Place an order into the simulated order book.

        Market orders are filled immediately at the current price (with
        slippage).  Limit and SL orders are stored as pending and checked
        on every subsequent ``update_price`` call.

        Args:
            order: The order to place.

        Returns:
            An ``OrderResponse`` indicating success/failure.
        """
        self._order_counter += 1
        order_id = order.order_id
        symbol = order.instrument.symbol

        # Store the order
        order = order.model_copy(
            update={"status": OrderStatus.PLACED, "placed_at": self._current_timestamp}
        )
        self._orders[order_id] = order

        logger.debug(
            "SimBroker: order %s placed — %s %s %d @ %s (%s)",
            order_id[:8],
            order.side.value,
            symbol,
            order.quantity,
            order.price or "MKT",
            order.order_type.value,
        )

        # Market orders fill immediately
        if order.order_type == OrderType.MARKET:
            current_price = self._current_prices.get(symbol)
            if current_price is None:
                order = order.model_copy(
                    update={
                        "status": OrderStatus.REJECTED,
                        "rejection_reason": "No price available",
                    }
                )
                self._orders[order_id] = order
                return OrderResponse(
                    success=False,
                    order_id=order_id,
                    message="No price available for symbol",
                    status=OrderStatus.REJECTED,
                )

            fill_price = self._apply_slippage(current_price, order.side)
            self._execute_fill(order, fill_price)
            return OrderResponse(
                success=True,
                order_id=order_id,
                message="Market order filled",
                status=OrderStatus.FILLED,
            )

        # Limit / SL orders go to the pending book
        self._pending_orders[order_id] = order
        return OrderResponse(
            success=True,
            order_id=order_id,
            message=f"{order.order_type.value} order placed",
            status=OrderStatus.OPEN,
        )

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel a pending order.

        Args:
            order_id: The ID of the order to cancel.

        Returns:
            An ``OrderResponse`` indicating success/failure.
        """
        if order_id in self._pending_orders:
            order = self._pending_orders.pop(order_id)
            order = order.model_copy(
                update={
                    "status": OrderStatus.CANCELLED,
                    "updated_at": self._current_timestamp,
                }
            )
            self._orders[order_id] = order
            logger.debug("SimBroker: order %s cancelled", order_id[:8])
            return OrderResponse(
                success=True,
                order_id=order_id,
                message="Order cancelled",
                status=OrderStatus.CANCELLED,
            )

        return OrderResponse(
            success=False,
            order_id=order_id,
            message="Order not found or already filled",
        )

    # ------------------------------------------------------------------
    # Price feed
    # ------------------------------------------------------------------

    def update_price(
        self, symbol: str, price: float, timestamp: datetime | None = None
    ) -> list[Trade]:
        """Update the latest price for a symbol and check pending orders.

        Called on every incoming tick/candle.  Iterates over all pending
        orders for the symbol, checking fill conditions for limit and
        stop-loss orders.

        Args:
            symbol: The trading symbol.
            price: The latest traded price.
            timestamp: Optional timestamp override.

        Returns:
            A list of trades that were filled on this price update.
        """
        self._current_prices[symbol] = price
        if timestamp is not None:
            self._current_timestamp = timestamp

        new_trades: list[Trade] = []

        # Check all pending orders for this symbol
        filled_ids: list[str] = []
        for order_id, order in list(self._pending_orders.items()):
            if order.instrument.symbol != symbol:
                continue

            trade = self._try_fill_order(order, price)
            if trade is not None:
                filled_ids.append(order_id)
                new_trades.append(trade)

        # Remove filled orders from pending
        for oid in filled_ids:
            self._pending_orders.pop(oid, None)

        return new_trades

    # ------------------------------------------------------------------
    # Fill logic
    # ------------------------------------------------------------------

    def _try_fill_order(self, order: Order, price: float) -> Trade | None:
        """Check if a pending order should fill at the given price.

        Fill conditions:
        - LIMIT BUY: price <= limit_price  (fill at limit)
        - LIMIT SELL: price >= limit_price  (fill at limit)
        - SL BUY (stop-loss): price >= trigger_price
        - SL SELL (stop-loss): price <= trigger_price
        - SL_M: same trigger as SL but fills at market (with slippage)

        Args:
            order: A pending order.
            price: The current market price.

        Returns:
            A ``Trade`` if the order was filled, else ``None``.
        """
        should_fill = False
        fill_price = price

        if order.order_type == OrderType.LIMIT:
            limit_price = float(order.price)  # type: ignore[arg-type]
            if order.side == OrderSide.BUY and price <= limit_price:
                should_fill = True
                fill_price = limit_price
            elif order.side == OrderSide.SELL and price >= limit_price:
                should_fill = True
                fill_price = limit_price

        elif order.order_type in (OrderType.SL, OrderType.SL_M):
            trigger = float(order.trigger_price)  # type: ignore[arg-type]
            if order.side == OrderSide.BUY and price >= trigger:
                should_fill = True
                if order.order_type == OrderType.SL and order.price is not None:
                    fill_price = float(order.price)
                else:
                    fill_price = self._apply_slippage(price, order.side)
            elif order.side == OrderSide.SELL and price <= trigger:
                should_fill = True
                if order.order_type == OrderType.SL and order.price is not None:
                    fill_price = float(order.price)
                else:
                    fill_price = self._apply_slippage(price, order.side)

        if should_fill:
            return self._execute_fill(order, fill_price)

        return None

    def _execute_fill(self, order: Order, fill_price: float) -> Trade:
        """Execute a fill: create a trade, update position, deduct commission.

        Args:
            order: The order being filled.
            fill_price: The execution price.

        Returns:
            The resulting ``Trade``.
        """
        symbol = order.instrument.symbol

        # Create trade record
        trade = Trade(
            trade_id=uuid.uuid4().hex,
            order_id=order.order_id,
            strategy_id=order.strategy_id,
            instrument=order.instrument,
            side=order.side,
            quantity=order.quantity,
            price=Decimal(str(round(fill_price, 2))),
            timestamp=self._current_timestamp,
        )
        self._trades.append(trade)

        # Update order status
        order = order.model_copy(
            update={
                "status": OrderStatus.FILLED,
                "filled_quantity": order.quantity,
                "average_price": Decimal(str(round(fill_price, 2))),
                "updated_at": self._current_timestamp,
            }
        )
        self._orders[order.order_id] = order

        # Update position and cash
        self._update_position(order.instrument, order.side, order.quantity, fill_price)

        # Deduct commission
        self._cash -= self._commission
        self._total_commission += self._commission

        logger.debug(
            "SimBroker: FILLED %s %s %d @ %.2f (commission=%.0f)",
            order.side.value,
            symbol,
            order.quantity,
            fill_price,
            self._commission,
        )

        return trade

    def _update_position(
        self,
        instrument: Instrument,
        side: OrderSide,
        quantity: int,
        fill_price: float,
    ) -> None:
        """Update the internal position for a symbol after a fill.

        Cash accounting: every fill changes cash by ``-signed_qty * fill_price``.
        A BUY spends cash; a SELL receives cash.  Realized P&L is tracked
        on the Position object when positions are reduced or closed.

        Args:
            instrument: The instrument traded.
            side: BUY or SELL.
            quantity: Number of units filled.
            fill_price: The fill price.
        """
        symbol = instrument.symbol
        signed_qty = quantity if side == OrderSide.BUY else -quantity

        # Cash: buy costs money (negative), sell receives money (positive)
        self._cash -= signed_qty * fill_price

        if symbol not in self._positions:
            # New position
            self._positions[symbol] = Position(
                instrument=instrument,
                quantity=signed_qty,
                average_price=Decimal(str(round(fill_price, 2))),
                ltp=Decimal(str(round(fill_price, 2))),
                product_type=ProductType.NRML,
            )
            return

        pos = self._positions[symbol]
        old_qty = pos.quantity
        old_avg = float(pos.average_price)
        new_qty = old_qty + signed_qty

        if new_qty == 0:
            # Position fully closed — realize all P&L
            if old_qty > 0:
                realized = (fill_price - old_avg) * abs(old_qty)
            else:
                realized = (old_avg - fill_price) * abs(old_qty)

            self._positions[symbol] = pos.model_copy(
                update={
                    "quantity": 0,
                    "average_price": Decimal("0"),
                    "ltp": Decimal(str(round(fill_price, 2))),
                    "pnl_realized": pos.pnl_realized + Decimal(str(round(realized, 2))),
                    "pnl_unrealized": Decimal("0"),
                }
            )

        elif (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
            # Adding to existing position — compute new weighted average price
            new_avg = (
                old_avg * abs(old_qty) + fill_price * abs(signed_qty)
            ) / abs(new_qty)
            self._positions[symbol] = pos.model_copy(
                update={
                    "quantity": new_qty,
                    "average_price": Decimal(str(round(new_avg, 2))),
                    "ltp": Decimal(str(round(fill_price, 2))),
                }
            )

        else:
            # Reducing or flipping position — realize P&L on the closed portion
            closed_qty = min(abs(old_qty), abs(signed_qty))
            if old_qty > 0:
                realized = (fill_price - old_avg) * closed_qty
            else:
                realized = (old_avg - fill_price) * closed_qty

            # If position flipped, the remaining qty is at fill_price
            # If just reduced, keep old average
            if abs(new_qty) == abs(signed_qty) - abs(old_qty):
                new_avg_price = fill_price
            else:
                new_avg_price = old_avg

            self._positions[symbol] = pos.model_copy(
                update={
                    "quantity": new_qty,
                    "average_price": Decimal(str(round(new_avg_price, 2))),
                    "ltp": Decimal(str(round(fill_price, 2))),
                    "pnl_realized": pos.pnl_realized
                    + Decimal(str(round(realized, 2))),
                }
            )

    def _apply_slippage(self, price: float, side: OrderSide) -> float:
        """Apply slippage to a fill price.

        Buys slip up (pay more), sells slip down (receive less).

        Args:
            price: The raw market price.
            side: BUY or SELL.

        Returns:
            The slippage-adjusted price.
        """
        slippage_fraction = self._slippage_bps / 10_000.0
        if side == OrderSide.BUY:
            return price * (1.0 + slippage_fraction)
        else:
            return price * (1.0 - slippage_fraction)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def cash(self) -> float:
        """Current cash balance."""
        return self._cash

    @property
    def equity(self) -> float:
        """Total equity = cash + mark-to-market value of all open positions."""
        mtm = 0.0
        for symbol, pos in self._positions.items():
            if pos.quantity == 0:
                continue
            current_price = self._current_prices.get(symbol, float(pos.ltp))
            mtm += pos.quantity * current_price
        return self._cash + mtm

    @property
    def trades(self) -> list[Trade]:
        """All executed trades."""
        return list(self._trades)

    @property
    def positions(self) -> dict[str, Position]:
        """All positions keyed by symbol."""
        return dict(self._positions)

    @property
    def pending_orders(self) -> dict[str, Order]:
        """Currently pending (unfilled) orders."""
        return dict(self._pending_orders)

    @property
    def total_commission(self) -> float:
        """Total commission paid across all fills."""
        return self._total_commission

    @property
    def all_orders(self) -> dict[str, Order]:
        """All orders (filled, pending, cancelled, etc.)."""
        return dict(self._orders)
