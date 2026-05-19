"""
WebSocket support for the algo trading platform.

Provides real-time streaming of market data, portfolio updates, order status
changes, and alerts over WebSocket connections.
"""

from .manager import WebSocketManager
from .routes import (
    ws_market_data,
    ws_portfolio,
    ws_orders,
    ws_alerts,
)

__all__ = [
    "WebSocketManager",
    "ws_market_data",
    "ws_portfolio",
    "ws_orders",
    "ws_alerts",
]
