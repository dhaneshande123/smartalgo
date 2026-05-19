"""
Order Management System -- central hub for order submission, tracking,
modification, cancellation, and lifecycle event emission.

All broker interactions go through :class:`BrokerManager` and every state
change is validated by the :class:`OrderStateMachine`.  Events are published
to the platform :class:`EventBus` so downstream consumers (risk engine,
PnL engine, UI) are notified in real time.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.broker_gateway.manager import BrokerManager
from core.event_bus.bus import BaseEventBus, EventPriority, Topics
from core.models import (
    Order,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Trade,
)

from .audit_trail import (
    AUDIT_CANCEL,
    AUDIT_ERROR,
    AUDIT_FILL,
    AUDIT_MODIFY,
    AUDIT_STATE_CHANGE,
    AUDIT_SUBMIT,
    AuditEntry,
    AuditTrail,
)
from .state_machine import InvalidTransitionError, OrderStateMachine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Order Manager
# ---------------------------------------------------------------------------


class OrderManager:
    """Central Order Management System.

    Responsibilities
    ----------------
    * Accept :class:`Order` requests, validate them, and route to the broker
      through :class:`BrokerManager`.
    * Track every in-flight order via the :class:`OrderStateMachine`.
    * Handle asynchronous order-update callbacks from the broker and
      reconcile local state.
    * Create :class:`Trade` objects when fills are reported.
    * Publish events on the platform :class:`EventBus` for every state change.
    * Maintain an :class:`AuditTrail` for compliance and debugging.

    Usage::

        oms = OrderManager(broker_manager, event_bus, audit_trail)
        order_id = await oms.submit_order(order)
        await oms.modify_order(order_id, new_price=Decimal("100.50"))
        await oms.cancel_order(order_id)
        cancelled = await oms.cancel_all(strategy_id="strat_1")
    """

    def __init__(
        self,
        broker_manager: BrokerManager,
        event_bus: BaseEventBus,
        audit: AuditTrail,
    ) -> None:
        self._broker_manager = broker_manager
        self._event_bus = event_bus
        self._audit = audit
        self._state_machine = OrderStateMachine()
        self._orders: dict[str, Order] = {}
        self._broker_to_internal: dict[str, str] = {}  # broker_order_id -> order_id
        self._strategy_orders: dict[str, set[str]] = {}  # strategy_id -> order_ids
        self._pending_modifications: dict[str, dict[str, Any]] = {}
        self._trades: list[Trade] = []
        self._lock = asyncio.Lock()

    # ===================================================================== #
    #  Order Submission                                                      #
    # ===================================================================== #

    async def submit_order(self, order: Order) -> str:
        """Validate and submit an order to the broker.

        The order is validated locally, assigned an ID if not already present,
        stored in the tracking dictionaries, and routed to the broker.  The
        local state is updated based on the broker response.

        Args:
            order: A fully populated :class:`Order` instance.

        Returns:
            The internal ``order_id``.

        Raises:
            ValueError: If local validation fails.
        """
        # Ensure a unique order_id.
        if not order.order_id:
            order.order_id = self._generate_order_id()

        async with self._lock:
            if order.order_id in self._orders:
                raise ValueError(
                    f"Duplicate order_id: {order.order_id} already tracked"
                )

        # Local validation.
        errors = await self._validate_order(order)
        if errors:
            error_msg = "; ".join(errors)
            logger.warning(
                "Order %s failed validation: %s", order.order_id, error_msg
            )
            order.rejection_reason = error_msg
            self._state_machine.transition(
                order, OrderStatus.REJECTED, reason=error_msg
            )
            await self._store_order(order)
            await self._record_audit(
                order, AUDIT_SUBMIT, "", OrderStatus.REJECTED.value,
                details={"validation_errors": errors},
            )
            await self._emit_order_event(order, "ORDER_REJECTED")
            return order.order_id

        # Store before sending to broker so concurrent callbacks find the order.
        await self._store_order(order)

        await self._record_audit(
            order, AUDIT_SUBMIT, "", OrderStatus.PENDING.value,
            details={
                "symbol": order.instrument.symbol,
                "side": order.side.value,
                "qty": order.quantity,
                "order_type": order.order_type.value,
                "price": str(order.price) if order.price is not None else None,
            },
        )

        # Route to broker.
        response: OrderResponse = await self._broker_manager.place_order(order)

        if response.success:
            order.broker_order_id = response.broker_order_id
            order.placed_at = datetime.now(timezone.utc)

            if response.broker_order_id:
                async with self._lock:
                    self._broker_to_internal[response.broker_order_id] = order.order_id

            try:
                self._state_machine.transition(
                    order, OrderStatus.PLACED, reason="broker_accepted"
                )
            except InvalidTransitionError:
                logger.warning(
                    "Order %s could not transition to PLACED (current: %s)",
                    order.order_id,
                    order.status.value,
                )

            await self._record_audit(
                order, AUDIT_STATE_CHANGE, OrderStatus.PENDING.value,
                OrderStatus.PLACED.value,
                details={"broker_order_id": response.broker_order_id},
            )
            await self._emit_order_event(order, "ORDER_PLACED")
            logger.info(
                "Order %s placed successfully (broker_id=%s)",
                order.order_id,
                response.broker_order_id,
            )
        else:
            reason = response.message or "broker_rejected"
            order.rejection_reason = reason
            target = (
                OrderStatus.REJECTED
                if response.status == OrderStatus.REJECTED
                else OrderStatus.ERROR
            )
            try:
                self._state_machine.transition(order, target, reason=reason)
            except InvalidTransitionError:
                logger.warning(
                    "Order %s could not transition to %s (current: %s)",
                    order.order_id,
                    target.value,
                    order.status.value,
                )

            event_type = (
                "ORDER_REJECTED" if target == OrderStatus.REJECTED else "ORDER_ERROR"
            )
            await self._record_audit(
                order, AUDIT_ERROR, OrderStatus.PENDING.value, target.value,
                details={"reason": reason},
            )
            await self._emit_order_event(order, event_type)
            logger.warning(
                "Order %s rejected/errored: %s", order.order_id, reason
            )

        return order.order_id

    # ===================================================================== #
    #  Order Modification                                                    #
    # ===================================================================== #

    async def modify_order(
        self,
        order_id: str,
        new_price: Decimal | float | None = None,
        new_quantity: int | None = None,
        new_trigger_price: Decimal | float | None = None,
    ) -> bool:
        """Modify an existing open/partial order.

        Only non-terminal orders with a ``broker_order_id`` can be modified.

        Args:
            order_id:          Internal order ID.
            new_price:         New limit price (optional).
            new_quantity:      New quantity (optional).
            new_trigger_price: New stop-loss trigger price (optional).

        Returns:
            ``True`` if the modification request was sent to the broker.
        """
        order = self.get_order(order_id)
        if order is None:
            logger.warning("modify_order: order %s not found", order_id)
            return False

        if self._state_machine.is_terminal(order.status):
            logger.warning(
                "modify_order: order %s is in terminal state %s",
                order_id,
                order.status.value,
            )
            return False

        if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIAL, OrderStatus.PLACED):
            logger.warning(
                "modify_order: order %s in non-modifiable state %s",
                order_id,
                order.status.value,
            )
            return False

        if not order.broker_order_id:
            logger.warning(
                "modify_order: order %s has no broker_order_id", order_id
            )
            return False

        modifications: dict[str, Any] = {}
        old_values: dict[str, Any] = {}

        if new_price is not None:
            old_values["old_price"] = str(order.price)
            modifications["price"] = float(new_price)
            order.price = Decimal(str(new_price))

        if new_quantity is not None:
            if new_quantity <= 0:
                logger.warning("modify_order: invalid quantity %d", new_quantity)
                return False
            old_values["old_quantity"] = order.quantity
            modifications["quantity"] = new_quantity
            order.quantity = new_quantity

        if new_trigger_price is not None:
            old_values["old_trigger_price"] = str(order.trigger_price)
            modifications["trigger_price"] = float(new_trigger_price)
            order.trigger_price = Decimal(str(new_trigger_price))

        if not modifications:
            logger.debug("modify_order: no modifications specified for %s", order_id)
            return False

        # Track pending modification.
        async with self._lock:
            self._pending_modifications[order_id] = modifications

        response: OrderResponse = await self._broker_manager.modify_order(
            order.broker_order_id, modifications
        )

        async with self._lock:
            self._pending_modifications.pop(order_id, None)

        order.updated_at = datetime.now(timezone.utc)

        await self._record_audit(
            order, AUDIT_MODIFY, order.status.value, order.status.value,
            details={**modifications, **old_values, "success": response.success,
                     "message": response.message},
        )

        if response.success:
            await self._emit_order_event(order, "ORDER_MODIFIED")
            logger.info(
                "Order %s modified: %s", order_id, modifications
            )
            return True

        logger.warning(
            "Order %s modification failed: %s", order_id, response.message
        )
        return False

    # ===================================================================== #
    #  Order Cancellation                                                    #
    # ===================================================================== #

    async def cancel_order(
        self, order_id: str, reason: str = "user_request"
    ) -> bool:
        """Cancel a specific order.

        Args:
            order_id: Internal order ID.
            reason:   Human-readable cancellation reason.

        Returns:
            ``True`` if the cancellation was accepted by the broker.
        """
        order = self.get_order(order_id)
        if order is None:
            logger.warning("cancel_order: order %s not found", order_id)
            return False

        if self._state_machine.is_terminal(order.status):
            logger.warning(
                "cancel_order: order %s already in terminal state %s",
                order_id,
                order.status.value,
            )
            return False

        if not self._state_machine.can_transition(order.status, OrderStatus.CANCELLED):
            logger.warning(
                "cancel_order: cannot cancel order %s in state %s",
                order_id,
                order.status.value,
            )
            return False

        old_status = order.status

        # If the order has a broker ID, send the cancel to the broker.
        if order.broker_order_id:
            response: OrderResponse = await self._broker_manager.cancel_order(
                order.broker_order_id
            )
            if not response.success:
                logger.warning(
                    "cancel_order: broker rejected cancel for %s: %s",
                    order_id,
                    response.message,
                )
                await self._record_audit(
                    order, AUDIT_CANCEL, old_status.value, old_status.value,
                    details={"reason": reason, "broker_response": response.message,
                             "success": False},
                )
                return False

        # Transition locally.
        try:
            self._state_machine.transition(order, OrderStatus.CANCELLED, reason=reason)
        except InvalidTransitionError as exc:
            logger.error("cancel_order transition failed: %s", exc)
            return False

        await self._record_audit(
            order, AUDIT_CANCEL, old_status.value, OrderStatus.CANCELLED.value,
            details={"reason": reason},
        )
        await self._emit_order_event(order, "ORDER_CANCELLED")
        logger.info("Order %s cancelled (reason: %s)", order_id, reason)
        return True

    async def cancel_all(
        self,
        strategy_id: str | None = None,
        symbol: str | None = None,
    ) -> int:
        """Cancel all matching non-terminal orders.

        Args:
            strategy_id: If set, only cancel orders belonging to this strategy.
            symbol:      If set, only cancel orders for this instrument symbol.

        Returns:
            The number of orders successfully cancelled.
        """
        candidates = self.get_active_orders()

        if strategy_id is not None:
            candidates = [o for o in candidates if o.strategy_id == strategy_id]
        if symbol is not None:
            candidates = [o for o in candidates if o.instrument.symbol == symbol]

        if not candidates:
            logger.info("cancel_all: no matching active orders found")
            return 0

        cancelled = 0
        tasks = [
            self.cancel_order(o.order_id, reason="cancel_all")
            for o in candidates
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if result is True:
                cancelled += 1
            elif isinstance(result, Exception):
                logger.error("cancel_all: exception during cancel: %s", result)

        logger.info(
            "cancel_all: cancelled %d / %d orders (strategy=%s, symbol=%s)",
            cancelled,
            len(candidates),
            strategy_id,
            symbol,
        )
        return cancelled

    # ===================================================================== #
    #  Broker Callbacks                                                      #
    # ===================================================================== #

    async def on_order_update(
        self,
        broker_order_id: str,
        new_status: OrderStatus,
        filled_qty: int = 0,
        avg_price: float = 0.0,
    ) -> None:
        """Handle an asynchronous order-status update from the broker.

        This method is designed to be called from the broker's WebSocket
        callback or polling loop.

        Args:
            broker_order_id: The broker-assigned order identifier.
            new_status:      The new :class:`OrderStatus` reported by the broker.
            filled_qty:      Cumulative filled quantity.
            avg_price:       Weighted average fill price.
        """
        async with self._lock:
            order_id = self._broker_to_internal.get(broker_order_id)

        if order_id is None:
            logger.warning(
                "on_order_update: unknown broker_order_id %s", broker_order_id
            )
            return

        order = self.get_order(order_id)
        if order is None:
            logger.error(
                "on_order_update: internal order %s not found for broker_id %s",
                order_id,
                broker_order_id,
            )
            return

        old_status = order.status

        # Skip if already at the target or in a terminal state.
        if old_status == new_status:
            # Still update fill info if quantities changed.
            if filled_qty > 0 and filled_qty != order.filled_quantity:
                order.filled_quantity = filled_qty
                order.average_price = Decimal(str(avg_price))
                order.updated_at = datetime.now(timezone.utc)
            return

        if self._state_machine.is_terminal(old_status):
            logger.debug(
                "on_order_update: order %s already terminal (%s), ignoring %s",
                order_id,
                old_status.value,
                new_status.value,
            )
            return

        # Attempt the transition.
        try:
            self._state_machine.transition(order, new_status, reason="broker_update")
        except InvalidTransitionError as exc:
            logger.error("on_order_update: %s", exc)
            await self._record_audit(
                order, AUDIT_ERROR, old_status.value, old_status.value,
                details={
                    "error": str(exc),
                    "attempted_status": new_status.value,
                    "broker_order_id": broker_order_id,
                },
            )
            return

        # Update fill information.
        if filled_qty > 0:
            order.filled_quantity = filled_qty
            order.average_price = Decimal(str(avg_price))

        await self._record_audit(
            order, AUDIT_STATE_CHANGE, old_status.value, new_status.value,
            details={
                "filled_qty": filled_qty,
                "avg_price": avg_price,
                "broker_order_id": broker_order_id,
            },
        )

        # Determine the event type.
        event_map: dict[OrderStatus, str] = {
            OrderStatus.OPEN: "ORDER_OPEN",
            OrderStatus.PARTIAL: "ORDER_PARTIAL_FILL",
            OrderStatus.FILLED: "ORDER_FILLED",
            OrderStatus.CANCELLED: "ORDER_CANCELLED",
            OrderStatus.REJECTED: "ORDER_REJECTED",
            OrderStatus.ERROR: "ORDER_ERROR",
        }
        event_type = event_map.get(new_status, "ORDER_UPDATE")
        await self._emit_order_event(order, event_type)

        # Create a Trade object on fills.
        if new_status in (OrderStatus.PARTIAL, OrderStatus.FILLED) and filled_qty > 0:
            fill_qty_delta = filled_qty - (
                order.filled_quantity - filled_qty
                if new_status == OrderStatus.PARTIAL
                else 0
            )
            # For simplicity, create a trade for the total filled quantity
            # reported in this callback.
            trade = Trade(
                order_id=order.order_id,
                strategy_id=order.strategy_id,
                instrument=order.instrument,
                side=order.side,
                quantity=filled_qty,
                price=Decimal(str(avg_price)),
                timestamp=datetime.now(timezone.utc),
                broker_trade_id=broker_order_id,
            )
            async with self._lock:
                self._trades.append(trade)

            await self._record_audit(
                order, AUDIT_FILL, old_status.value, new_status.value,
                details={
                    "trade_id": trade.trade_id,
                    "fill_qty": filled_qty,
                    "fill_price": avg_price,
                },
            )
            await self._emit_trade_event(order, trade)

            logger.info(
                "Order %s fill: qty=%d @ %.2f (status=%s)",
                order.order_id,
                filled_qty,
                avg_price,
                new_status.value,
            )

    # ===================================================================== #
    #  Query Methods                                                         #
    # ===================================================================== #

    def get_order(self, order_id: str) -> Order | None:
        """Retrieve an order by its internal ID.

        Returns ``None`` if the order is not tracked.
        """
        return self._orders.get(order_id)

    def get_orders_by_strategy(self, strategy_id: str) -> list[Order]:
        """Return all orders belonging to *strategy_id*."""
        order_ids = self._strategy_orders.get(strategy_id, set())
        return [
            self._orders[oid]
            for oid in order_ids
            if oid in self._orders
        ]

    def get_active_orders(self) -> list[Order]:
        """Return all orders that are **not** in a terminal state."""
        return [
            o for o in self._orders.values()
            if not self._state_machine.is_terminal(o.status)
        ]

    def get_all_orders(self) -> list[Order]:
        """Return every tracked order (active and terminal)."""
        return list(self._orders.values())

    def get_trades(self) -> list[Trade]:
        """Return all recorded trades."""
        return list(self._trades)

    async def get_local_orders(self) -> dict[str, Order]:
        """Return a snapshot of the internal order dict.

        Provided for compatibility with the reconciliation engine.
        """
        async with self._lock:
            return dict(self._orders)

    # ===================================================================== #
    #  Internal Helpers                                                      #
    # ===================================================================== #

    async def _validate_order(self, order: Order) -> list[str]:
        """Validate an order locally before submission.

        Returns a list of error messages.  An empty list means the order is
        valid.
        """
        errors: list[str] = []

        if order.quantity <= 0:
            errors.append("quantity must be positive")

        if order.order_type == OrderType.LIMIT:
            if order.price is None or order.price <= 0:
                errors.append("LIMIT order requires a positive price")

        if order.order_type in (OrderType.SL, OrderType.SL_M):
            if order.trigger_price is None or order.trigger_price <= 0:
                errors.append("SL/SL_M order requires a positive trigger_price")

        if order.order_type == OrderType.SL:
            if order.price is None or order.price <= 0:
                errors.append("SL order requires a positive limit price")
            if (
                order.price is not None
                and order.trigger_price is not None
            ):
                if order.side == OrderSide.BUY and order.price < order.trigger_price:
                    errors.append(
                        "BUY SL order: limit price must be >= trigger price"
                    )
                if order.side == OrderSide.SELL and order.price > order.trigger_price:
                    errors.append(
                        "SELL SL order: limit price must be <= trigger price"
                    )

        if not order.instrument.symbol:
            errors.append("instrument symbol is required")

        lot_size = order.instrument.lot_size
        if lot_size > 1 and order.quantity % lot_size != 0:
            errors.append(
                f"quantity {order.quantity} is not a multiple of lot size {lot_size}"
            )

        return errors

    async def _store_order(self, order: Order) -> None:
        """Store order in internal tracking dictionaries."""
        async with self._lock:
            self._orders[order.order_id] = order
            if order.strategy_id:
                if order.strategy_id not in self._strategy_orders:
                    self._strategy_orders[order.strategy_id] = set()
                self._strategy_orders[order.strategy_id].add(order.order_id)

    async def _emit_order_event(self, order: Order, event_type: str) -> None:
        """Publish an order event to the event bus."""
        priority = (
            EventPriority.HIGH
            if event_type in ("ORDER_REJECTED", "ORDER_ERROR")
            else EventPriority.NORMAL
        )
        try:
            await self._event_bus.publish(
                topic=Topics.ORDERS,
                event_type=event_type,
                payload={
                    "order_id": order.order_id,
                    "broker_order_id": order.broker_order_id,
                    "strategy_id": order.strategy_id,
                    "symbol": order.instrument.symbol,
                    "side": order.side.value,
                    "order_type": order.order_type.value,
                    "quantity": order.quantity,
                    "filled_quantity": order.filled_quantity,
                    "price": str(order.price) if order.price is not None else None,
                    "average_price": str(order.average_price),
                    "status": order.status.value,
                    "rejection_reason": order.rejection_reason,
                },
                priority=priority,
                source="oms.order_manager",
                correlation_id=order.order_id,
            )
        except Exception:
            logger.exception(
                "Failed to emit %s event for order %s", event_type, order.order_id
            )

    async def _emit_trade_event(self, order: Order, trade: Trade) -> None:
        """Publish a trade event to the event bus."""
        try:
            await self._event_bus.publish(
                topic=Topics.TRADES,
                event_type="TRADE_FILL",
                payload={
                    "trade_id": trade.trade_id,
                    "order_id": trade.order_id,
                    "strategy_id": trade.strategy_id,
                    "symbol": trade.instrument.symbol,
                    "side": trade.side.value,
                    "quantity": trade.quantity,
                    "price": str(trade.price),
                    "timestamp": trade.timestamp.isoformat(),
                },
                priority=EventPriority.NORMAL,
                source="oms.order_manager",
                correlation_id=order.order_id,
            )
        except Exception:
            logger.exception(
                "Failed to emit TRADE_FILL event for trade %s", trade.trade_id
            )

    async def _record_audit(
        self,
        order: Order,
        event_type: str,
        old_state: str,
        new_state: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Write an entry to the audit trail."""
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc),
            order_id=order.order_id,
            event_type=event_type,
            old_state=old_state,
            new_state=new_state,
            details=details or {},
            strategy_id=order.strategy_id or "",
            broker_order_id=order.broker_order_id or "",
        )
        await self._audit.record(entry)

    def _generate_order_id(self) -> str:
        """Generate a unique internal order identifier."""
        return f"ORD-{uuid.uuid4().hex[:16]}"
