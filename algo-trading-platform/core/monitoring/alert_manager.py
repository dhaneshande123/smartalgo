"""
Alert management for the algo trading platform.

Creates, dispatches, deduplicates, and tracks alerts across all platform
components.  Supports in-memory storage, event bus publication, console
logging, and placeholder webhook channels.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.models import Alert, AlertLevel
from core.event_bus.bus import BaseEventBus, EventPriority, Topics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Severity -> event priority mapping
# ---------------------------------------------------------------------------

_LEVEL_TO_PRIORITY: dict[AlertLevel, EventPriority] = {
    AlertLevel.INFO: EventPriority.LOW,
    AlertLevel.WARNING: EventPriority.NORMAL,
    AlertLevel.CRITICAL: EventPriority.CRITICAL,
}


# ---------------------------------------------------------------------------
# AlertManager
# ---------------------------------------------------------------------------


class AlertManager:
    """Manages platform alerts across all components.

    Supports multiple alert channels:
    - In-memory store (always active, queryable via API)
    - Event bus publication
    - Console logging
    - Webhook (placeholder for Telegram / Slack / email)

    Features:
    - Alert deduplication (same alert not repeated within cooldown)
    - Alert severity levels (INFO, WARNING, CRITICAL)
    - Alert acknowledgment
    - Alert history with filtering

    Usage::

        mgr = AlertManager(event_bus)
        await mgr.send_alert(AlertLevel.WARNING, "Daily loss limit 80%", source="risk_engine")
        alerts = mgr.get_alerts(level=AlertLevel.CRITICAL, limit=10)
    """

    def __init__(
        self,
        event_bus: BaseEventBus | None = None,
        cooldown_seconds: float = 60.0,
        max_alerts: int = 10_000,
    ) -> None:
        self._alerts: list[Alert] = []
        self._max_alerts: int = max_alerts
        self._event_bus: BaseEventBus | None = event_bus
        self._cooldown: float = cooldown_seconds
        # Deduplication: key -> last alert timestamp
        self._last_alert_keys: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Public API — sending alerts
    # ------------------------------------------------------------------

    async def send_alert(
        self,
        level: AlertLevel,
        message: str,
        source: str = "",
        strategy_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> Alert | None:
        """Create and dispatch an alert.

        If an identical alert (same level + message + source) was sent
        within the cooldown window the alert is suppressed and ``None``
        is returned.

        Args:
            level: Severity of the alert.
            message: Human-readable alert message.
            source: Component that raised the alert (e.g. ``"risk_engine"``).
            strategy_id: Optional strategy identifier.
            details: Optional structured data payload.

        Returns:
            The created :class:`Alert`, or ``None`` if deduplicated.
        """
        if self._is_duplicate(level, message, source):
            logger.debug("Suppressed duplicate alert: [%s] %s", level.value, message)
            return None

        now = datetime.now(timezone.utc)

        data: dict[str, Any] = {}
        if strategy_id:
            data["strategy_id"] = strategy_id
        if details:
            data.update(details)

        alert = Alert(
            level=level,
            source=source or "unknown",
            message=message,
            timestamp=now,
            data=data or None,
        )

        # Store in memory
        self._alerts.append(alert)
        self._enforce_max_alerts()

        # Update dedup tracker
        dedup_key = self._dedup_key(level, message, source)
        self._last_alert_keys[dedup_key] = now

        # Log to console
        self._log_alert(alert)

        # Publish to event bus
        if self._event_bus is not None:
            await self._publish_to_event_bus(alert)

        return alert

    # ------------------------------------------------------------------
    # Public API — querying
    # ------------------------------------------------------------------

    def get_alerts(
        self,
        level: AlertLevel | None = None,
        source: str | None = None,
        since: datetime | None = None,
        acknowledged: bool | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        """Return alerts matching the given filters.

        Results are returned newest-first.

        Args:
            level: Filter by severity level.
            source: Filter by source component.
            since: Only return alerts after this timestamp.
            acknowledged: Filter by acknowledgment state.
            limit: Maximum number of alerts to return.

        Returns:
            A list of matching :class:`Alert` instances.
        """
        results: list[Alert] = []
        # Iterate in reverse (newest first)
        for alert in reversed(self._alerts):
            if level is not None and alert.level != level:
                continue
            if source is not None and alert.source != source:
                continue
            if since is not None and alert.timestamp < since:
                continue
            if acknowledged is not None and alert.acknowledged != acknowledged:
                continue
            results.append(alert)
            if len(results) >= limit:
                break
        return results

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Mark an alert as acknowledged.

        Args:
            alert_id: The ``alert_id`` of the alert to acknowledge.

        Returns:
            ``True`` if the alert was found and acknowledged,
            ``False`` otherwise.
        """
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                if not alert.acknowledged:
                    alert.acknowledged = True
                    logger.info("Alert %s acknowledged", alert_id)
                return True
        return False

    def acknowledge_all(self, level: AlertLevel | None = None) -> int:
        """Acknowledge all (optionally filtered) unacknowledged alerts.

        Args:
            level: If provided, only acknowledge alerts of this severity.

        Returns:
            The number of alerts that were newly acknowledged.
        """
        count = 0
        for alert in self._alerts:
            if alert.acknowledged:
                continue
            if level is not None and alert.level != level:
                continue
            alert.acknowledged = True
            count += 1
        if count:
            logger.info("Bulk-acknowledged %d alerts", count)
        return count

    def get_unacknowledged_count(self) -> int:
        """Return the number of unacknowledged alerts."""
        return sum(1 for a in self._alerts if not a.acknowledged)

    def get_alert_counts(self) -> dict[str, int]:
        """Return alert counts grouped by level."""
        counts: dict[str, int] = {lvl.value: 0 for lvl in AlertLevel}
        for alert in self._alerts:
            counts[alert.level.value] += 1
        return counts

    def clear_old_alerts(self, older_than: datetime) -> int:
        """Remove alerts older than the given timestamp.

        Args:
            older_than: Cut-off datetime; alerts with ``timestamp``
                strictly before this value are removed.

        Returns:
            The number of alerts removed.
        """
        before = len(self._alerts)
        self._alerts = [a for a in self._alerts if a.timestamp >= older_than]
        removed = before - len(self._alerts)
        if removed:
            logger.info("Cleared %d old alerts (before %s)", removed, older_than.isoformat())
        # Also clean up stale dedup keys
        self._clean_dedup_keys()
        return removed

    # ------------------------------------------------------------------
    # Event bus publication
    # ------------------------------------------------------------------

    async def _publish_to_event_bus(self, alert: Alert) -> None:
        """Publish an alert to the event bus on the RISK_ALERTS topic."""
        if self._event_bus is None:
            return
        try:
            priority = _LEVEL_TO_PRIORITY.get(alert.level, EventPriority.NORMAL)
            payload: dict[str, Any] = {
                "alert_id": alert.alert_id,
                "level": alert.level.value,
                "source": alert.source,
                "message": alert.message,
                "timestamp": alert.timestamp.isoformat(),
            }
            if alert.data:
                payload["data"] = alert.data

            await self._event_bus.publish(
                topic=Topics.RISK_ALERTS,
                event_type="ALERT",
                payload=payload,
                priority=priority,
                source=f"alert_manager:{alert.source}",
            )
        except Exception:
            logger.exception("Failed to publish alert %s to event bus", alert.alert_id)

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_key(level: AlertLevel, message: str, source: str) -> str:
        """Create a deduplication key from the alert identity fields."""
        return f"{level.value}|{source}|{message}"

    def _is_duplicate(self, level: AlertLevel, message: str, source: str) -> bool:
        """Check whether the same alert was sent within the cooldown window."""
        key = self._dedup_key(level, message, source)
        last = self._last_alert_keys.get(key)
        if last is None:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < self._cooldown

    def _clean_dedup_keys(self) -> None:
        """Remove expired entries from the dedup tracker."""
        now = datetime.now(timezone.utc)
        expired = [
            k
            for k, ts in self._last_alert_keys.items()
            if (now - ts).total_seconds() > self._cooldown * 2
        ]
        for k in expired:
            del self._last_alert_keys[k]

    # ------------------------------------------------------------------
    # Console logging
    # ------------------------------------------------------------------

    @staticmethod
    def _log_alert(alert: Alert) -> None:
        """Log an alert at the appropriate severity."""
        prefix = f"[ALERT:{alert.level.value}] [{alert.source}]"
        if alert.level == AlertLevel.CRITICAL:
            logger.critical("%s %s", prefix, alert.message)
        elif alert.level == AlertLevel.WARNING:
            logger.warning("%s %s", prefix, alert.message)
        else:
            logger.info("%s %s", prefix, alert.message)

    # ------------------------------------------------------------------
    # Internal housekeeping
    # ------------------------------------------------------------------

    def _enforce_max_alerts(self) -> None:
        """Trim the alert list if it exceeds the maximum size.

        Removes the oldest alerts first.
        """
        if len(self._alerts) > self._max_alerts:
            overflow = len(self._alerts) - self._max_alerts
            self._alerts = self._alerts[overflow:]
            logger.debug("Trimmed %d oldest alerts (max=%d)", overflow, self._max_alerts)
