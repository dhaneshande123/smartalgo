"""
Unified notification manager for the algo trading platform.

Registers multiple notification channels (Telegram, Discord, etc.) and
broadcasts messages according to severity-based routing rules.  All
dispatch is async and errors in individual notifiers are logged but never
propagated to the caller.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

from core.models import Alert, AlertLevel, Trade

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notifier protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    """Minimal interface that any notification channel must satisfy."""

    @property
    def is_configured(self) -> bool: ...

    async def send_message(self, text: str) -> bool: ...

    async def send_alert(self, alert: Alert) -> bool: ...

    async def send_trade_notification(self, trade: Trade) -> bool: ...


# ---------------------------------------------------------------------------
# Severity routing defaults
# ---------------------------------------------------------------------------

# Mapping from AlertLevel to the set of notifier *names* that should receive
# messages at that level.  ``None`` means "send to ALL registered notifiers".
_DEFAULT_ROUTING: dict[AlertLevel, set[str] | None] = {
    AlertLevel.CRITICAL: None,                   # broadcast to all
    AlertLevel.WARNING: {"telegram"},             # Telegram only
    AlertLevel.INFO: {"telegram"},                # Telegram only
}


# ---------------------------------------------------------------------------
# NotificationManager
# ---------------------------------------------------------------------------


class NotificationManager:
    """Manage and dispatch notifications across multiple channels.

    Usage::

        mgr = NotificationManager()
        mgr.register("telegram", telegram_notifier)
        mgr.register("discord", discord_notifier)

        await mgr.notify("Server started", AlertLevel.INFO)
        await mgr.notify_alert(alert)
        await mgr.notify_trade(trade)
    """

    def __init__(
        self,
        routing: dict[AlertLevel, set[str] | None] | None = None,
    ) -> None:
        self._notifiers: dict[str, Notifier] = {}
        self._routing = routing or dict(_DEFAULT_ROUTING)

    # -- registration -------------------------------------------------------

    def register(self, name: str, notifier: Notifier) -> None:
        """Register a notifier under *name*.

        Only configured notifiers are kept; unconfigured ones are silently
        skipped so callers can always attempt registration without
        pre-checking credentials.
        """
        if not notifier.is_configured:
            logger.info(
                "Notifier '%s' is not configured; skipping registration", name
            )
            return
        self._notifiers[name] = notifier
        logger.info("Notifier '%s' registered", name)

    def unregister(self, name: str) -> None:
        """Remove a notifier by name (no-op if not registered)."""
        self._notifiers.pop(name, None)

    @property
    def registered_names(self) -> list[str]:
        """Return names of all currently registered notifiers."""
        return list(self._notifiers)

    # -- dispatch helpers ---------------------------------------------------

    def _resolve_targets(
        self, level: AlertLevel
    ) -> dict[str, Notifier]:
        """Return the notifiers that should receive a message at *level*."""
        allowed_names = self._routing.get(level)
        if allowed_names is None:
            # None means broadcast to all
            return dict(self._notifiers)
        return {
            name: n
            for name, n in self._notifiers.items()
            if name in allowed_names
        }

    async def _safe_send(
        self, name: str, coro: Any
    ) -> bool:
        """Await *coro* and catch + log any exception."""
        try:
            return await coro
        except Exception:
            logger.exception("Notifier '%s' failed", name)
            return False

    # -- public dispatch ----------------------------------------------------

    async def notify(
        self,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
    ) -> dict[str, bool]:
        """Broadcast a plain-text message to channels matching *level*.

        Returns a dict mapping notifier name to success boolean.
        """
        targets = self._resolve_targets(level)
        if not targets:
            return {}

        tasks = {
            name: self._safe_send(name, n.send_message(message))
            for name, n in targets.items()
        }
        results = await asyncio.gather(*tasks.values())
        return dict(zip(tasks.keys(), results))

    async def notify_alert(self, alert: Alert) -> dict[str, bool]:
        """Format and send an ``Alert`` to channels matching its level."""
        targets = self._resolve_targets(alert.level)
        if not targets:
            return {}

        tasks = {
            name: self._safe_send(name, n.send_alert(alert))
            for name, n in targets.items()
        }
        results = await asyncio.gather(*tasks.values())
        return dict(zip(tasks.keys(), results))

    async def notify_trade(self, trade: Trade) -> dict[str, bool]:
        """Send a trade notification to all registered notifiers."""
        if not self._notifiers:
            return {}

        tasks = {
            name: self._safe_send(name, n.send_trade_notification(trade))
            for name, n in self._notifiers.items()
        }
        results = await asyncio.gather(*tasks.values())
        return dict(zip(tasks.keys(), results))
