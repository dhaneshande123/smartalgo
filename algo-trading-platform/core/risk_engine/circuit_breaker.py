"""
Automatic circuit-breaker mechanism for halting trading when risk
thresholds are exceeded.

Supports multiple named breakers, each with independent trigger
conditions, cooldown timers, and optional auto-recovery.  When any
breaker transitions to OPEN all new order flow is blocked until the
breaker recovers (manually or automatically).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

from core.event_bus.bus import BaseEventBus, EventPriority, Topics
from core.models import Alert, AlertLevel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums & data structures
# ---------------------------------------------------------------------------


class CircuitBreakerState(str, Enum):
    """Lifecycle states of a circuit breaker."""

    CLOSED = "closed"  # Normal trading
    OPEN = "open"  # Trading halted
    HALF_OPEN = "half_open"  # Trial recovery period


@dataclass
class CircuitBreakerConfig:
    """Configuration for a single named circuit breaker.

    Each threshold field is checked only when its value is > 0.
    """

    name: str
    max_loss_amount: float = 0.0  # trigger if daily loss exceeds this (absolute)
    max_loss_pct: float = 0.0  # trigger if loss % of capital exceeds this
    max_consecutive_losses: int = 0  # trigger after N consecutive losing trades
    max_drawdown_pct: float = 0.0  # trigger on portfolio drawdown %
    cooldown_seconds: float = 300.0  # time before auto-recovery (0 = manual only)
    auto_recover: bool = False  # auto-recover after cooldown?
    cancel_open_orders: bool = True  # cancel all open orders when triggered


@dataclass
class CircuitBreakerEvent:
    """Immutable record of a circuit-breaker state change."""

    breaker_name: str
    trigger_reason: str
    triggered_at: datetime
    state: CircuitBreakerState
    recovered_at: datetime | None = None
    metrics_at_trigger: dict[str, Any] = field(default_factory=dict)


# Type alias for the cancel-all-orders callback
CancelAllFn = Callable[[], Awaitable[None]]

# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Automatic trading-halt mechanism.

    Multiple named circuit breakers can be configured, each with its own
    trigger thresholds and recovery policy.  When **any** breaker trips to
    ``OPEN``:

    * New orders are rejected (``is_trading_allowed()`` returns ``False``).
    * If ``cancel_open_orders`` is set the provided *cancel_all_fn* callback
      is invoked.
    * An alert is published on the ``RISK_BREACH`` topic of the event bus.
    * If ``auto_recover`` is ``True`` a background task will move the breaker
      back to ``CLOSED`` after the configured cooldown.

    Usage::

        cb = CircuitBreaker(event_bus, cancel_all_fn=my_cancel_fn)
        cb.add_breaker(CircuitBreakerConfig(
            name="daily_loss",
            max_loss_amount=500_000,
            cooldown_seconds=600,
            auto_recover=True,
        ))
        triggered = await cb.check(daily_loss=-600_000)
    """

    def __init__(
        self,
        event_bus: BaseEventBus,
        cancel_all_fn: CancelAllFn | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._cancel_all_fn = cancel_all_fn
        self._lock = asyncio.Lock()

        self._breakers: dict[str, CircuitBreakerConfig] = {}
        self._states: dict[str, CircuitBreakerState] = {}
        self._history: list[CircuitBreakerEvent] = []
        self._consecutive_losses: int = 0
        self._recovery_tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_breaker(self, config: CircuitBreakerConfig) -> None:
        """Register a named circuit breaker.

        If a breaker with the same *name* already exists it is replaced and
        its state is reset to ``CLOSED``.
        """
        self._breakers[config.name] = config
        self._states[config.name] = CircuitBreakerState.CLOSED
        # Cancel any pending recovery task for a replaced breaker
        task = self._recovery_tasks.pop(config.name, None)
        if task is not None and not task.done():
            task.cancel()
        logger.info("Circuit breaker '%s' registered", config.name)

    def remove_breaker(self, name: str) -> None:
        """Remove a named circuit breaker and cancel its recovery task."""
        self._breakers.pop(name, None)
        self._states.pop(name, None)
        task = self._recovery_tasks.pop(name, None)
        if task is not None and not task.done():
            task.cancel()
        logger.info("Circuit breaker '%s' removed", name)

    # ------------------------------------------------------------------
    # Core check / trigger / recover
    # ------------------------------------------------------------------

    async def check(
        self,
        daily_loss: float = 0.0,
        daily_loss_pct: float = 0.0,
        consecutive_losses: int = 0,
        drawdown_pct: float = 0.0,
    ) -> bool:
        """Evaluate all registered breakers against the supplied metrics.

        Args:
            daily_loss: Absolute daily P&L (negative means loss).
            daily_loss_pct: Daily loss as a percentage of capital (positive = loss).
            consecutive_losses: Current streak of consecutive losing trades.
            drawdown_pct: Current portfolio drawdown percentage.

        Returns:
            ``True`` if **any** breaker was triggered during this call.
        """
        any_triggered = False

        for name, config in self._breakers.items():
            if self._states.get(name) == CircuitBreakerState.OPEN:
                continue  # Already tripped

            reason = self._evaluate_breaker(
                config,
                daily_loss=daily_loss,
                daily_loss_pct=daily_loss_pct,
                consecutive_losses=consecutive_losses,
                drawdown_pct=drawdown_pct,
            )
            if reason is not None:
                metrics = {
                    "daily_loss": daily_loss,
                    "daily_loss_pct": daily_loss_pct,
                    "consecutive_losses": consecutive_losses,
                    "drawdown_pct": drawdown_pct,
                }
                await self.trigger(name, reason, metrics)
                any_triggered = True

        return any_triggered

    async def trigger(
        self,
        breaker_name: str,
        reason: str,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Manually (or internally) trigger a specific breaker.

        Transitions the breaker to ``OPEN``, records the event, optionally
        cancels open orders, emits an alert, and schedules auto-recovery if
        configured.
        """
        async with self._lock:
            config = self._breakers.get(breaker_name)
            if config is None:
                logger.warning(
                    "Cannot trigger unknown breaker '%s'", breaker_name
                )
                return

            if self._states.get(breaker_name) == CircuitBreakerState.OPEN:
                logger.debug("Breaker '%s' already OPEN", breaker_name)
                return

            self._states[breaker_name] = CircuitBreakerState.OPEN
            now = datetime.now(timezone.utc)

            event = CircuitBreakerEvent(
                breaker_name=breaker_name,
                trigger_reason=reason,
                triggered_at=now,
                state=CircuitBreakerState.OPEN,
                metrics_at_trigger=metrics or {},
            )
            self._history.append(event)

            logger.critical(
                "CIRCUIT BREAKER '%s' TRIGGERED: %s  |  metrics=%s",
                breaker_name,
                reason,
                metrics,
            )

        # Cancel open orders (outside lock to avoid deadlock with broker calls)
        if config.cancel_open_orders and self._cancel_all_fn is not None:
            try:
                await self._cancel_all_fn()
                logger.info(
                    "Open orders cancelled by breaker '%s'", breaker_name
                )
            except Exception:
                logger.exception(
                    "Failed to cancel orders for breaker '%s'", breaker_name
                )

        # Emit alert
        await self._emit_breaker_event(event)

        # Schedule auto-recovery
        if config.auto_recover and config.cooldown_seconds > 0:
            await self._schedule_recovery(breaker_name, config.cooldown_seconds)

    async def recover(self, breaker_name: str) -> None:
        """Move a breaker back to ``CLOSED`` (manual or auto recovery)."""
        async with self._lock:
            if breaker_name not in self._breakers:
                logger.warning(
                    "Cannot recover unknown breaker '%s'", breaker_name
                )
                return

            prev_state = self._states.get(breaker_name)
            if prev_state == CircuitBreakerState.CLOSED:
                logger.debug("Breaker '%s' already CLOSED", breaker_name)
                return

            self._states[breaker_name] = CircuitBreakerState.CLOSED
            now = datetime.now(timezone.utc)

            # Update the most recent history entry for this breaker
            for evt in reversed(self._history):
                if evt.breaker_name == breaker_name and evt.recovered_at is None:
                    evt.recovered_at = now
                    evt.state = CircuitBreakerState.CLOSED
                    break

            logger.info("Circuit breaker '%s' recovered to CLOSED", breaker_name)

        # Cancel pending recovery task if one exists
        task = self._recovery_tasks.pop(breaker_name, None)
        if task is not None and not task.done():
            task.cancel()

        # Emit recovery alert
        recovery_event = CircuitBreakerEvent(
            breaker_name=breaker_name,
            trigger_reason="recovery",
            triggered_at=now,
            state=CircuitBreakerState.CLOSED,
            recovered_at=now,
        )
        await self._emit_breaker_event(recovery_event)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_trading_allowed(self) -> bool:
        """Return ``True`` if **all** breakers are ``CLOSED``."""
        if not self._breakers:
            return True
        return all(
            st == CircuitBreakerState.CLOSED
            for st in self._states.values()
        )

    def get_state(self, breaker_name: str) -> CircuitBreakerState | None:
        """Return the current state of a named breaker, or ``None``."""
        return self._states.get(breaker_name)

    def get_triggered_breakers(self) -> list[str]:
        """Return the names of all breakers currently in ``OPEN`` state."""
        return [
            name
            for name, state in self._states.items()
            if state == CircuitBreakerState.OPEN
        ]

    @property
    def history(self) -> list[CircuitBreakerEvent]:
        """Full history of breaker trigger / recovery events."""
        return list(self._history)

    # ------------------------------------------------------------------
    # Consecutive-loss tracking
    # ------------------------------------------------------------------

    def record_trade_result(self, is_profitable: bool) -> None:
        """Update the consecutive-loss counter based on the latest trade.

        Call this after every trade fill.  The counter is used by
        ``max_consecutive_losses`` breakers during :meth:`check`.
        """
        if is_profitable:
            if self._consecutive_losses > 0:
                logger.debug(
                    "Consecutive-loss streak of %d broken by winning trade",
                    self._consecutive_losses,
                )
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            logger.debug(
                "Consecutive losses: %d", self._consecutive_losses
            )

    @property
    def consecutive_losses(self) -> int:
        """Current streak of consecutive losing trades."""
        return self._consecutive_losses

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate_breaker(
        config: CircuitBreakerConfig,
        *,
        daily_loss: float,
        daily_loss_pct: float,
        consecutive_losses: int,
        drawdown_pct: float,
    ) -> str | None:
        """Return a reason string if any threshold in *config* is breached,
        otherwise ``None``.

        ``daily_loss`` is expected to be negative when there is a loss.
        ``daily_loss_pct`` and ``drawdown_pct`` are expected to be positive
        numbers representing the magnitude of the loss / drawdown.
        """
        if config.max_loss_amount > 0 and daily_loss < 0:
            if abs(daily_loss) >= config.max_loss_amount:
                return (
                    f"Daily loss {daily_loss:.2f} exceeds "
                    f"limit {config.max_loss_amount:.2f}"
                )

        if config.max_loss_pct > 0 and daily_loss_pct > 0:
            if daily_loss_pct >= config.max_loss_pct:
                return (
                    f"Daily loss {daily_loss_pct:.2f}% exceeds "
                    f"limit {config.max_loss_pct:.2f}%"
                )

        if config.max_consecutive_losses > 0 and consecutive_losses > 0:
            if consecutive_losses >= config.max_consecutive_losses:
                return (
                    f"Consecutive losses {consecutive_losses} reached "
                    f"limit {config.max_consecutive_losses}"
                )

        if config.max_drawdown_pct > 0 and drawdown_pct > 0:
            if drawdown_pct >= config.max_drawdown_pct:
                return (
                    f"Drawdown {drawdown_pct:.2f}% exceeds "
                    f"limit {config.max_drawdown_pct:.2f}%"
                )

        return None

    async def _schedule_recovery(
        self, breaker_name: str, cooldown: float
    ) -> None:
        """Schedule an auto-recovery after *cooldown* seconds."""
        # Cancel any existing recovery task for this breaker
        existing = self._recovery_tasks.pop(breaker_name, None)
        if existing is not None and not existing.done():
            existing.cancel()

        async def _do_recover() -> None:
            try:
                await asyncio.sleep(cooldown)
                logger.info(
                    "Auto-recovering breaker '%s' after %.1fs cooldown",
                    breaker_name,
                    cooldown,
                )
                await self.recover(breaker_name)
            except asyncio.CancelledError:
                logger.debug(
                    "Recovery task for breaker '%s' cancelled", breaker_name
                )

        task = asyncio.ensure_future(_do_recover())
        self._recovery_tasks[breaker_name] = task

    async def _emit_breaker_event(
        self, event: CircuitBreakerEvent
    ) -> None:
        """Publish a circuit-breaker event to the event bus."""
        is_trigger = event.state == CircuitBreakerState.OPEN
        level = AlertLevel.CRITICAL if is_trigger else AlertLevel.INFO
        action = "TRIGGERED" if is_trigger else "RECOVERED"

        alert = Alert(
            level=level,
            source="CircuitBreaker",
            message=(
                f"Circuit breaker '{event.breaker_name}' {action}: "
                f"{event.trigger_reason}"
            ),
            timestamp=event.triggered_at,
            data={
                "breaker_name": event.breaker_name,
                "state": event.state.value,
                "trigger_reason": event.trigger_reason,
                "triggered_at": event.triggered_at.isoformat(),
                "recovered_at": (
                    event.recovered_at.isoformat()
                    if event.recovered_at
                    else None
                ),
                "metrics_at_trigger": event.metrics_at_trigger,
            },
        )

        topic = Topics.RISK_BREACH if is_trigger else Topics.RISK_ALERTS
        priority = (
            EventPriority.CRITICAL if is_trigger else EventPriority.NORMAL
        )

        try:
            await self._event_bus.publish(
                topic=topic,
                event_type=f"CIRCUIT_BREAKER_{action}",
                payload={"alert": alert.model_dump(mode="json")},
                priority=priority,
                source="CircuitBreaker",
            )
        except Exception:
            logger.exception(
                "Failed to emit circuit breaker event for '%s'",
                event.breaker_name,
            )
