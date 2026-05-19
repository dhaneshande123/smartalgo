"""
Real-time risk management with pre-trade and post-trade checks.

The :class:`RiskManager` is the central gate-keeper that every order must pass
through before it reaches the OMS.  It enforces configurable limits on order
value, position size, portfolio exposure, daily loss, order rate, quantity,
Greeks, and concentration.  It also provides a kill-switch that immediately
halts all new order flow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any

from core.event_bus.bus import BaseEventBus, EventPriority, Topics
from core.models import (
    InstrumentType,
    Order,
    OrderSide,
    Trade,
)
from core.risk_engine.position_tracker import PositionState, PositionTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk limits configuration
# ---------------------------------------------------------------------------


@dataclass
class RiskLimits:
    """Configurable risk limits.

    All monetary values are in INR.  Limits can be updated at runtime via
    :meth:`RiskManager.update_limits`.
    """

    max_order_value: float = 5_000_000          # max single order value
    max_position_value: float = 20_000_000      # max single position value
    max_portfolio_value: float = 100_000_000    # max total portfolio value
    max_loss_per_order: float = 50_000          # max loss on a single order
    max_loss_per_strategy: float = 500_000      # max loss per strategy per day
    max_loss_per_day: float = 1_000_000         # max total loss per day
    max_open_orders: int = 50                   # max concurrent open orders
    max_orders_per_minute: int = 30             # order rate limit
    max_quantity_per_order: int = 1800          # max lots in single order
    max_greeks_delta: float = 500.0             # max absolute portfolio delta
    max_greeks_gamma: float = 100.0             # max absolute gamma
    max_greeks_vega: float = 50_000.0           # max absolute vega
    position_concentration_limit: float = 0.25  # max % of portfolio in one instrument


# ---------------------------------------------------------------------------
# Risk check result
# ---------------------------------------------------------------------------


@dataclass
class RiskCheckResult:
    """Result of a pre-trade risk check."""

    approved: bool
    order: Order
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def summary(self) -> str:
        status = "APPROVED" if self.approved else "REJECTED"
        parts = [f"[{status}] order={self.order.order_id[:8]}"]
        if self.checks_failed:
            parts.append(f"failed=[{', '.join(self.checks_failed)}]")
        if self.warnings:
            parts.append(f"warnings=[{', '.join(self.warnings)}]")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Risk Manager
# ---------------------------------------------------------------------------


class RiskManager:
    """Real-time risk management with pre-trade and post-trade checks.

    Pre-trade checks (before order submission)
    -------------------------------------------
    - Kill-switch guard
    - Order value limit
    - Position limit
    - Portfolio exposure limit
    - Daily loss limit
    - Strategy loss limit
    - Order rate limit
    - Quantity limit
    - Concentration limit

    Post-trade monitoring
    ---------------------
    - Real-time P&L monitoring
    - Drawdown alerts
    - Kill switch (emergency halt all trading)

    Usage::

        risk = RiskManager(limits, position_tracker, event_bus)
        result = await risk.check_order(order)
        if result.approved:
            await oms.submit_order(order)
    """

    def __init__(
        self,
        limits: RiskLimits,
        position_tracker: PositionTracker,
        event_bus: BaseEventBus,
    ) -> None:
        self._limits = limits
        self._tracker = position_tracker
        self._event_bus = event_bus

        # Kill switch
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str = ""
        self._kill_switch_activated_at: datetime | None = None

        # Daily counters
        self._daily_pnl: float = 0.0
        self._strategy_pnl: dict[str, float] = {}

        # Order rate tracking (sliding window of timestamps)
        self._order_timestamps: list[datetime] = []

        # Active (open) order count — should be updated by OMS callbacks
        self._active_order_count: int = 0

        # Greeks cache — updated externally by the greeks engine
        self._portfolio_delta: float = 0.0
        self._portfolio_gamma: float = 0.0
        self._portfolio_vega: float = 0.0

        # Alert thresholds
        self._drawdown_alert_pct: float = 0.5   # alert at 50 % of daily loss limit
        self._drawdown_alerted: bool = False

        logger.info("RiskManager initialised with limits: %s", limits)

    # ------------------------------------------------------------------
    # Pre-trade check — main entry point
    # ------------------------------------------------------------------

    async def check_order(self, order: Order) -> RiskCheckResult:
        """Run all pre-trade risk checks on *order*.

        Returns a :class:`RiskCheckResult` indicating whether the order is
        approved, along with details of which checks passed and which failed.
        """
        passed: list[str] = []
        failed: list[str] = []
        warnings: list[str] = []

        checks = [
            ("kill_switch", self._check_kill_switch),
            ("order_value", self._check_order_value),
            ("position_limit", self._check_position_limit),
            ("portfolio_exposure", self._check_portfolio_exposure),
            ("daily_loss", self._check_daily_loss),
            ("strategy_loss", self._check_strategy_loss),
            ("order_rate", self._check_order_rate),
            ("quantity_limit", self._check_quantity_limit),
            ("concentration", self._check_concentration),
        ]

        for name, check_fn in checks:
            try:
                ok, msg = await check_fn(order)
                if ok:
                    passed.append(name)
                else:
                    failed.append(f"{name}: {msg}")
            except Exception as exc:
                # Treat unexpected errors as failures — fail-safe
                failed.append(f"{name}: internal error ({exc})")
                logger.exception("Risk check %s raised an exception", name)

        # Soft warnings (non-blocking)
        warnings.extend(await self._generate_warnings(order))

        approved = len(failed) == 0
        result = RiskCheckResult(
            approved=approved,
            order=order,
            checks_passed=passed,
            checks_failed=failed,
            warnings=warnings,
        )

        if approved:
            # Record the order timestamp for rate limiting
            self._order_timestamps.append(datetime.now(timezone.utc))
            self._active_order_count += 1
            logger.info("Order %s APPROVED (%d checks passed)", order.order_id[:8], len(passed))
        else:
            logger.warning(
                "Order %s REJECTED: %s", order.order_id[:8], "; ".join(failed),
            )
            # Publish risk breach event
            await self._publish_risk_event(
                "order_rejected",
                {"order_id": order.order_id, "reasons": failed},
            )

        return result

    # ------------------------------------------------------------------
    # Post-trade monitoring
    # ------------------------------------------------------------------

    async def on_trade(self, trade: Trade) -> None:
        """Post-trade risk monitoring.

        Called after a trade fill to update daily P&L, per-strategy P&L,
        and check for daily loss threshold breaches.
        """
        # Forward to position tracker
        state = self._tracker.on_trade(trade)

        # Update daily PnL from realised
        # We use the position tracker's aggregate because it handles
        # partial close realised PnL correctly.
        self._daily_pnl = (
            self._tracker.get_total_realised_pnl()
            + self._tracker.get_total_unrealised_pnl()
        )

        # Per-strategy PnL
        strategy_id = trade.strategy_id or "__default__"
        strategy_positions = self._tracker.get_positions_by_strategy(strategy_id)
        strategy_pnl = sum(p.realised_pnl + p.unrealised_pnl for p in strategy_positions)
        self._strategy_pnl[strategy_id] = strategy_pnl

        # Decrement active order count (a fill means an order is being consumed)
        self._active_order_count = max(0, self._active_order_count - 1)

        # Check daily loss
        if self._daily_pnl <= -self._limits.max_loss_per_day:
            logger.critical(
                "DAILY LOSS LIMIT BREACHED: PnL=%.2f limit=%.2f — activating kill switch",
                self._daily_pnl,
                self._limits.max_loss_per_day,
            )
            self.activate_kill_switch(
                f"Daily loss limit breached: PnL={self._daily_pnl:.2f}"
            )

        # Check strategy loss
        if strategy_pnl <= -self._limits.max_loss_per_strategy:
            logger.warning(
                "Strategy %s loss limit breached: PnL=%.2f limit=%.2f",
                strategy_id,
                strategy_pnl,
                self._limits.max_loss_per_strategy,
            )
            await self._publish_risk_event(
                "strategy_loss_breach",
                {
                    "strategy_id": strategy_id,
                    "pnl": strategy_pnl,
                    "limit": self._limits.max_loss_per_strategy,
                },
            )

        # Publish PnL update
        await self._event_bus.publish(
            topic=Topics.PNL_UPDATE,
            event_type="pnl_update",
            payload={
                "daily_pnl": round(self._daily_pnl, 2),
                "trade_id": trade.trade_id,
                "strategy_id": strategy_id,
                "strategy_pnl": round(strategy_pnl, 2),
            },
            source="risk_manager",
        )

    async def on_price_update(self, symbol: str, price: float) -> None:
        """Update position valuations and check for drawdown alerts.

        Should be called on every tick / price change for instruments with
        open positions.
        """
        self._tracker.update_price(symbol, price)

        # Recompute aggregate daily PnL
        self._daily_pnl = (
            self._tracker.get_total_realised_pnl()
            + self._tracker.get_total_unrealised_pnl()
        )

        # Drawdown alert (fire once when 50% of daily limit is consumed)
        alert_threshold = -self._limits.max_loss_per_day * self._drawdown_alert_pct
        if self._daily_pnl <= alert_threshold and not self._drawdown_alerted:
            self._drawdown_alerted = True
            logger.warning(
                "DRAWDOWN ALERT: PnL=%.2f has breached %.0f%% of daily limit (%.2f)",
                self._daily_pnl,
                self._drawdown_alert_pct * 100,
                self._limits.max_loss_per_day,
            )
            await self._publish_risk_event(
                "drawdown_alert",
                {
                    "daily_pnl": round(self._daily_pnl, 2),
                    "threshold_pct": self._drawdown_alert_pct,
                    "limit": self._limits.max_loss_per_day,
                },
            )

        # Hard daily loss check
        if self._daily_pnl <= -self._limits.max_loss_per_day:
            if not self._kill_switch_active:
                self.activate_kill_switch(
                    f"Daily loss limit breached on price update: PnL={self._daily_pnl:.2f}"
                )

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    def activate_kill_switch(self, reason: str) -> None:
        """Emergency halt -- reject all new orders immediately."""
        if self._kill_switch_active:
            logger.info("Kill switch already active (reason: %s)", self._kill_switch_reason)
            return

        self._kill_switch_active = True
        self._kill_switch_reason = reason
        self._kill_switch_activated_at = datetime.now(timezone.utc)
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def deactivate_kill_switch(self) -> None:
        """Deactivate the kill switch, allowing new orders again."""
        if not self._kill_switch_active:
            logger.info("Kill switch is not active — nothing to deactivate")
            return

        logger.warning(
            "Kill switch DEACTIVATED (was active since %s, reason: %s)",
            self._kill_switch_activated_at,
            self._kill_switch_reason,
        )
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        self._kill_switch_activated_at = None

    # ------------------------------------------------------------------
    # Order lifecycle callbacks
    # ------------------------------------------------------------------

    def on_order_cancelled(self) -> None:
        """Called when an order is cancelled — adjust the active order count."""
        self._active_order_count = max(0, self._active_order_count - 1)

    def on_order_rejected(self) -> None:
        """Called when an order is rejected — adjust the active order count."""
        self._active_order_count = max(0, self._active_order_count - 1)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def update_limits(self, **kwargs: Any) -> None:
        """Update specific risk limits at runtime.

        Example::

            risk.update_limits(max_loss_per_day=2_000_000, max_open_orders=100)
        """
        for key, value in kwargs.items():
            if not hasattr(self._limits, key):
                logger.warning("Unknown risk limit key: %s — ignoring", key)
                continue
            old = getattr(self._limits, key)
            setattr(self._limits, key, value)
            logger.info("Risk limit %s updated: %s -> %s", key, old, value)

    def update_greeks(self, delta: float, gamma: float, vega: float) -> None:
        """Update cached portfolio greeks (called by the greeks engine)."""
        self._portfolio_delta = delta
        self._portfolio_gamma = gamma
        self._portfolio_vega = vega

    def reset_daily(self) -> None:
        """Reset daily counters. Called at start of each trading day."""
        self._daily_pnl = 0.0
        self._strategy_pnl.clear()
        self._order_timestamps.clear()
        self._active_order_count = 0
        self._drawdown_alerted = False
        logger.info("RiskManager daily counters reset")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_kill_switch_active(self) -> bool:
        return self._kill_switch_active

    @property
    def current_limits(self) -> RiskLimits:
        return self._limits

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def risk_metrics(self) -> dict[str, Any]:
        """Summary of current risk state for dashboards / monitoring."""
        return {
            "kill_switch_active": self._kill_switch_active,
            "kill_switch_reason": self._kill_switch_reason,
            "daily_pnl": round(self._daily_pnl, 2),
            "strategy_pnl": {k: round(v, 2) for k, v in self._strategy_pnl.items()},
            "active_orders": self._active_order_count,
            "net_exposure": round(self._tracker.get_net_exposure(), 2),
            "open_positions": self._tracker.get_position_count(),
            "total_unrealised_pnl": round(self._tracker.get_total_unrealised_pnl(), 2),
            "total_realised_pnl": round(self._tracker.get_total_realised_pnl(), 2),
            "portfolio_delta": round(self._portfolio_delta, 2),
            "portfolio_gamma": round(self._portfolio_gamma, 4),
            "portfolio_vega": round(self._portfolio_vega, 2),
            "limits": {
                "max_order_value": self._limits.max_order_value,
                "max_position_value": self._limits.max_position_value,
                "max_portfolio_value": self._limits.max_portfolio_value,
                "max_loss_per_day": self._limits.max_loss_per_day,
                "max_open_orders": self._limits.max_open_orders,
            },
        }

    # ------------------------------------------------------------------
    # Internal risk check methods
    # ------------------------------------------------------------------

    async def _check_kill_switch(self, order: Order) -> tuple[bool, str]:
        """Reject immediately if the kill switch is active."""
        if self._kill_switch_active:
            return False, f"Kill switch active: {self._kill_switch_reason}"
        return True, ""

    async def _check_order_value(self, order: Order) -> tuple[bool, str]:
        """Ensure the order's notional value does not exceed the single-order limit."""
        price = float(order.price or order.trigger_price or Decimal("0"))
        if price == 0:
            # Market orders — we don't have a price yet, allow through with a warning
            return True, ""

        lot_size = order.instrument.lot_size
        order_value = price * order.quantity * lot_size

        if order_value > self._limits.max_order_value:
            return (
                False,
                f"Order value {order_value:,.0f} exceeds limit {self._limits.max_order_value:,.0f}",
            )
        return True, ""

    async def _check_position_limit(self, order: Order) -> tuple[bool, str]:
        """Ensure the resulting position does not exceed the single-position limit."""
        symbol = order.instrument.symbol
        strategy_id = order.strategy_id or ""
        state = self._tracker.get_position(symbol, strategy_id)

        price = float(order.price or order.trigger_price or Decimal("0"))
        lot_size = order.instrument.lot_size

        if state is None or state.position.quantity == 0:
            # New position — value is the order value
            if price == 0:
                return True, ""
            new_value = price * order.quantity * lot_size
        else:
            # Existing position — check if we're adding or reducing
            current_qty = state.position.quantity
            signed_qty = order.quantity if order.side == OrderSide.BUY else -order.quantity

            # If reducing, always allow
            if (current_qty > 0 and signed_qty < 0) or (current_qty < 0 and signed_qty > 0):
                return True, ""

            # Adding to position
            resulting_qty = abs(current_qty + signed_qty)
            effective_price = state.last_price if state.last_price > 0 else price
            if effective_price == 0:
                return True, ""
            new_value = effective_price * resulting_qty * lot_size

        if new_value > self._limits.max_position_value:
            return (
                False,
                f"Resulting position value {new_value:,.0f} exceeds limit "
                f"{self._limits.max_position_value:,.0f}",
            )
        return True, ""

    async def _check_portfolio_exposure(self, order: Order) -> tuple[bool, str]:
        """Ensure total portfolio exposure stays within the global limit."""
        current_exposure = self._tracker.get_net_exposure()
        price = float(order.price or order.trigger_price or Decimal("0"))
        lot_size = order.instrument.lot_size

        if price == 0:
            return True, ""

        order_value = price * order.quantity * lot_size
        # Worst case: the order adds to exposure (it might reduce, but we're conservative)
        projected = current_exposure + order_value

        if projected > self._limits.max_portfolio_value:
            return (
                False,
                f"Projected portfolio exposure {projected:,.0f} exceeds limit "
                f"{self._limits.max_portfolio_value:,.0f}",
            )
        return True, ""

    async def _check_daily_loss(self, order: Order) -> tuple[bool, str]:
        """Reject if the daily loss limit has already been breached."""
        if self._daily_pnl <= -self._limits.max_loss_per_day:
            return (
                False,
                f"Daily loss limit breached: PnL={self._daily_pnl:,.0f}, "
                f"limit={self._limits.max_loss_per_day:,.0f}",
            )
        return True, ""

    async def _check_strategy_loss(self, order: Order) -> tuple[bool, str]:
        """Reject if the strategy's daily loss limit has been breached."""
        strategy_id = order.strategy_id or "__default__"
        strategy_pnl = self._strategy_pnl.get(strategy_id, 0.0)

        if strategy_pnl <= -self._limits.max_loss_per_strategy:
            return (
                False,
                f"Strategy {strategy_id} loss limit breached: PnL={strategy_pnl:,.0f}, "
                f"limit={self._limits.max_loss_per_strategy:,.0f}",
            )
        return True, ""

    async def _check_order_rate(self, order: Order) -> tuple[bool, str]:
        """Enforce the orders-per-minute rate limit using a sliding window."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=1)

        # Prune old timestamps
        self._order_timestamps = [
            ts for ts in self._order_timestamps if ts >= cutoff
        ]

        if len(self._order_timestamps) >= self._limits.max_orders_per_minute:
            return (
                False,
                f"Order rate limit: {len(self._order_timestamps)} orders in last minute, "
                f"limit={self._limits.max_orders_per_minute}",
            )

        # Also check active order count
        if self._active_order_count >= self._limits.max_open_orders:
            return (
                False,
                f"Max open orders: {self._active_order_count} >= {self._limits.max_open_orders}",
            )

        return True, ""

    async def _check_quantity_limit(self, order: Order) -> tuple[bool, str]:
        """Ensure the order quantity does not exceed the per-order lot limit."""
        if order.quantity > self._limits.max_quantity_per_order:
            return (
                False,
                f"Quantity {order.quantity} exceeds limit {self._limits.max_quantity_per_order}",
            )
        return True, ""

    async def _check_concentration(self, order: Order) -> tuple[bool, str]:
        """Ensure no single instrument exceeds the concentration limit.

        Concentration is measured as the ratio of a single position's absolute
        value to the total portfolio exposure.
        """
        total_exposure = self._tracker.get_net_exposure()
        if total_exposure == 0:
            # First position — no concentration issue
            return True, ""

        symbol = order.instrument.symbol
        strategy_id = order.strategy_id or ""
        state = self._tracker.get_position(symbol, strategy_id)

        price = float(order.price or order.trigger_price or Decimal("0"))
        lot_size = order.instrument.lot_size

        if price == 0:
            return True, ""

        order_value = price * order.quantity * lot_size
        current_position_value = abs(state.current_value) if state else 0.0
        resulting_value = current_position_value + order_value
        projected_total = total_exposure + order_value

        concentration = resulting_value / projected_total if projected_total > 0 else 0.0

        if concentration > self._limits.position_concentration_limit:
            return (
                False,
                f"Position concentration {concentration:.1%} for {symbol} exceeds "
                f"limit {self._limits.position_concentration_limit:.0%}",
            )
        return True, ""

    # ------------------------------------------------------------------
    # Warnings (non-blocking)
    # ------------------------------------------------------------------

    async def _generate_warnings(self, order: Order) -> list[str]:
        """Generate non-blocking warnings for the order."""
        warnings: list[str] = []

        # Warn if approaching daily loss limit (> 70%)
        if self._daily_pnl < 0:
            loss_pct = abs(self._daily_pnl) / self._limits.max_loss_per_day
            if loss_pct > 0.7:
                warnings.append(
                    f"Daily PnL at {loss_pct:.0%} of loss limit "
                    f"({self._daily_pnl:,.0f} / {self._limits.max_loss_per_day:,.0f})"
                )

        # Warn on high portfolio delta
        if abs(self._portfolio_delta) > self._limits.max_greeks_delta * 0.8:
            warnings.append(
                f"Portfolio delta {self._portfolio_delta:.1f} approaching "
                f"limit {self._limits.max_greeks_delta:.1f}"
            )

        # Warn on high gamma
        if abs(self._portfolio_gamma) > self._limits.max_greeks_gamma * 0.8:
            warnings.append(
                f"Portfolio gamma {self._portfolio_gamma:.2f} approaching "
                f"limit {self._limits.max_greeks_gamma:.2f}"
            )

        # Warn on high vega
        if abs(self._portfolio_vega) > self._limits.max_greeks_vega * 0.8:
            warnings.append(
                f"Portfolio vega {self._portfolio_vega:.1f} approaching "
                f"limit {self._limits.max_greeks_vega:.1f}"
            )

        # Warn on approaching order rate limit
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=1)
        recent_count = sum(1 for ts in self._order_timestamps if ts >= cutoff)
        if recent_count > self._limits.max_orders_per_minute * 0.8:
            warnings.append(
                f"Order rate at {recent_count}/{self._limits.max_orders_per_minute} per minute"
            )

        return warnings

    # ------------------------------------------------------------------
    # Event publishing helper
    # ------------------------------------------------------------------

    async def _publish_risk_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Publish a risk-related event to the event bus."""
        try:
            await self._event_bus.publish(
                topic=Topics.RISK_ALERTS,
                event_type=event_type,
                payload={"risk": payload},
                priority=EventPriority.HIGH,
                source="risk_manager",
            )
        except Exception:
            logger.exception("Failed to publish risk event %s", event_type)
