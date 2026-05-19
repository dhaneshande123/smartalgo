"""
Bracket & Cover Order Manager -- manages composite order structures where
an entry fill triggers automatic placement of exit legs (target + stoploss).

Bracket Order:
    Entry -> (on fill) -> Target (LIMIT) + Stoploss (SL-M)
    When one exit leg fills, the other is cancelled (OCO behaviour).
    Supports trailing stoploss.

Cover Order:
    Entry -> (on fill) -> Stoploss (SL-M)
    Simpler variant with only a compulsory stoploss leg.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Awaitable, Callable

from core.models import Order, OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class BracketState(str, Enum):
    """Lifecycle state of a bracket / cover order."""

    PENDING = "pending"
    ENTRY_PLACED = "entry_placed"
    ACTIVE = "active"          # Entry filled, exit legs placed
    TARGET_HIT = "target_hit"  # Target leg filled
    SL_HIT = "sl_hit"          # Stoploss leg filled
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class BracketOrder:
    """A bracket order: entry + target + stoploss with OCO exit.

    Attributes:
        bracket_id:      Unique ID for this bracket.
        entry_order:     The entry order template.
        target_price:    Limit price for the target (profit-taking) leg.
        stoploss_price:  Trigger price for the stoploss leg (SL-M).
        trailing_sl:     Trailing stoploss distance in price points (0 = off).
        state:           Current bracket lifecycle state.
        entry_order_id:  OMS order ID for the entry leg.
        target_order_id: OMS order ID for the target leg.
        sl_order_id:     OMS order ID for the stoploss leg.
        strategy_id:     Owning strategy.
        fill_price:      Actual entry fill price.
        fill_qty:        Actual entry fill quantity.
        created_at:      Creation timestamp.
    """

    bracket_id: str
    entry_order: Order
    target_price: float
    stoploss_price: float
    trailing_sl: float = 0.0
    state: BracketState = BracketState.PENDING
    entry_order_id: str = ""
    target_order_id: str = ""
    sl_order_id: str = ""
    strategy_id: str = ""
    fill_price: float = 0.0
    fill_qty: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CoverOrder:
    """A cover order: entry + compulsory stoploss.

    Attributes:
        cover_id:        Unique ID for this cover order.
        entry_order:     The entry order template.
        stoploss_price:  Trigger price for the stoploss leg.
        state:           Current lifecycle state.
        entry_order_id:  OMS order ID for the entry leg.
        sl_order_id:     OMS order ID for the stoploss leg.
        strategy_id:     Owning strategy.
        fill_price:      Actual entry fill price.
        fill_qty:        Actual entry fill quantity.
        created_at:      Creation timestamp.
    """

    cover_id: str
    entry_order: Order
    stoploss_price: float
    state: BracketState = BracketState.PENDING
    entry_order_id: str = ""
    sl_order_id: str = ""
    strategy_id: str = ""
    fill_price: float = 0.0
    fill_qty: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Callable types for OMS interaction
# ---------------------------------------------------------------------------

SubmitFn = Callable[[Order], Awaitable[str]]
CancelFn = Callable[[str, str], Awaitable[bool]]  # (order_id, reason) -> success
ModifyFn = Callable[..., Awaitable[bool]]


# ---------------------------------------------------------------------------
# Bracket Order Manager
# ---------------------------------------------------------------------------


class BracketOrderManager:
    """Manages bracket and cover orders.

    Listens for entry fills and automatically places exit legs.  Implements
    OCO (One-Cancels-Other) for bracket exit legs and supports trailing
    stoploss updates.

    Usage::

        bom = BracketOrderManager(
            submit_fn=oms.submit_order,
            cancel_fn=oms.cancel_order,
            modify_fn=oms.modify_order,
        )
        bracket_id = await bom.submit_bracket(entry, target=24500, sl=24100)
        # OMS calls bom.on_order_fill when entry fills
        # OMS calls bom.on_order_fill when target/SL fills -> auto-cancel other
    """

    def __init__(
        self,
        submit_fn: SubmitFn,
        cancel_fn: CancelFn,
        modify_fn: ModifyFn | None = None,
    ) -> None:
        """
        Args:
            submit_fn: Async callable that submits an :class:`Order` and
                       returns the order_id.
            cancel_fn: Async callable ``(order_id, reason)`` that cancels
                       an order and returns success.
            modify_fn: Async callable for modifying orders (used for
                       trailing SL updates).  Optional.
        """
        self._submit_fn = submit_fn
        self._cancel_fn = cancel_fn
        self._modify_fn = modify_fn

        self._brackets: dict[str, BracketOrder] = {}
        self._covers: dict[str, CoverOrder] = {}

        # Reverse lookups: order_id -> bracket/cover id
        self._order_to_bracket: dict[str, str] = {}
        self._order_to_cover: dict[str, str] = {}

        self._lock = asyncio.Lock()

    # ===================================================================== #
    #  Bracket Orders                                                       #
    # ===================================================================== #

    async def submit_bracket(
        self,
        entry_order: Order,
        target_price: float,
        stoploss_price: float,
        trailing_sl: float = 0.0,
    ) -> str:
        """Submit a bracket order.

        The entry order is placed immediately.  Target and stoploss legs are
        placed automatically once the entry fills.

        Args:
            entry_order:    The entry order.
            target_price:   Target (take-profit) limit price.
            stoploss_price: Stoploss trigger price.
            trailing_sl:    Trailing SL distance in points (0 = disabled).

        Returns:
            The ``bracket_id``.

        Raises:
            ValueError: If prices are inconsistent with the entry side.
        """
        self._validate_bracket_prices(entry_order, target_price, stoploss_price)

        bracket_id = self._generate_id("BRK")
        bracket = BracketOrder(
            bracket_id=bracket_id,
            entry_order=entry_order,
            target_price=target_price,
            stoploss_price=stoploss_price,
            trailing_sl=trailing_sl,
            strategy_id=entry_order.strategy_id or "",
        )

        # Submit the entry order.
        try:
            entry_id = await self._submit_fn(entry_order)
            bracket.entry_order_id = entry_id
            bracket.state = BracketState.ENTRY_PLACED
        except Exception as exc:
            bracket.state = BracketState.ERROR
            logger.error("Bracket %s entry submission failed: %s", bracket_id, exc)
            async with self._lock:
                self._brackets[bracket_id] = bracket
            raise

        async with self._lock:
            self._brackets[bracket_id] = bracket
            self._order_to_bracket[entry_id] = bracket_id

        logger.info(
            "Bracket %s created: entry=%s, target=%.2f, sl=%.2f, trailing=%.1f",
            bracket_id,
            entry_id,
            target_price,
            stoploss_price,
            trailing_sl,
        )
        return bracket_id

    async def cancel_bracket(self, bracket_id: str) -> bool:
        """Cancel a bracket order.

        Cancels the entry order (if not yet filled) or all exit legs (if
        active).

        Args:
            bracket_id: The bracket to cancel.

        Returns:
            ``True`` if the cancellation was initiated successfully.
        """
        bracket = self._brackets.get(bracket_id)
        if bracket is None:
            logger.warning("cancel_bracket: unknown %s", bracket_id)
            return False

        if bracket.state in (
            BracketState.TARGET_HIT,
            BracketState.SL_HIT,
            BracketState.CANCELLED,
        ):
            logger.info("Bracket %s already in terminal state %s", bracket_id, bracket.state.value)
            return False

        cancelled_any = False

        if bracket.state == BracketState.ENTRY_PLACED and bracket.entry_order_id:
            try:
                result = await self._cancel_fn(bracket.entry_order_id, "bracket_cancel")
                if result:
                    cancelled_any = True
            except Exception as exc:
                logger.error("Bracket %s failed to cancel entry: %s", bracket_id, exc)

        if bracket.state == BracketState.ACTIVE:
            if bracket.target_order_id:
                try:
                    result = await self._cancel_fn(bracket.target_order_id, "bracket_cancel")
                    if result:
                        cancelled_any = True
                except Exception as exc:
                    logger.error("Bracket %s failed to cancel target: %s", bracket_id, exc)

            if bracket.sl_order_id:
                try:
                    result = await self._cancel_fn(bracket.sl_order_id, "bracket_cancel")
                    if result:
                        cancelled_any = True
                except Exception as exc:
                    logger.error("Bracket %s failed to cancel SL: %s", bracket_id, exc)

        bracket.state = BracketState.CANCELLED
        logger.info("Bracket %s cancelled", bracket_id)
        return cancelled_any

    # ===================================================================== #
    #  Cover Orders                                                         #
    # ===================================================================== #

    async def submit_cover(
        self,
        entry_order: Order,
        stoploss_price: float,
    ) -> str:
        """Submit a cover order (entry + compulsory stoploss).

        Args:
            entry_order:    The entry order.
            stoploss_price: Stoploss trigger price.

        Returns:
            The ``cover_id``.
        """
        cover_id = self._generate_id("COV")
        cover = CoverOrder(
            cover_id=cover_id,
            entry_order=entry_order,
            stoploss_price=stoploss_price,
            strategy_id=entry_order.strategy_id or "",
        )

        try:
            entry_id = await self._submit_fn(entry_order)
            cover.entry_order_id = entry_id
            cover.state = BracketState.ENTRY_PLACED
        except Exception as exc:
            cover.state = BracketState.ERROR
            logger.error("Cover %s entry submission failed: %s", cover_id, exc)
            async with self._lock:
                self._covers[cover_id] = cover
            raise

        async with self._lock:
            self._covers[cover_id] = cover
            self._order_to_cover[entry_id] = cover_id

        logger.info(
            "Cover %s created: entry=%s, sl=%.2f",
            cover_id,
            entry_id,
            stoploss_price,
        )
        return cover_id

    async def cancel_cover(self, cover_id: str) -> bool:
        """Cancel a cover order.

        Cancels the entry order (if not yet filled) or the SL leg (if
        active).

        Args:
            cover_id: The cover order to cancel.

        Returns:
            ``True`` if the cancellation was initiated successfully.
        """
        cover = self._covers.get(cover_id)
        if cover is None:
            logger.warning("cancel_cover: unknown %s", cover_id)
            return False

        if cover.state in (BracketState.SL_HIT, BracketState.CANCELLED):
            logger.info("Cover %s already in terminal state %s", cover_id, cover.state.value)
            return False

        cancelled = False

        if cover.state == BracketState.ENTRY_PLACED and cover.entry_order_id:
            try:
                result = await self._cancel_fn(cover.entry_order_id, "cover_cancel")
                if result:
                    cancelled = True
            except Exception as exc:
                logger.error("Cover %s failed to cancel entry: %s", cover_id, exc)

        if cover.state == BracketState.ACTIVE and cover.sl_order_id:
            try:
                result = await self._cancel_fn(cover.sl_order_id, "cover_cancel")
                if result:
                    cancelled = True
            except Exception as exc:
                logger.error("Cover %s failed to cancel SL: %s", cover_id, exc)

        cover.state = BracketState.CANCELLED
        logger.info("Cover %s cancelled", cover_id)
        return cancelled

    # ===================================================================== #
    #  Event Handlers (called by OMS)                                       #
    # ===================================================================== #

    async def on_order_fill(
        self,
        order_id: str,
        fill_price: float,
        fill_qty: int,
    ) -> None:
        """Handle an order fill event.

        Determines whether *order_id* belongs to a bracket or cover and
        dispatches accordingly:

        - Bracket **entry**: place target + SL legs.
        - Bracket **target**: cancel SL and mark TARGET_HIT.
        - Bracket **SL**: cancel target and mark SL_HIT.
        - Cover **entry**: place SL leg.
        - Cover **SL**: mark SL_HIT.
        """
        async with self._lock:
            bracket_id = self._order_to_bracket.get(order_id)
            cover_id = self._order_to_cover.get(order_id)

        if bracket_id:
            bracket = self._brackets.get(bracket_id)
            if bracket is not None:
                await self._handle_bracket_fill(bracket, order_id, fill_price, fill_qty)

        if cover_id:
            cover = self._covers.get(cover_id)
            if cover is not None:
                await self._handle_cover_fill(cover, order_id, fill_price, fill_qty)

    async def on_order_cancel(self, order_id: str) -> None:
        """Handle an external order cancellation event.

        If a bracket entry is cancelled externally, the bracket transitions
        to CANCELLED.  If an exit leg is cancelled externally while the
        bracket is active, the bracket moves to ERROR state (unexpected).
        """
        async with self._lock:
            bracket_id = self._order_to_bracket.get(order_id)
            cover_id = self._order_to_cover.get(order_id)

        if bracket_id:
            bracket = self._brackets.get(bracket_id)
            if bracket is not None:
                if order_id == bracket.entry_order_id and bracket.state == BracketState.ENTRY_PLACED:
                    bracket.state = BracketState.CANCELLED
                    logger.info("Bracket %s cancelled (entry cancelled externally)", bracket_id)
                elif bracket.state == BracketState.ACTIVE and order_id in (
                    bracket.target_order_id, bracket.sl_order_id
                ):
                    # An exit leg was cancelled externally while the bracket
                    # is active -- this is unexpected and leaves the position
                    # partially protected.
                    logger.warning(
                        "Bracket %s exit leg %s cancelled externally "
                        "(bracket remains ACTIVE -- manual intervention needed)",
                        bracket_id,
                        order_id,
                    )

        if cover_id:
            cover = self._covers.get(cover_id)
            if cover is not None:
                if order_id == cover.entry_order_id and cover.state == BracketState.ENTRY_PLACED:
                    cover.state = BracketState.CANCELLED
                    logger.info("Cover %s cancelled (entry cancelled externally)", cover_id)
                elif order_id == cover.sl_order_id and cover.state == BracketState.ACTIVE:
                    logger.warning(
                        "Cover %s SL leg cancelled externally "
                        "(position unprotected -- manual intervention needed)",
                        cover_id,
                    )

    async def update_trailing_sl(
        self, bracket_id: str, current_price: float
    ) -> None:
        """Update trailing stoploss based on current market price.

        For a BUY entry, the SL moves up (but never down) as price rises.
        For a SELL entry, the SL moves down (but never up) as price falls.

        Args:
            bracket_id:    The bracket order to update.
            current_price: Current market price of the underlying.
        """
        bracket = self._brackets.get(bracket_id)
        if bracket is None:
            return
        if bracket.state != BracketState.ACTIVE:
            return
        if bracket.trailing_sl <= 0:
            return
        if not bracket.sl_order_id:
            return
        if self._modify_fn is None:
            logger.warning(
                "Bracket %s: trailing SL update skipped (no modify_fn)",
                bracket_id,
            )
            return

        is_buy = bracket.entry_order.side == OrderSide.BUY

        if is_buy:
            # For a long position, SL trails below the price and moves up only.
            new_sl = current_price - bracket.trailing_sl
            if new_sl <= bracket.stoploss_price:
                return  # No improvement.
            bracket.stoploss_price = new_sl
        else:
            # For a short position, SL trails above the price and moves down only.
            new_sl = current_price + bracket.trailing_sl
            if new_sl >= bracket.stoploss_price:
                return  # No improvement.
            bracket.stoploss_price = new_sl

        try:
            await self._modify_fn(
                bracket.sl_order_id,
                new_trigger_price=Decimal(str(new_sl)),
            )
            logger.info(
                "Bracket %s trailing SL updated to %.2f (price=%.2f)",
                bracket_id,
                new_sl,
                current_price,
            )
        except Exception as exc:
            logger.error(
                "Bracket %s trailing SL modify failed: %s", bracket_id, exc
            )

    # ===================================================================== #
    #  Queries                                                              #
    # ===================================================================== #

    def get_bracket(self, bracket_id: str) -> BracketOrder | None:
        """Return a bracket order by ID, or ``None``."""
        return self._brackets.get(bracket_id)

    def get_cover(self, cover_id: str) -> CoverOrder | None:
        """Return a cover order by ID, or ``None``."""
        return self._covers.get(cover_id)

    def get_active_brackets(self) -> list[BracketOrder]:
        """Return all brackets in non-terminal states."""
        terminal = {
            BracketState.TARGET_HIT,
            BracketState.SL_HIT,
            BracketState.CANCELLED,
            BracketState.ERROR,
        }
        return [b for b in self._brackets.values() if b.state not in terminal]

    def get_active_covers(self) -> list[CoverOrder]:
        """Return all covers in non-terminal states."""
        terminal = {
            BracketState.TARGET_HIT,
            BracketState.SL_HIT,
            BracketState.CANCELLED,
            BracketState.ERROR,
        }
        return [c for c in self._covers.values() if c.state not in terminal]

    def get_brackets_by_strategy(self, strategy_id: str) -> list[BracketOrder]:
        """Return all brackets belonging to a strategy."""
        return [
            b for b in self._brackets.values()
            if b.strategy_id == strategy_id
        ]

    def get_covers_by_strategy(self, strategy_id: str) -> list[CoverOrder]:
        """Return all covers belonging to a strategy."""
        return [
            c for c in self._covers.values()
            if c.strategy_id == strategy_id
        ]

    # ===================================================================== #
    #  Private Helpers                                                      #
    # ===================================================================== #

    async def _handle_bracket_fill(
        self,
        bracket: BracketOrder,
        order_id: str,
        fill_price: float,
        fill_qty: int,
    ) -> None:
        """Process a fill for a bracket-related order."""

        if order_id == bracket.entry_order_id:
            # Entry filled -- place target + SL legs.
            bracket.fill_price = fill_price
            bracket.fill_qty = fill_qty
            await self._place_target_and_sl(bracket, fill_price, fill_qty)

        elif order_id == bracket.target_order_id:
            # Target hit -- cancel SL (OCO).
            bracket.state = BracketState.TARGET_HIT
            logger.info(
                "Bracket %s target hit @ %.2f", bracket.bracket_id, fill_price
            )
            if bracket.sl_order_id:
                try:
                    await self._cancel_fn(bracket.sl_order_id, "bracket_oco_target_hit")
                except Exception as exc:
                    logger.error(
                        "Bracket %s failed to cancel SL after target hit: %s",
                        bracket.bracket_id,
                        exc,
                    )

        elif order_id == bracket.sl_order_id:
            # SL hit -- cancel target (OCO).
            bracket.state = BracketState.SL_HIT
            logger.info(
                "Bracket %s SL hit @ %.2f", bracket.bracket_id, fill_price
            )
            if bracket.target_order_id:
                try:
                    await self._cancel_fn(
                        bracket.target_order_id, "bracket_oco_sl_hit"
                    )
                except Exception as exc:
                    logger.error(
                        "Bracket %s failed to cancel target after SL hit: %s",
                        bracket.bracket_id,
                        exc,
                    )

    async def _handle_cover_fill(
        self,
        cover: CoverOrder,
        order_id: str,
        fill_price: float,
        fill_qty: int,
    ) -> None:
        """Process a fill for a cover-related order."""

        if order_id == cover.entry_order_id:
            # Entry filled -- place SL.
            cover.fill_price = fill_price
            cover.fill_qty = fill_qty
            await self._place_cover_sl(cover, fill_price, fill_qty)

        elif order_id == cover.sl_order_id:
            # SL hit.
            cover.state = BracketState.SL_HIT
            logger.info(
                "Cover %s SL hit @ %.2f", cover.cover_id, fill_price
            )

    async def _place_target_and_sl(
        self,
        bracket: BracketOrder,
        fill_price: float,
        fill_qty: int,
    ) -> None:
        """Place target and stoploss legs after entry fill."""
        entry = bracket.entry_order
        is_buy = entry.side == OrderSide.BUY
        exit_side = OrderSide.SELL if is_buy else OrderSide.BUY

        # Target leg (LIMIT order).
        target_order = entry.model_copy(deep=True)
        target_order.order_id = ""
        target_order.broker_order_id = None
        target_order.side = exit_side
        target_order.order_type = OrderType.LIMIT
        target_order.price = Decimal(str(bracket.target_price))
        target_order.trigger_price = None
        target_order.quantity = fill_qty
        target_order.filled_quantity = 0
        target_order.status = OrderStatus.PENDING
        target_order.placed_at = None
        target_order.updated_at = None
        target_order.parent_order_id = bracket.entry_order_id

        # Stoploss leg (SL-M order).
        sl_order = entry.model_copy(deep=True)
        sl_order.order_id = ""
        sl_order.broker_order_id = None
        sl_order.side = exit_side
        sl_order.order_type = OrderType.SL_M
        sl_order.price = None
        sl_order.trigger_price = Decimal(str(bracket.stoploss_price))
        sl_order.quantity = fill_qty
        sl_order.filled_quantity = 0
        sl_order.status = OrderStatus.PENDING
        sl_order.placed_at = None
        sl_order.updated_at = None
        sl_order.parent_order_id = bracket.entry_order_id

        try:
            target_id = await self._submit_fn(target_order)
            sl_id = await self._submit_fn(sl_order)

            bracket.target_order_id = target_id
            bracket.sl_order_id = sl_id
            bracket.state = BracketState.ACTIVE

            async with self._lock:
                self._order_to_bracket[target_id] = bracket.bracket_id
                self._order_to_bracket[sl_id] = bracket.bracket_id

            logger.info(
                "Bracket %s active: target=%s (%.2f), sl=%s (%.2f)",
                bracket.bracket_id,
                target_id,
                bracket.target_price,
                sl_id,
                bracket.stoploss_price,
            )
        except Exception as exc:
            bracket.state = BracketState.ERROR
            logger.error(
                "Bracket %s failed to place exit legs: %s",
                bracket.bracket_id,
                exc,
            )

    async def _place_cover_sl(
        self,
        cover: CoverOrder,
        fill_price: float,
        fill_qty: int,
    ) -> None:
        """Place the stoploss leg after a cover entry fill."""
        entry = cover.entry_order
        is_buy = entry.side == OrderSide.BUY
        exit_side = OrderSide.SELL if is_buy else OrderSide.BUY

        sl_order = entry.model_copy(deep=True)
        sl_order.order_id = ""
        sl_order.broker_order_id = None
        sl_order.side = exit_side
        sl_order.order_type = OrderType.SL_M
        sl_order.price = None
        sl_order.trigger_price = Decimal(str(cover.stoploss_price))
        sl_order.quantity = fill_qty
        sl_order.filled_quantity = 0
        sl_order.status = OrderStatus.PENDING
        sl_order.placed_at = None
        sl_order.updated_at = None
        sl_order.parent_order_id = cover.entry_order_id

        try:
            sl_id = await self._submit_fn(sl_order)
            cover.sl_order_id = sl_id
            cover.state = BracketState.ACTIVE

            async with self._lock:
                self._order_to_cover[sl_id] = cover.cover_id

            logger.info(
                "Cover %s active: sl=%s (%.2f)",
                cover.cover_id,
                sl_id,
                cover.stoploss_price,
            )
        except Exception as exc:
            cover.state = BracketState.ERROR
            logger.error(
                "Cover %s failed to place SL: %s", cover.cover_id, exc
            )

    @staticmethod
    def _validate_bracket_prices(
        entry: Order, target: float, stoploss: float
    ) -> None:
        """Validate that bracket prices are consistent with the entry side.

        For a BUY entry: target must be above stoploss.
        For a SELL entry: target must be below stoploss.

        Raises:
            ValueError: If the price relationship is invalid.
        """
        is_buy = entry.side == OrderSide.BUY
        if is_buy:
            if target <= stoploss:
                raise ValueError(
                    f"BUY bracket: target ({target}) must be > stoploss ({stoploss})"
                )
        else:
            if target >= stoploss:
                raise ValueError(
                    f"SELL bracket: target ({target}) must be < stoploss ({stoploss})"
                )

    @staticmethod
    def _generate_id(prefix: str) -> str:
        """Generate a unique ID with the given prefix."""
        return f"{prefix}-{uuid.uuid4().hex[:12]}"
