"""
Notifications sub-package for the algo trading platform.

Re-exports the main public API::

    from core.notifications import NotificationManager, TelegramNotifier, DiscordNotifier
"""

from core.notifications.discord_notifier import DiscordNotifier
from core.notifications.notification_manager import NotificationManager
from core.notifications.telegram_notifier import TelegramNotifier

__all__ = [
    "DiscordNotifier",
    "NotificationManager",
    "TelegramNotifier",
]
