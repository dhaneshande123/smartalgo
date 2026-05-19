"""
Real-time drawdown monitoring at position, strategy, and portfolio levels.

Tracks peak-to-trough drawdown for every monitored entity, emits alerts
when configurable percentage thresholds are breached, and records maximum
historical drawdown for post-session analysis.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.event_bus.bus import BaseEventBus, EventPriority, Topics
from core.models import Alert, AlertLevel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DrawdownState:
    """Tracks drawdown metrics for a single monitored entity."""

    entity_id: str  # position key, strategy_id, or "PORTFOLIO"
    entity_type: str  # "position", "strategy", "portfolio"

    peak_value: float  # highest value seen
    trough_value: float  # lowest value since last peak
    current_value: float

    current_drawdown: float  # peak - current (absolute, >= 0)
    current_drawdown_pct: float  # drawdown as % of peak (0-100 range)

    max_drawdown: float  # worst-ever absolute drawdown
    max_drawdown_pct: float  # worst-ever % drawdown

    peak_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trough_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    recovery_time: datetime | None = None  # when drawdown recovered to zero

    in_drawdown: bool = False  # currently below peak?

    # Internal bookkeeping: which thresholds have already fired an alert
    _alerted_thresholds: set[float] = field(default_factory=set, repr=False)


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class DrawdownMonitor:
    """Real-time drawdown monitoring with configurable alert thresholds.

    Monitors three levels:
      1. **Position-level** drawdown (per instrument / position key)
      2. **Strategy-level** drawdown (aggregated P&L per strategy)
      3. **Portfolio-level** drawdown (total portfolio value / P&L)

    Features:
      - Emits :class:`Alert` objects via the event bus at configurable
        drawdown-percentage thresholds.
      - Tracks maximum drawdown history for every entity.
      - Recovery detection: clears ``in_drawdown`` and records ``recovery_time``
        when the value returns to a new peak.
      - Configurable cooldown period between repeated alerts for the **same**
        entity so the bus is not flooded.

    Usage::

        monitor = DrawdownMonitor(event_bus)
        monitor.set_alert_thresholds(
            strategy_pct=[5, 10, 20],
            portfolio_pct=[3, 5, 10],
        )
        monitor.update_value("strat_1", "strategy", current_pnl)
    """

    # Valid entity types
    _VALID_ENTITY_TYPES = {"position", "strategy", "portfolio"}

    def __init__(
        self,
        event_bus: BaseEventBus,
        alert_cooldown_seconds: float = 300.0,
    ) -> None:
        self._event_bus = event_bus
        self._alert_cooldown = alert_cooldown_seconds
        self._lock = asyncio.Lock()

        # entity_id -> DrawdownState
        self._states: dict[str, DrawdownState] = {}

        # Configurable thresholds per entity type (percentage values, e.g. 5.0 = 5 %)
        self._thresholds: dict[str, list[float]] = {
            "position": [5.0, 10.0, 20.0],
            "strategy": [5.0, 10.0, 15.0, 25.0],
            "portfolio": [2.0, 5.0, 8.0, 10.0],
        }

        # (entity_id, threshold_pct) -> last alert UTC time
        self._last_alert_time: dict[tuple[str, float], datetime] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_value(
        self,
        entity_id: str,
        entity_type: str,
        current_value: float,
    ) -> DrawdownState:
        """Update the current value for *entity_id* and recompute drawdown.

        If the entity has not been seen before a new :class:`DrawdownState` is
        created with *current_value* as the initial peak.

        Args:
            entity_id: Unique identifier (position key, strategy id, or
                ``"PORTFOLIO"``).
            entity_type: One of ``"position"``, ``"strategy"``, ``"portfolio"``.
            current_value: Current P&L or value for this entity.

        Returns:
            The updated :class:`DrawdownState`.
        """
        if entity_type not in self._VALID_ENTITY_TYPES:
            raise ValueError(
                f"Invalid entity_type {entity_type!r}; "
                f"expected one of {self._VALID_ENTITY_TYPES}"
            )

        now = datetime.now(timezone.utc)

        state = self._states.get(entity_id)
        if state is None:
            state = DrawdownState(
                entity_id=entity_id,
                entity_type=entity_type,
                peak_value=current_value,
                trough_value=current_value,
                current_value=current_value,
                current_drawdown=0.0,
                current_drawdown_pct=0.0,
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                peak_time=now,
                trough_time=now,
                recovery_time=None,
                in_drawdown=False,
            )
            self._states[entity_id] = state
            logger.debug(
                "Drawdown tracking started for %s (%s) at value %.4f",
                entity_id,
                entity_type,
                current_value,
            )
            return state

        state.current_value = current_value

        # --- New peak? ---
        if current_value >= state.peak_value:
            if state.in_drawdown:
                # Recovery detected
                state.recovery_time = now
                logger.info(
                    "Drawdown recovery for %s (%s): value %.4f reached new peak",
                    entity_id,
                    entity_type,
                    current_value,
                )
            state.peak_value = current_value
            state.peak_time = now
            state.trough_value = current_value
            state.trough_time = now
            state.current_drawdown = 0.0
            state.current_drawdown_pct = 0.0
            state.in_drawdown = False
            # Clear fired thresholds so they can fire again on the next drawdown
            state._alerted_thresholds.clear()
            return state

        # --- In drawdown ---
        state.in_drawdown = True
        state.recovery_time = None

        # Track trough
        if current_value < state.trough_value:
            state.trough_value = current_value
            state.trough_time = now

        # Compute current drawdown (absolute, always >= 0)
        state.current_drawdown = state.peak_value - current_value

        # Compute drawdown percentage (relative to peak)
        if state.peak_value != 0.0:
            state.current_drawdown_pct = (
                state.current_drawdown / abs(state.peak_value)
            ) * 100.0
        else:
            state.current_drawdown_pct = 0.0

        # Update max drawdown if this is the worst yet
        if state.current_drawdown > state.max_drawdown:
            state.max_drawdown = state.current_drawdown
        if state.current_drawdown_pct > state.max_drawdown_pct:
            state.max_drawdown_pct = state.current_drawdown_pct

        # Check thresholds and schedule alerts
        self._check_thresholds(state, now)

        return state

    def set_alert_thresholds(
        self,
        position_pct: list[float] | None = None,
        strategy_pct: list[float] | None = None,
        portfolio_pct: list[float] | None = None,
    ) -> None:
        """Override the default drawdown-percentage alert thresholds.

        Each list should contain ascending percentage values (e.g. ``[5, 10, 20]``).
        Only the supplied arguments are overwritten; pass ``None`` to keep the
        current thresholds for that entity type.
        """
        if position_pct is not None:
            self._thresholds["position"] = sorted(position_pct)
        if strategy_pct is not None:
            self._thresholds["strategy"] = sorted(strategy_pct)
        if portfolio_pct is not None:
            self._thresholds["portfolio"] = sorted(portfolio_pct)
        logger.info("Drawdown alert thresholds updated: %s", self._thresholds)

    def get_state(self, entity_id: str) -> DrawdownState | None:
        """Return the current :class:`DrawdownState` for *entity_id*, or ``None``."""
        return self._states.get(entity_id)

    def get_all_states(self) -> list[DrawdownState]:
        """Return a list of all tracked :class:`DrawdownState` objects."""
        return list(self._states.values())

    def get_max_portfolio_drawdown(self) -> float:
        """Return the worst-ever portfolio drawdown percentage.

        Returns ``0.0`` if no portfolio-level entity has been tracked.
        """
        portfolio_states = [
            s for s in self._states.values() if s.entity_type == "portfolio"
        ]
        if not portfolio_states:
            return 0.0
        return max(s.max_drawdown_pct for s in portfolio_states)

    def reset(self, entity_id: str | None = None) -> None:
        """Reset drawdown tracking.

        Args:
            entity_id: If supplied, only reset the given entity.  If ``None``,
                reset *all* tracked entities and alert history.
        """
        if entity_id is not None:
            self._states.pop(entity_id, None)
            # Remove associated alert cooldowns
            keys_to_remove = [
                k for k in self._last_alert_time if k[0] == entity_id
            ]
            for k in keys_to_remove:
                del self._last_alert_time[k]
            logger.info("Drawdown state reset for entity %s", entity_id)
        else:
            self._states.clear()
            self._last_alert_time.clear()
            logger.info("All drawdown states reset")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_thresholds(self, state: DrawdownState, now: datetime) -> None:
        """Fire async alerts for any newly-breached thresholds."""
        thresholds = self._thresholds.get(state.entity_type, [])
        for threshold_pct in thresholds:
            if state.current_drawdown_pct < threshold_pct:
                # Thresholds are sorted ascending; stop at the first one not breached
                break
            if threshold_pct in state._alerted_thresholds:
                continue  # already alerted for this threshold in current drawdown

            # Check cooldown
            cooldown_key = (state.entity_id, threshold_pct)
            last_time = self._last_alert_time.get(cooldown_key)
            if last_time is not None:
                elapsed = (now - last_time).total_seconds()
                if elapsed < self._alert_cooldown:
                    continue

            # Mark as alerted and schedule the async emission
            state._alerted_thresholds.add(threshold_pct)
            self._last_alert_time[cooldown_key] = now
            asyncio.ensure_future(self._emit_drawdown_alert(state, threshold_pct))

    async def _emit_drawdown_alert(
        self,
        state: DrawdownState,
        threshold_pct: float,
    ) -> None:
        """Publish a drawdown alert to the event bus.

        The alert level escalates with the threshold:
          - < 10 %  -> WARNING
          - >= 10 % -> CRITICAL
        """
        if threshold_pct >= 10.0:
            level = AlertLevel.CRITICAL
        else:
            level = AlertLevel.WARNING

        now = datetime.now(timezone.utc)

        alert = Alert(
            level=level,
            source="DrawdownMonitor",
            message=(
                f"Drawdown alert: {state.entity_type} '{state.entity_id}' "
                f"drawdown {state.current_drawdown_pct:.2f}% "
                f"(threshold {threshold_pct:.1f}%). "
                f"Peak={state.peak_value:.2f}, Current={state.current_value:.2f}"
            ),
            timestamp=now,
            data={
                "entity_id": state.entity_id,
                "entity_type": state.entity_type,
                "threshold_pct": threshold_pct,
                "current_drawdown_pct": round(state.current_drawdown_pct, 4),
                "current_drawdown": round(state.current_drawdown, 4),
                "max_drawdown_pct": round(state.max_drawdown_pct, 4),
                "peak_value": state.peak_value,
                "trough_value": state.trough_value,
                "current_value": state.current_value,
            },
        )

        try:
            await self._event_bus.publish(
                topic=Topics.RISK_ALERTS,
                event_type="DRAWDOWN_ALERT",
                payload={"alert": alert.model_dump(mode="json")},
                priority=EventPriority.HIGH
                if level == AlertLevel.CRITICAL
                else EventPriority.NORMAL,
                source="DrawdownMonitor",
            )
            logger.warning(
                "Drawdown alert emitted: %s %s at %.2f%% (threshold %.1f%%)",
                state.entity_type,
                state.entity_id,
                state.current_drawdown_pct,
                threshold_pct,
            )
        except Exception:
            logger.exception(
                "Failed to emit drawdown alert for %s %s",
                state.entity_type,
                state.entity_id,
            )
