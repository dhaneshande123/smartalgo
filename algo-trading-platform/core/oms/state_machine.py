"""
Order State Machine -- manages valid lifecycle transitions for orders.

Enforces a strict directed graph of allowed state changes and raises
``InvalidTransitionError`` when a caller attempts an illegal move.  Supports
listener callbacks that are invoked after every successful transition.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from core.models import Order, OrderStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted.

    Attributes:
        order_id:      The order that triggered the error.
        current_state: The state the order was in at the time of the attempt.
        target_state:  The state the caller tried to move to.
        reason:        Human-readable explanation.
    """

    def __init__(
        self,
        order_id: str,
        current_state: OrderStatus,
        target_state: OrderStatus,
        reason: str = "",
    ) -> None:
        self.order_id = order_id
        self.current_state = current_state
        self.target_state = target_state
        detail = (
            f"Invalid transition for order {order_id}: "
            f"{current_state.value} -> {target_state.value}"
        )
        if reason:
            detail += f" ({reason})"
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Transition listener type
# ---------------------------------------------------------------------------

TransitionListener = Callable[[Order, OrderStatus, OrderStatus, str], Any]
"""Signature: (order, old_status, new_status, reason) -> Any"""


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------


class OrderStateMachine:
    """Manages order state transitions with validation.

    Valid transitions::

        PENDING   -> PLACED, REJECTED, ERROR, CANCELLED
        PLACED    -> OPEN, REJECTED, ERROR, CANCELLED
        OPEN      -> PARTIAL, FILLED, CANCELLED, ERROR
        PARTIAL   -> FILLED, CANCELLED, ERROR

    Terminal states: FILLED, CANCELLED, REJECTED, ERROR

    Usage::

        sm = OrderStateMachine()
        if sm.can_transition(order.status, OrderStatus.PLACED):
            order = sm.transition(order, OrderStatus.PLACED)
    """

    VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
        OrderStatus.PENDING: {
            OrderStatus.PLACED,
            OrderStatus.REJECTED,
            OrderStatus.ERROR,
            OrderStatus.CANCELLED,
        },
        OrderStatus.PLACED: {
            OrderStatus.OPEN,
            OrderStatus.REJECTED,
            OrderStatus.ERROR,
            OrderStatus.CANCELLED,
        },
        OrderStatus.OPEN: {
            OrderStatus.PARTIAL,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.ERROR,
        },
        OrderStatus.PARTIAL: {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.ERROR,
        },
        # Terminal states have no outgoing edges.
        OrderStatus.FILLED: set(),
        OrderStatus.CANCELLED: set(),
        OrderStatus.REJECTED: set(),
        OrderStatus.ERROR: set(),
    }

    TERMINAL_STATES: set[OrderStatus] = {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.ERROR,
    }

    def __init__(self) -> None:
        self._listeners: list[TransitionListener] = []

    # -- public API ----------------------------------------------------------

    def can_transition(self, current: OrderStatus, target: OrderStatus) -> bool:
        """Return *True* if moving from *current* to *target* is allowed."""
        allowed = self.VALID_TRANSITIONS.get(current)
        if allowed is None:
            return False
        return target in allowed

    def transition(
        self,
        order: Order,
        new_status: OrderStatus,
        reason: str = "",
    ) -> Order:
        """Transition *order* to *new_status*.

        The ``order`` object is mutated in place (``status`` and ``updated_at``
        are set) and also returned for convenience.  If the transition is
        invalid, an :class:`InvalidTransitionError` is raised and the order is
        left unchanged.

        Args:
            order:      The order to transition.
            new_status: The desired target state.
            reason:     Optional human-readable reason for the transition.

        Returns:
            The same *order* instance with its status updated.

        Raises:
            InvalidTransitionError: If the transition is not in the valid graph.
        """
        old_status = order.status

        if old_status == new_status:
            logger.debug(
                "Order %s already in state %s -- no-op transition",
                order.order_id,
                new_status.value,
            )
            return order

        if not self.can_transition(old_status, new_status):
            raise InvalidTransitionError(
                order_id=order.order_id,
                current_state=old_status,
                target_state=new_status,
                reason=reason,
            )

        # Apply the state change.
        order.status = new_status
        order.updated_at = datetime.now(timezone.utc)

        if new_status == OrderStatus.REJECTED and reason:
            order.rejection_reason = reason

        logger.info(
            "Order %s transitioned %s -> %s%s",
            order.order_id,
            old_status.value,
            new_status.value,
            f" (reason: {reason})" if reason else "",
        )

        # Notify listeners.
        self._notify_listeners(order, old_status, new_status, reason)

        return order

    def is_terminal(self, status: OrderStatus) -> bool:
        """Return *True* if *status* is a terminal (final) state."""
        return status in self.TERMINAL_STATES

    def on_transition(self, callback: TransitionListener) -> None:
        """Register a listener invoked after every successful transition.

        The callback receives ``(order, old_status, new_status, reason)``.
        Exceptions raised inside the callback are logged but do **not**
        prevent the transition from completing.
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: TransitionListener) -> None:
        """Remove a previously registered listener.  No-op if not found."""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def get_valid_targets(self, current: OrderStatus) -> set[OrderStatus]:
        """Return the set of states reachable from *current*.

        If *current* is terminal the returned set is empty.
        """
        return set(self.VALID_TRANSITIONS.get(current, set()))

    # -- internal ------------------------------------------------------------

    def _notify_listeners(
        self,
        order: Order,
        old_status: OrderStatus,
        new_status: OrderStatus,
        reason: str,
    ) -> None:
        """Fan out the transition event to all registered listeners."""
        for listener in self._listeners:
            try:
                listener(order, old_status, new_status, reason)
            except Exception:
                logger.exception(
                    "Transition listener %r raised an exception for order %s",
                    listener,
                    order.order_id,
                )
