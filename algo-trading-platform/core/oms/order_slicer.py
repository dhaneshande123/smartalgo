"""
Iceberg Order Slicer -- splits large orders into smaller child orders
to minimise market impact and avoid detection.

Supports:
* Configurable max/min slice sizes with optional randomisation.
* Configurable inter-slice delays with optional jitter.
* Wait-for-fill semantics: next slice only sent after current slice fills.
* Tracking of parent-child relationships with per-child metadata.
* Weighted-average fill price computation.
* Cancellation of in-progress iceberg orders (including in-flight children).
* Pause / resume for manual intervention.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable

from core.models import Order, OrderStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration & state
# ---------------------------------------------------------------------------


class SliceState(str, Enum):
    """State of an iceberg parent order."""

    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class SliceConfig:
    """Configuration for order slicing.

    Attributes:
        max_slice_quantity: Maximum lots per child order.
        min_slice_quantity: Minimum lots per child order.
        delay_between_slices_ms: Base delay between consecutive slices.
        randomize_quantity: Vary slice sizes by +-20 % to avoid pattern
            detection.
        randomize_delay: Add jitter (+-30 %) to the inter-slice delay.
        max_concurrent_slices: How many slices may be in-flight at once.
        price_tolerance: Acceptable price deviation from the parent order's
            limit price (0 = use exact price).
    """

    max_slice_quantity: int
    min_slice_quantity: int = 1
    delay_between_slices_ms: int = 500
    randomize_quantity: bool = True
    randomize_delay: bool = True
    max_concurrent_slices: int = 1
    price_tolerance: float = 0.0

    def __post_init__(self) -> None:
        if self.max_slice_quantity < 1:
            raise ValueError("max_slice_quantity must be >= 1")
        if self.min_slice_quantity < 1:
            raise ValueError("min_slice_quantity must be >= 1")
        if self.min_slice_quantity > self.max_slice_quantity:
            raise ValueError("min_slice_quantity cannot exceed max_slice_quantity")
        if self.delay_between_slices_ms < 0:
            raise ValueError("delay_between_slices_ms must be >= 0")
        if self.max_concurrent_slices < 1:
            raise ValueError("max_concurrent_slices must be >= 1")


@dataclass
class ChildSlice:
    """Metadata for a single child slice order.

    Attributes:
        child_order_id: OMS order ID assigned to this child.
        requested_qty: Number of lots requested for this slice.
        filled_qty: Lots filled so far.
        fill_price: Price at which the child was filled (0 if not yet filled).
        submitted_at: When the slice was submitted.
        filled_at: When the fill was reported.
        status: Current status string (pending / submitted / filled / cancelled / error).
    """

    child_order_id: str = ""
    requested_qty: int = 0
    filled_qty: int = 0
    fill_price: float = 0.0
    submitted_at: datetime | None = None
    filled_at: datetime | None = None
    status: str = "pending"


@dataclass
class ParentOrder:
    """Tracks the overall state of an iceberg order.

    Attributes:
        parent_id:       Unique identifier for this iceberg order.
        order:           The original (full-size) order.
        config:          Slicing configuration.
        state:           Current lifecycle state.
        total_filled:    Cumulative lots filled across all child orders.
        child_order_ids: IDs of child orders submitted so far.
        children:        Detailed per-child metadata.
        avg_fill_price:  Weighted-average fill price across all children.
        created_at:      When the iceberg was created.
        completed_at:    When the iceberg reached a terminal state.
    """

    parent_id: str
    order: Order
    config: SliceConfig
    state: SliceState = SliceState.PENDING
    total_filled: int = 0
    child_order_ids: list[str] = field(default_factory=list)
    children: list[ChildSlice] = field(default_factory=list)
    avg_fill_price: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    # Internal tracking for VWAP calculation
    _fill_value: float = field(default=0.0, repr=False)

    @property
    def remaining(self) -> int:
        """Lots still to be filled."""
        return max(self.order.quantity - self.total_filled, 0)

    @property
    def progress_pct(self) -> float:
        """Fill progress as a percentage."""
        if self.order.quantity == 0:
            return 100.0
        return (self.total_filled / self.order.quantity) * 100.0

    @property
    def is_complete(self) -> bool:
        """Whether the entire parent quantity has been filled."""
        return self.total_filled >= self.order.quantity


# ---------------------------------------------------------------------------
# Callable type aliases
# ---------------------------------------------------------------------------

SubmitFn = Callable[[Order], Awaitable[str]]
CancelFn = Callable[[str], Awaitable[None]]


# ---------------------------------------------------------------------------
# Slicer
# ---------------------------------------------------------------------------


class OrderSlicer:
    """Splits large orders into child slices for iceberg execution.

    The slicer submits one child at a time (or up to
    ``max_concurrent_slices``), waits for a fill notification via
    :meth:`on_child_fill`, and then releases the next slice after an
    optional delay.

    Parameters
    ----------
    submit_fn:
        Async callable that submits a child :class:`Order` and returns
        the assigned order_id.
    cancel_fn:
        Optional async callable that cancels an in-flight child order
        by order_id.  Used when :meth:`cancel_iceberg` is called.

    Usage::

        slicer = OrderSlicer(submit_fn=oms.submit_order, cancel_fn=my_cancel)
        parent_id = await slicer.submit_iceberg(
            order, SliceConfig(max_slice_quantity=5)
        )
        # As fills come in from the OMS:
        await slicer.on_child_fill(parent_id, child_id, filled_qty=5, fill_price=24200.0)
        # To cancel:
        await slicer.cancel_iceberg(parent_id)
    """

    def __init__(
        self,
        submit_fn: SubmitFn,
        cancel_fn: CancelFn | None = None,
    ) -> None:
        self._submit_fn = submit_fn
        self._cancel_fn = cancel_fn
        self._parents: dict[str, ParentOrder] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._child_to_parent: dict[str, str] = {}  # child_order_id -> parent_id
        self._fill_events: dict[str, asyncio.Event] = {}  # parent_id -> event
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    async def submit_iceberg(self, order: Order, config: SliceConfig) -> str:
        """Submit an iceberg order.

        Creates a :class:`ParentOrder`, starts a background slicing loop,
        and returns the parent ID.

        Args:
            order:  The full-size order to be sliced.
            config: Slicing parameters.

        Returns:
            The parent order ID.

        Raises:
            ValueError: If order quantity is below the minimum slice size or
                the order is already tracked.
        """
        if order.quantity < config.min_slice_quantity:
            raise ValueError(
                f"Order quantity ({order.quantity}) is less than "
                f"min_slice_quantity ({config.min_slice_quantity})"
            )

        parent_id = f"ICE-{uuid.uuid4().hex[:12]}"
        parent = ParentOrder(
            parent_id=parent_id,
            order=order,
            config=config,
        )

        async with self._lock:
            if parent_id in self._parents:
                raise ValueError(f"Iceberg {parent_id} is already tracked")
            self._parents[parent_id] = parent
            self._fill_events[parent_id] = asyncio.Event()

        # Start the slicing loop in the background.
        task = asyncio.create_task(
            self._slicing_loop(parent_id), name=f"iceberg-{parent_id}"
        )
        self._tasks[parent_id] = task

        logger.info(
            "Iceberg %s created: total_qty=%d, max_slice=%d, symbol=%s",
            parent_id,
            order.quantity,
            config.max_slice_quantity,
            order.instrument.symbol,
        )
        return parent_id

    async def on_child_fill(
        self,
        parent_id: str,
        child_order_id: str,
        filled_qty: int,
        fill_price: float,
    ) -> None:
        """Notify the slicer that a child order has been filled.

        Updates the parent's cumulative fill tracking and VWAP, then signals
        the slicing loop to submit the next slice.

        Args:
            parent_id:      The parent iceberg order ID.
            child_order_id: The child order that was filled.
            filled_qty:     Number of lots filled in this child.
            fill_price:     Price at which the child was filled.
        """
        async with self._lock:
            parent = self._parents.get(parent_id)
            if parent is None:
                logger.warning("on_child_fill: unknown parent %s", parent_id)
                return

            # Update per-child metadata.
            child_meta: ChildSlice | None = None
            for child in parent.children:
                if child.child_order_id == child_order_id:
                    child_meta = child
                    break

            if child_meta is not None:
                child_meta.filled_qty = filled_qty
                child_meta.fill_price = fill_price
                child_meta.filled_at = datetime.now(timezone.utc)
                child_meta.status = "filled"

            # Update parent aggregates.
            parent.total_filled += filled_qty
            parent._fill_value += filled_qty * fill_price
            if parent.total_filled > 0:
                parent.avg_fill_price = parent._fill_value / parent.total_filled

        logger.info(
            "Iceberg %s child fill: +%d @ %.2f (total=%d/%d, VWAP=%.2f)",
            parent_id,
            filled_qty,
            fill_price,
            parent.total_filled,
            parent.order.quantity,
            parent.avg_fill_price,
        )

        # Check if fully filled.
        if parent.is_complete:
            parent.state = SliceState.COMPLETED
            parent.completed_at = datetime.now(timezone.utc)
            logger.info(
                "Iceberg %s completed: %d lots @ VWAP %.2f",
                parent_id,
                parent.total_filled,
                parent.avg_fill_price,
            )

        # Signal the slicing loop that a fill arrived.
        event = self._fill_events.get(parent_id)
        if event is not None:
            event.set()

    async def cancel_iceberg(self, parent_id: str) -> None:
        """Cancel an iceberg order and stop further slicing.

        If a ``cancel_fn`` was provided, in-flight child orders are also
        cancelled.

        Args:
            parent_id: The parent iceberg order ID.
        """
        async with self._lock:
            parent = self._parents.get(parent_id)
            if parent is None:
                logger.warning("cancel_iceberg: unknown parent %s", parent_id)
                return

            if parent.state in (SliceState.COMPLETED, SliceState.CANCELLED):
                logger.info(
                    "Iceberg %s already in terminal state %s",
                    parent_id,
                    parent.state.value,
                )
                return

            parent.state = SliceState.CANCELLED
            parent.completed_at = datetime.now(timezone.utc)

        # Cancel the background task.
        task = self._tasks.pop(parent_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Cancel in-flight children via the cancel function.
        if self._cancel_fn is not None:
            for child in parent.children:
                if child.status in ("pending", "submitted"):
                    try:
                        await self._cancel_fn(child.child_order_id)
                        child.status = "cancelled"
                        logger.info(
                            "Cancelled child %s of iceberg %s",
                            child.child_order_id,
                            parent_id,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to cancel child %s of iceberg %s",
                            child.child_order_id,
                            parent_id,
                        )

        logger.info(
            "Iceberg %s cancelled (filled %d/%d)",
            parent_id,
            parent.total_filled,
            parent.order.quantity,
        )

    async def pause_iceberg(self, parent_id: str) -> None:
        """Pause an iceberg order (no new slices until resumed).

        The currently in-flight slice will still complete, but no further
        slices are submitted.

        Args:
            parent_id: The parent iceberg order ID.
        """
        parent = self._parents.get(parent_id)
        if parent is not None and parent.state == SliceState.ACTIVE:
            parent.state = SliceState.PAUSED
            logger.info("Iceberg %s paused", parent_id)

    async def resume_iceberg(self, parent_id: str) -> None:
        """Resume a paused iceberg order.

        Args:
            parent_id: The parent iceberg order ID.
        """
        parent = self._parents.get(parent_id)
        if parent is not None and parent.state == SliceState.PAUSED:
            parent.state = SliceState.ACTIVE
            logger.info("Iceberg %s resumed", parent_id)
            # Signal the loop to wake up.
            event = self._fill_events.get(parent_id)
            if event is not None:
                event.set()

    # ------------------------------------------------------------------ #
    #  Queries                                                            #
    # ------------------------------------------------------------------ #

    def get_status(self, parent_id: str) -> ParentOrder | None:
        """Return the :class:`ParentOrder` for *parent_id*, or ``None``."""
        return self._parents.get(parent_id)

    def get_all_parents(self) -> list[ParentOrder]:
        """Return all tracked parent orders."""
        return list(self._parents.values())

    def get_active_icebergs(self) -> list[ParentOrder]:
        """Return only active (non-terminal) iceberg orders."""
        return [
            p for p in self._parents.values()
            if p.state in (SliceState.ACTIVE, SliceState.PAUSED)
        ]

    def get_parent_for_child(self, child_order_id: str) -> str | None:
        """Return the parent_id for a child order, or ``None``."""
        return self._child_to_parent.get(child_order_id)

    # ------------------------------------------------------------------ #
    #  Internal Slicing Loop                                              #
    # ------------------------------------------------------------------ #

    async def _slicing_loop(self, parent_id: str) -> None:
        """Background coroutine that submits slices one at a time.

        For each slice:
        1. Compute the next slice quantity.
        2. Submit the child order via ``submit_fn``.
        3. Wait for the fill event (set by :meth:`on_child_fill`).
        4. Wait for the configured inter-slice delay.
        5. Repeat until the parent quantity is exhausted or the task is
           cancelled.
        """
        parent = self._parents.get(parent_id)
        if parent is None:
            return

        fill_event = self._fill_events.get(parent_id)
        if fill_event is None:
            return

        parent.state = SliceState.ACTIVE

        try:
            while parent.remaining > 0:
                # Handle paused state.
                while parent.state == SliceState.PAUSED:
                    fill_event.clear()
                    try:
                        await asyncio.wait_for(fill_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                    if parent.state not in (SliceState.PAUSED, SliceState.ACTIVE):
                        return

                if parent.state != SliceState.ACTIVE:
                    break

                slice_qty = self._compute_next_slice_qty(parent)
                if slice_qty <= 0:
                    break

                # Create the child order from the parent.
                child_order = self._create_child_order(parent, slice_qty)

                # Build child metadata.
                child_meta = ChildSlice(
                    requested_qty=slice_qty,
                    submitted_at=datetime.now(timezone.utc),
                )

                try:
                    child_id = await self._submit_fn(child_order)
                    child_meta.child_order_id = child_id
                    child_meta.status = "submitted"

                    async with self._lock:
                        parent.child_order_ids.append(child_id)
                        parent.children.append(child_meta)
                        self._child_to_parent[child_id] = parent_id

                    logger.debug(
                        "Iceberg %s submitted slice %s: qty=%d (remaining=%d)",
                        parent_id,
                        child_id,
                        slice_qty,
                        parent.remaining,
                    )
                except Exception as exc:
                    child_meta.status = "error"
                    async with self._lock:
                        parent.children.append(child_meta)
                    logger.error(
                        "Iceberg %s slice submission failed: %s",
                        parent_id,
                        exc,
                    )
                    parent.state = SliceState.ERROR
                    parent.completed_at = datetime.now(timezone.utc)
                    break

                # Wait for the child fill before submitting next slice.
                fill_event.clear()
                try:
                    await asyncio.wait_for(fill_event.wait(), timeout=300.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Iceberg %s child %s fill timed out after 300s",
                        parent_id,
                        child_meta.child_order_id,
                    )
                    parent.state = SliceState.ERROR
                    parent.completed_at = datetime.now(timezone.utc)
                    break

                # Check if parent moved to non-active state during the wait.
                if parent.state not in (SliceState.ACTIVE, SliceState.PAUSED):
                    break

                # Inter-slice delay before next submission.
                if parent.remaining > 0:
                    delay = self._compute_delay(parent.config)
                    if delay > 0:
                        await asyncio.sleep(delay)

            # Mark as completed if fully filled.
            if parent.is_complete and parent.state == SliceState.ACTIVE:
                parent.state = SliceState.COMPLETED
                parent.completed_at = datetime.now(timezone.utc)
                logger.info("Iceberg %s completed", parent_id)

        except asyncio.CancelledError:
            logger.debug("Iceberg %s slicing loop cancelled", parent_id)
            raise
        except Exception:
            logger.exception("Iceberg %s slicing loop error", parent_id)
            parent.state = SliceState.ERROR
            parent.completed_at = datetime.now(timezone.utc)
        finally:
            self._tasks.pop(parent_id, None)

    # ------------------------------------------------------------------ #
    #  Slice Computation Helpers                                          #
    # ------------------------------------------------------------------ #

    def _compute_next_slice_qty(self, parent: ParentOrder) -> int:
        """Determine the lot count for the next slice.

        The base quantity is ``min(remaining, max_slice_quantity)``.
        When *randomize_quantity* is enabled, this value is varied by
        +-20 %, clamped to ``[min_slice_quantity, remaining]``.
        Lot-size alignment is applied if the instrument's lot size > 1.
        """
        remaining = parent.remaining
        if remaining <= 0:
            return 0

        cfg = parent.config
        base = min(remaining, cfg.max_slice_quantity)

        if cfg.randomize_quantity and base > cfg.min_slice_quantity:
            low = max(cfg.min_slice_quantity, int(base * 0.8))
            high = min(remaining, int(base * 1.2))
            if high < low:
                high = low
            base = random.randint(low, high)

        # Clamp.
        base = max(base, cfg.min_slice_quantity)
        base = min(base, remaining)

        # Lot-size alignment.
        lot_size = parent.order.instrument.lot_size
        if lot_size > 1 and base >= lot_size:
            base = (base // lot_size) * lot_size
            if base == 0:
                base = lot_size

        return base

    def _compute_delay(self, config: SliceConfig) -> float:
        """Compute delay in seconds before the next slice.

        If *randomize_delay* is enabled, the base delay is varied by
        +-30 %.
        """
        base_ms = config.delay_between_slices_ms
        if config.randomize_delay and base_ms > 0:
            jitter = random.uniform(-0.3, 0.3)
            base_ms = int(base_ms * (1.0 + jitter))
        return max(base_ms, 0) / 1000.0

    @staticmethod
    def _create_child_order(parent: ParentOrder, quantity: int) -> Order:
        """Create a child order by deep-copying the parent with adjusted quantity.

        The child inherits instrument, side, order type, product type, and
        price fields.  It gets a fresh order_id and ``parent_order_id`` set
        to the parent's ID.
        """
        child = parent.order.model_copy(deep=True)
        child.order_id = f"{parent.parent_id}-{len(parent.child_order_ids):03d}"
        child.quantity = quantity
        child.filled_quantity = 0
        child.status = OrderStatus.PENDING
        child.parent_order_id = parent.order.order_id
        child.broker_order_id = None
        child.placed_at = None
        child.updated_at = None
        return child
