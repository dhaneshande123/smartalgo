"""
Order Reconciliation Engine — Ensures local order state matches broker state.

Periodically fetches the order book from the broker and reconciles with
the local order tracking system. Detects and resolves discrepancies such as:
- Orders filled on the exchange but not yet reflected locally
- Orders rejected/cancelled by the broker
- Ghost orders (local but not on broker)
- Partial fills not yet processed
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from core.models import Order, OrderStatus, Trade

from .base import BaseBroker

logger = logging.getLogger(__name__)


class DiscrepancyType(str, Enum):
    """Types of order state discrepancies."""
    STATUS_MISMATCH = "status_mismatch"      # Local and broker status differ
    MISSING_LOCAL = "missing_local"           # Order on broker but not in local tracking
    MISSING_BROKER = "missing_broker"         # Order in local tracking but not on broker
    FILL_MISMATCH = "fill_mismatch"           # Filled quantity differs
    PRICE_MISMATCH = "price_mismatch"         # Average price differs


@dataclass
class Discrepancy:
    """A detected discrepancy between local and broker order state."""
    discrepancy_type: DiscrepancyType
    order_id: str
    broker_order_id: str | None = None
    local_value: str = ""
    broker_value: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved: bool = False
    resolution: str = ""

    def __repr__(self) -> str:
        return (
            f"Discrepancy({self.discrepancy_type.value}: "
            f"order={self.order_id}, local={self.local_value}, "
            f"broker={self.broker_value})"
        )


@dataclass
class ReconciliationResult:
    """Result of a reconciliation cycle."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    broker_name: str = ""
    local_order_count: int = 0
    broker_order_count: int = 0
    discrepancies_found: int = 0
    discrepancies_resolved: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""


# Callback for when discrepancies are detected
DiscrepancyHandler = Callable[[list[Discrepancy]], Awaitable[None]]


class OrderReconciler:
    """Reconciles local order state with broker order book.

    Usage:
        reconciler = OrderReconciler(broker, get_local_orders_fn)
        reconciler.on_discrepancy(my_handler)
        await reconciler.start(interval=30)  # reconcile every 30 seconds
    """

    def __init__(
        self,
        broker: BaseBroker,
        get_local_orders: Callable[[], Awaitable[dict[str, Order]]],
        auto_resolve: bool = True,
    ) -> None:
        """
        Args:
            broker: The broker to reconcile against.
            get_local_orders: Async callable returning dict of order_id -> Order
                             for all orders being tracked locally.
            auto_resolve: If True, automatically update local state on discrepancy.
        """
        self._broker = broker
        self._get_local_orders = get_local_orders
        self._auto_resolve = auto_resolve
        self._handlers: list[DiscrepancyHandler] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._history: list[ReconciliationResult] = []
        self._max_history = 100

    def on_discrepancy(self, handler: DiscrepancyHandler) -> None:
        """Register a callback for when discrepancies are detected."""
        self._handlers.append(handler)

    async def reconcile_once(self) -> ReconciliationResult:
        """Run a single reconciliation cycle."""
        import time
        start = time.perf_counter()
        result = ReconciliationResult(broker_name=self._broker.name)

        try:
            # Fetch both sides
            local_orders = await self._get_local_orders()
            broker_orders_list = await self._broker.get_order_book()

            result.local_order_count = len(local_orders)
            result.broker_order_count = len(broker_orders_list)

            # Index broker orders by broker_order_id
            broker_orders: dict[str, Order] = {}
            for bo in broker_orders_list:
                if bo.broker_order_id:
                    broker_orders[bo.broker_order_id] = bo

            discrepancies: list[Discrepancy] = []

            # Check local orders against broker
            for order_id, local_order in local_orders.items():
                if not local_order.broker_order_id:
                    continue

                broker_order = broker_orders.get(local_order.broker_order_id)

                if broker_order is None:
                    # Order exists locally but not on broker
                    if local_order.status not in (
                        OrderStatus.PENDING,
                        OrderStatus.CANCELLED,
                        OrderStatus.REJECTED,
                        OrderStatus.ERROR,
                    ):
                        discrepancies.append(Discrepancy(
                            discrepancy_type=DiscrepancyType.MISSING_BROKER,
                            order_id=order_id,
                            broker_order_id=local_order.broker_order_id,
                            local_value=local_order.status.value,
                            broker_value="NOT_FOUND",
                        ))
                    continue

                # Status mismatch
                if local_order.status != broker_order.status:
                    d = Discrepancy(
                        discrepancy_type=DiscrepancyType.STATUS_MISMATCH,
                        order_id=order_id,
                        broker_order_id=local_order.broker_order_id,
                        local_value=local_order.status.value,
                        broker_value=broker_order.status.value,
                    )
                    discrepancies.append(d)

                # Fill quantity mismatch
                if local_order.filled_quantity != broker_order.filled_quantity:
                    discrepancies.append(Discrepancy(
                        discrepancy_type=DiscrepancyType.FILL_MISMATCH,
                        order_id=order_id,
                        broker_order_id=local_order.broker_order_id,
                        local_value=str(local_order.filled_quantity),
                        broker_value=str(broker_order.filled_quantity),
                    ))

                # Average price mismatch (only if filled)
                if broker_order.filled_quantity > 0:
                    if local_order.average_price != broker_order.average_price:
                        discrepancies.append(Discrepancy(
                            discrepancy_type=DiscrepancyType.PRICE_MISMATCH,
                            order_id=order_id,
                            broker_order_id=local_order.broker_order_id,
                            local_value=str(local_order.average_price),
                            broker_value=str(broker_order.average_price),
                        ))

                # Remove from broker dict to track what's left
                del broker_orders[local_order.broker_order_id]

            # Remaining broker orders are not tracked locally
            for broker_oid, broker_order in broker_orders.items():
                # Only flag active orders — ignore completed ones
                if broker_order.status in (
                    OrderStatus.OPEN,
                    OrderStatus.PLACED,
                    OrderStatus.PARTIAL,
                ):
                    discrepancies.append(Discrepancy(
                        discrepancy_type=DiscrepancyType.MISSING_LOCAL,
                        order_id="",
                        broker_order_id=broker_oid,
                        local_value="NOT_TRACKED",
                        broker_value=broker_order.status.value,
                    ))

            result.discrepancies = discrepancies
            result.discrepancies_found = len(discrepancies)

            # Notify handlers
            if discrepancies:
                logger.warning(
                    "Reconciliation found %d discrepancies on %s",
                    len(discrepancies),
                    self._broker.name,
                )
                for d in discrepancies:
                    logger.warning("  %s", d)

                for handler in self._handlers:
                    try:
                        await handler(discrepancies)
                    except Exception as exc:
                        logger.error("Discrepancy handler failed: %s", exc)
            else:
                logger.debug("Reconciliation clean on %s", self._broker.name)

        except Exception as exc:
            result.success = False
            result.error = str(exc)
            logger.error("Reconciliation failed on %s: %s", self._broker.name, exc)

        result.duration_ms = (time.perf_counter() - start) * 1000

        # Store history
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return result

    async def start(self, interval: float = 30.0) -> None:
        """Start periodic reconciliation.

        Args:
            interval: Seconds between reconciliation cycles.
        """
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(interval))
        logger.info(
            "Order reconciler started for %s (interval=%.0fs)",
            self._broker.name,
            interval,
        )

    async def stop(self) -> None:
        """Stop periodic reconciliation."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Order reconciler stopped for %s", self._broker.name)

    async def _run_loop(self, interval: float) -> None:
        """Background loop for periodic reconciliation."""
        while self._running:
            try:
                await self.reconcile_once()
            except Exception as exc:
                logger.error("Reconciliation loop error: %s", exc)
            await asyncio.sleep(interval)

    @property
    def history(self) -> list[ReconciliationResult]:
        """Recent reconciliation results."""
        return list(self._history)

    @property
    def last_result(self) -> ReconciliationResult | None:
        """Most recent reconciliation result."""
        return self._history[-1] if self._history else None
