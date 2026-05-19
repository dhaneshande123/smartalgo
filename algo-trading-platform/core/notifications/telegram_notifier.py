"""
Telegram notification channel for the algo trading platform.

Sends formatted alerts, trade notifications, P&L summaries, and risk
breach warnings to a Telegram chat via the Bot API.  Uses ``httpx``
for async HTTP calls and degrades gracefully when credentials are not
configured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from core.models import Alert, AlertLevel, Trade

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TELEGRAM_API = "https://api.telegram.org"

_SEVERITY_EMOJI: dict[AlertLevel, str] = {
    AlertLevel.INFO: "\u2139\ufe0f",       # info
    AlertLevel.WARNING: "\u26a0\ufe0f",     # warning
    AlertLevel.CRITICAL: "\U0001f6a8",      # rotating light
}


# ---------------------------------------------------------------------------
# TelegramNotifier
# ---------------------------------------------------------------------------


class TelegramNotifier:
    """Send messages to a Telegram chat using the Bot HTTP API.

    Parameters
    ----------
    bot_token:
        Telegram bot token.  Falls back to ``TELEGRAM_BOT_TOKEN`` env var.
    chat_id:
        Target chat / group ID.  Falls back to ``TELEGRAM_CHAT_ID`` env var.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._timeout = timeout
        self._configured = bool(self.bot_token and self.chat_id)

        if not self._configured:
            logger.info(
                "TelegramNotifier is not configured (missing bot token or chat ID). "
                "Notifications will be skipped."
            )

    @property
    def is_configured(self) -> bool:
        """Return True if both bot token and chat ID are set."""
        return self._configured

    # -- low-level send -----------------------------------------------------

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a plain text message to the configured Telegram chat.

        Returns ``True`` on success, ``False`` on failure (logged, never raised).
        """
        if not self._configured:
            logger.debug("Telegram not configured; skipping message")
            return False

        url = f"{_TELEGRAM_API}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    return True
                logger.warning(
                    "Telegram API returned %s: %s",
                    response.status_code,
                    response.text,
                )
                return False
        except httpx.HTTPError as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False

    # -- formatted senders --------------------------------------------------

    async def send_alert(self, alert: Alert) -> bool:
        """Format an ``Alert`` model with severity emoji and send it."""
        emoji = _SEVERITY_EMOJI.get(alert.level, "")
        lines = [
            f"{emoji} <b>{alert.level.value} Alert</b>",
            f"<b>Source:</b> {alert.source}",
            f"<b>Message:</b> {alert.message}",
            f"<b>Time:</b> {alert.timestamp:%Y-%m-%d %H:%M:%S}",
        ]
        return await self.send_message("\n".join(lines))

    async def send_trade_notification(self, trade: Trade) -> bool:
        """Format trade details and send to Telegram."""
        side_emoji = "\U0001f7e2" if trade.side.value == "BUY" else "\U0001f534"
        lines = [
            f"{side_emoji} <b>Trade Executed</b>",
            f"<b>Instrument:</b> {trade.instrument.symbol}",
            f"<b>Side:</b> {trade.side.value}",
            f"<b>Qty:</b> {trade.quantity}",
            f"<b>Price:</b> {trade.price}",
            f"<b>Strategy:</b> {trade.strategy_id or 'N/A'}",
            f"<b>Time:</b> {trade.timestamp:%Y-%m-%d %H:%M:%S}",
        ]
        return await self.send_message("\n".join(lines))

    async def send_pnl_summary(self, pnl_data: dict[str, Any]) -> bool:
        """Send a daily P&L summary.

        Expected keys in *pnl_data*: ``realized``, ``unrealized``, ``total``,
        ``trades_count``, ``win_rate`` (all optional with sensible fallbacks).
        """
        total = pnl_data.get("total", 0)
        pnl_emoji = "\U0001f4c8" if total >= 0 else "\U0001f4c9"

        lines = [
            f"{pnl_emoji} <b>Daily P&L Summary</b>",
            f"<b>Realized:</b> {pnl_data.get('realized', 0):,.2f}",
            f"<b>Unrealized:</b> {pnl_data.get('unrealized', 0):,.2f}",
            f"<b>Total:</b> {total:,.2f}",
            f"<b>Trades:</b> {pnl_data.get('trades_count', 0)}",
            f"<b>Win Rate:</b> {pnl_data.get('win_rate', 0):.1%}",
        ]
        return await self.send_message("\n".join(lines))

    async def send_risk_breach(self, breach_info: dict[str, Any]) -> bool:
        """Send an urgent risk-breach alert.

        Expected keys: ``metric``, ``current_value``, ``threshold``, ``message``.
        """
        lines = [
            "\U0001f6a8\U0001f6a8 <b>RISK BREACH</b> \U0001f6a8\U0001f6a8",
            f"<b>Metric:</b> {breach_info.get('metric', 'unknown')}",
            f"<b>Current:</b> {breach_info.get('current_value', 'N/A')}",
            f"<b>Threshold:</b> {breach_info.get('threshold', 'N/A')}",
            f"<b>Details:</b> {breach_info.get('message', '')}",
        ]
        return await self.send_message("\n".join(lines))
