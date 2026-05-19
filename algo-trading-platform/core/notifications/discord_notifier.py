"""
Discord webhook notification channel for the algo trading platform.

Posts messages and rich embeds to a Discord channel via a webhook URL.
Uses ``httpx`` for async HTTP.  Degrades gracefully when the webhook URL
is not configured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from core.models import Alert, AlertLevel, Trade

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity -> Discord embed colour mapping (decimal RGB)
# ---------------------------------------------------------------------------

_LEVEL_COLOURS: dict[AlertLevel, int] = {
    AlertLevel.INFO: 0x3498DB,       # blue
    AlertLevel.WARNING: 0xF39C12,    # orange
    AlertLevel.CRITICAL: 0xE74C3C,   # red
}


# ---------------------------------------------------------------------------
# DiscordNotifier
# ---------------------------------------------------------------------------


class DiscordNotifier:
    """Send messages and embeds to a Discord channel via webhook.

    Parameters
    ----------
    webhook_url:
        Discord webhook URL.  Falls back to ``DISCORD_WEBHOOK_URL`` env var.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self._timeout = timeout
        self._configured = bool(self.webhook_url)

        if not self._configured:
            logger.info(
                "DiscordNotifier is not configured (missing webhook URL). "
                "Notifications will be skipped."
            )

    @property
    def is_configured(self) -> bool:
        return self._configured

    # -- low-level senders --------------------------------------------------

    async def send_message(self, text: str) -> bool:
        """Send a plain-text message to the Discord webhook.

        Returns ``True`` on success, ``False`` on failure.
        """
        if not self._configured:
            logger.debug("Discord not configured; skipping message")
            return False

        payload = {"content": text}
        return await self._post(payload)

    async def send_embed(
        self,
        title: str,
        fields: list[dict[str, Any]],
        color: int = 0x3498DB,
    ) -> bool:
        """Send a rich embed to the Discord webhook.

        Parameters
        ----------
        title:
            Embed title.
        fields:
            List of ``{"name": ..., "value": ..., "inline": bool}`` dicts.
        color:
            Embed sidebar colour as a decimal RGB int.
        """
        embed: dict[str, Any] = {
            "title": title,
            "color": color,
            "fields": fields,
        }
        payload: dict[str, Any] = {"embeds": [embed]}
        return await self._post(payload)

    # -- formatted senders --------------------------------------------------

    async def send_alert(self, alert: Alert) -> bool:
        """Format an ``Alert`` as a Discord embed and send it."""
        colour = _LEVEL_COLOURS.get(alert.level, 0x95A5A6)
        fields = [
            {"name": "Level", "value": alert.level.value, "inline": True},
            {"name": "Source", "value": alert.source, "inline": True},
            {"name": "Message", "value": alert.message, "inline": False},
            {
                "name": "Time",
                "value": alert.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "inline": True,
            },
        ]
        return await self.send_embed(
            title=f"{alert.level.value} Alert",
            fields=fields,
            color=colour,
        )

    async def send_trade_notification(self, trade: Trade) -> bool:
        """Format a ``Trade`` as a Discord embed and send it."""
        colour = 0x2ECC71 if trade.side.value == "BUY" else 0xE74C3C
        fields = [
            {"name": "Instrument", "value": trade.instrument.symbol, "inline": True},
            {"name": "Side", "value": trade.side.value, "inline": True},
            {"name": "Quantity", "value": str(trade.quantity), "inline": True},
            {"name": "Price", "value": str(trade.price), "inline": True},
            {"name": "Strategy", "value": trade.strategy_id or "N/A", "inline": True},
            {
                "name": "Time",
                "value": trade.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "inline": True,
            },
        ]
        return await self.send_embed(
            title="Trade Executed",
            fields=fields,
            color=colour,
        )

    # -- internal -----------------------------------------------------------

    async def _post(self, payload: dict[str, Any]) -> bool:
        """POST *payload* to the webhook URL.  Returns success boolean."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self.webhook_url, json=payload)
                if response.status_code in (200, 204):
                    return True
                logger.warning(
                    "Discord webhook returned %s: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except httpx.HTTPError as exc:
            logger.error("Failed to send Discord notification: %s", exc)
            return False
