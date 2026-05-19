"""
WebSocket Connection Manager — manages client connections, subscriptions,
broadcasting, and heartbeat keep-alive for the algo trading platform.

Channels:
    ticks       — live market data ticks
    orders      — order status updates
    trades      — trade fill notifications
    pnl         — real-time P&L snapshots
    alerts      — platform alerts and risk warnings
    positions   — position updates
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

try:
    import orjson

    def _json_dumps(obj: Any) -> str:
        return orjson.dumps(obj, default=_default_serializer).decode("utf-8")

except ImportError:
    import json

    def _json_dumps(obj: Any) -> str:  # type: ignore[misc]
        return json.dumps(obj, default=_default_serializer)


logger = logging.getLogger(__name__)

# Valid channel names that clients can subscribe to
VALID_CHANNELS = frozenset({
    "ticks",
    "orders",
    "trades",
    "pnl",
    "alerts",
    "positions",
})

HEARTBEAT_INTERVAL = 30.0  # seconds between server-side pings
CLIENT_TIMEOUT = 60.0  # consider client dead after this many seconds without pong


def _default_serializer(obj: Any) -> Any:
    """Fallback serializer for objects that orjson / json cannot handle."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


class _ClientConnection:
    """Internal representation of a single WebSocket client."""

    __slots__ = ("websocket", "client_id", "channels", "connected_at", "last_pong")

    def __init__(self, websocket: WebSocket, client_id: str) -> None:
        self.websocket = websocket
        self.client_id = client_id
        self.channels: set[str] = set()
        self.connected_at: float = time.monotonic()
        self.last_pong: float = time.monotonic()


class WebSocketManager:
    """Manages WebSocket connections, channel subscriptions, and broadcasting.

    Usage::

        ws_manager = WebSocketManager()

        # In a WebSocket endpoint:
        await ws_manager.connect(websocket, client_id="abc")
        ws_manager.subscribe(client_id="abc", channels=["ticks", "pnl"])
        ...
        await ws_manager.broadcast("ticks", {"symbol": "NIFTY", "ltp": 24300})
        ...
        ws_manager.disconnect(client_id="abc")
    """

    def __init__(self) -> None:
        # client_id -> _ClientConnection
        self._clients: dict[str, _ClientConnection] = {}
        # channel -> set of client_ids
        self._channel_subs: dict[str, set[str]] = defaultdict(set)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the heartbeat background loop."""
        if self._running:
            return
        self._running = True
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="ws-heartbeat"
        )
        logger.info("WebSocketManager started")

    async def stop(self) -> None:
        """Stop the heartbeat loop and close all connections."""
        self._running = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Close all connected clients
        for client_id in list(self._clients):
            await self._force_disconnect(client_id)

        logger.info("WebSocketManager stopped")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(
        self,
        websocket: WebSocket,
        client_id: str,
        channels: list[str] | None = None,
    ) -> None:
        """Accept a WebSocket connection and register the client.

        Args:
            websocket: The FastAPI WebSocket object.
            client_id: A unique identifier for this client.
            channels: Optional list of channels to auto-subscribe to.
        """
        await websocket.accept()

        async with self._lock:
            # If this client_id is already connected, disconnect the old one
            if client_id in self._clients:
                await self._force_disconnect(client_id)

            conn = _ClientConnection(websocket, client_id)
            self._clients[client_id] = conn

        # Subscribe to requested channels
        if channels:
            self.subscribe(client_id, channels)

        logger.info(
            "Client connected: %s (channels=%s, total_clients=%d)",
            client_id,
            channels or [],
            len(self._clients),
        )

        # Send welcome message
        await self.send_to(client_id, {
            "type": "connected",
            "client_id": client_id,
            "channels": list(conn.channels),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def disconnect(self, client_id: str) -> None:
        """Remove a client and clean up all its subscriptions.

        Args:
            client_id: The client to disconnect.
        """
        conn = self._clients.pop(client_id, None)
        if conn is None:
            return

        # Remove from all channel subscriptions
        for channel in conn.channels:
            self._channel_subs[channel].discard(client_id)

        logger.info(
            "Client disconnected: %s (total_clients=%d)",
            client_id,
            len(self._clients),
        )

    async def _force_disconnect(self, client_id: str) -> None:
        """Force-close a client's WebSocket and clean up."""
        conn = self._clients.get(client_id)
        if conn is None:
            return
        try:
            await conn.websocket.close(code=1000)
        except Exception:
            pass
        self.disconnect(client_id)

    # ------------------------------------------------------------------
    # Channel subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, client_id: str, channels: list[str]) -> list[str]:
        """Subscribe a client to one or more channels.

        Args:
            client_id: The client to subscribe.
            channels: List of channel names.

        Returns:
            The list of channels actually subscribed to (invalid ones filtered out).
        """
        conn = self._clients.get(client_id)
        if conn is None:
            return []

        subscribed: list[str] = []
        for ch in channels:
            if ch in VALID_CHANNELS:
                conn.channels.add(ch)
                self._channel_subs[ch].add(client_id)
                subscribed.append(ch)
            else:
                logger.warning(
                    "Client %s tried to subscribe to invalid channel: %s",
                    client_id,
                    ch,
                )

        return subscribed

    def unsubscribe(self, client_id: str, channels: list[str]) -> None:
        """Unsubscribe a client from one or more channels."""
        conn = self._clients.get(client_id)
        if conn is None:
            return

        for ch in channels:
            conn.channels.discard(ch)
            self._channel_subs[ch].discard(client_id)

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def broadcast(self, channel: str, data: dict[str, Any]) -> int:
        """Send a message to all subscribers of a channel.

        Args:
            channel: The channel to broadcast on.
            data: The payload to send. Will be wrapped in an envelope with
                  ``channel`` and ``timestamp`` fields.

        Returns:
            The number of clients the message was sent to.
        """
        subscriber_ids = list(self._channel_subs.get(channel, set()))
        if not subscriber_ids:
            return 0

        envelope = {
            "channel": channel,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        message = _json_dumps(envelope)

        sent = 0
        disconnected: list[str] = []
        for cid in subscriber_ids:
            conn = self._clients.get(cid)
            if conn is None:
                continue
            try:
                await conn.websocket.send_text(message)
                sent += 1
            except (WebSocketDisconnect, RuntimeError, Exception):
                disconnected.append(cid)

        # Clean up disconnected clients
        for cid in disconnected:
            self.disconnect(cid)

        return sent

    async def send_to(self, client_id: str, data: dict[str, Any]) -> bool:
        """Send a message directly to a specific client.

        Args:
            client_id: The target client.
            data: The payload to send.

        Returns:
            True if the message was sent, False otherwise.
        """
        conn = self._clients.get(client_id)
        if conn is None:
            return False

        try:
            message = _json_dumps(data)
            await conn.websocket.send_text(message)
            return True
        except (WebSocketDisconnect, RuntimeError, Exception):
            self.disconnect(client_id)
            return False

    # ------------------------------------------------------------------
    # Heartbeat / keep-alive
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Periodically ping all connected clients to keep connections alive."""
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if not self._running:
                break

            now = time.monotonic()
            stale_clients: list[str] = []

            for client_id, conn in list(self._clients.items()):
                # Check if client has timed out
                if now - conn.last_pong > CLIENT_TIMEOUT:
                    stale_clients.append(client_id)
                    continue

                # Send ping
                try:
                    await conn.websocket.send_text(
                        _json_dumps({"type": "ping", "ts": now})
                    )
                except (WebSocketDisconnect, RuntimeError, Exception):
                    stale_clients.append(client_id)

            # Remove stale clients
            for cid in stale_clients:
                logger.info("Removing stale client: %s", cid)
                await self._force_disconnect(cid)

    def handle_pong(self, client_id: str) -> None:
        """Record receipt of a pong from a client.

        Call this when the client sends a ``{"type": "pong"}`` message.
        """
        conn = self._clients.get(client_id)
        if conn is not None:
            conn.last_pong = time.monotonic()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def client_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._clients)

    def channel_stats(self) -> dict[str, int]:
        """Return subscriber counts per channel."""
        return {ch: len(subs) for ch, subs in self._channel_subs.items() if subs}

    def snapshot(self) -> dict[str, Any]:
        """Return a summary of WebSocket manager state."""
        return {
            "total_clients": self.client_count,
            "channels": self.channel_stats(),
            "clients": [
                {
                    "client_id": c.client_id,
                    "channels": list(c.channels),
                    "connected_seconds": round(time.monotonic() - c.connected_at, 1),
                }
                for c in self._clients.values()
            ],
        }
