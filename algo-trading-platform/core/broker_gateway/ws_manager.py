"""
WebSocket connection manager with automatic reconnection.

Provides a robust, production-grade WebSocket wrapper designed for broker
streaming APIs (tick data, order updates).  Key features:

* Exponential back-off with jitter to prevent thundering-herd reconnects.
* Periodic ping/pong health monitoring.
* Callback-based message dispatch (async).
* Graceful shutdown with drain.
* Connection state tracking and rich metrics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection, connect

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages a single WebSocket connection with automatic reconnection.

    The manager runs three background tasks after :meth:`connect` is called:

    1. **connect loop** -- establishes the connection and re-establishes it
       on failure using exponential back-off with jitter.
    2. **receive loop** -- reads incoming messages and dispatches them to
       the ``on_message`` callback.
    3. **ping loop** -- sends periodic pings to detect silent connection
       drops.

    All public methods are coroutine-safe and can be called from any task.

    Example::

        async def on_msg(msg: str | bytes) -> None:
            print("Got:", msg)

        ws = WebSocketManager(
            url="wss://broker.example.com/ws",
            on_message=on_msg,
            name="zerodha-ticks",
        )
        await ws.connect()
        # ... later ...
        await ws.disconnect()
    """

    class State(Enum):
        """Connection lifecycle states."""

        DISCONNECTED = "disconnected"
        CONNECTING = "connecting"
        CONNECTED = "connected"
        RECONNECTING = "reconnecting"
        CLOSING = "closing"

    def __init__(
        self,
        url: str,
        on_message: Callable[..., Any],
        on_connect: Callable[..., Any] | None = None,
        on_disconnect: Callable[..., Any] | None = None,
        on_error: Callable[..., Any] | None = None,
        headers: dict[str, str] | None = None,
        max_reconnect_attempts: int = 50,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        ping_interval: float = 30.0,
        ping_timeout: float = 10.0,
        name: str = "ws",
    ) -> None:
        """Initialise the WebSocket manager.

        Args:
            url: WebSocket endpoint URL (``wss://`` or ``ws://``).
            on_message: Async callback invoked for every received message.
                Signature: ``async (message: str | bytes) -> None``.
            on_connect: Optional async callback fired after a successful
                connection (or reconnection).
            on_disconnect: Optional async callback fired after the
                connection is lost.
            on_error: Optional async callback fired when an error occurs.
                Signature: ``async (error: Exception) -> None``.
            headers: Extra HTTP headers sent during the handshake (e.g.
                authentication tokens).
            max_reconnect_attempts: Maximum consecutive reconnection
                attempts before giving up.  Set to ``0`` to disable
                automatic reconnection.
            initial_backoff: Initial back-off delay in seconds after the
                first failed reconnection attempt.
            max_backoff: Upper bound on the back-off delay in seconds.
            ping_interval: Seconds between health-check pings.
            ping_timeout: Seconds to wait for a pong before declaring the
                connection dead.
            name: Human-readable name used in log messages.
        """
        self._url = url
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_error = on_error
        self._headers = headers or {}
        self._max_reconnect_attempts = max_reconnect_attempts
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._name = name

        # Internal state
        self._state: WebSocketManager.State = self.State.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._should_reconnect: bool = False
        self._send_lock: asyncio.Lock = asyncio.Lock()

        # Metrics
        self._messages_received: int = 0
        self._messages_sent: int = 0
        self._reconnect_count: int = 0
        self._connected_since: float | None = None
        self._last_message_at: float | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket connection.

        Starts the background connect loop which will keep reconnecting
        on failure until :meth:`disconnect` is called or
        ``max_reconnect_attempts`` is exhausted.

        Raises:
            RuntimeError: If the manager is already connected or connecting.
        """
        if self._state not in (self.State.DISCONNECTED,):
            raise RuntimeError(
                f"[{self._name}] Cannot connect: current state is {self._state.value}"
            )

        self._should_reconnect = True
        self._connect_task = asyncio.create_task(
            self._connect_loop(), name=f"{self._name}-connect-loop"
        )
        logger.info("[%s] Connection initiated to %s", self._name, self._url)

    async def disconnect(self) -> None:
        """Gracefully close the connection and stop all background tasks.

        Safe to call multiple times or when already disconnected.
        """
        if self._state == self.State.DISCONNECTED:
            return

        logger.info("[%s] Disconnecting...", self._name)
        self._state = self.State.CLOSING
        self._should_reconnect = False

        # Cancel background tasks
        for task in (self._ping_task, self._receive_task, self._connect_task):
            if task is not None and not task.done():
                task.cancel()

        # Close the WebSocket
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        # Await task cancellation
        tasks_to_await = [
            t
            for t in (self._ping_task, self._receive_task, self._connect_task)
            if t is not None and not t.done()
        ]
        if tasks_to_await:
            await asyncio.gather(*tasks_to_await, return_exceptions=True)

        self._connect_task = None
        self._receive_task = None
        self._ping_task = None

        self._state = self.State.DISCONNECTED
        self._connected_since = None

        if self._on_disconnect is not None:
            try:
                await self._on_disconnect()
            except Exception:
                logger.exception("[%s] on_disconnect callback failed", self._name)

        logger.info("[%s] Disconnected", self._name)

    async def send(self, data: str | bytes) -> None:
        """Send a text or binary message over the WebSocket.

        Args:
            data: The message payload.

        Raises:
            RuntimeError: If not currently connected.
        """
        if self._ws is None or self._state != self.State.CONNECTED:
            raise RuntimeError(
                f"[{self._name}] Cannot send: state is {self._state.value}"
            )

        async with self._send_lock:
            await self._ws.send(data)
            self._messages_sent += 1

    async def send_json(self, data: dict[str, Any]) -> None:
        """Serialize *data* as JSON and send it as a text frame.

        Args:
            data: Dict to be JSON-encoded and sent.
        """
        await self.send(json.dumps(data))

    @property
    def is_connected(self) -> bool:
        """``True`` when the connection is established and healthy."""
        return self._state == self.State.CONNECTED

    @property
    def state(self) -> State:
        """Current connection state."""
        return self._state

    @property
    def metrics(self) -> dict[str, Any]:
        """Snapshot of connection and throughput metrics.

        Returns:
            A dict with keys ``messages_received``, ``messages_sent``,
            ``reconnect_count``, ``uptime_seconds``, and
            ``last_message_at``.
        """
        uptime: float = 0.0
        if self._connected_since is not None:
            uptime = time.monotonic() - self._connected_since

        return {
            "messages_received": self._messages_received,
            "messages_sent": self._messages_sent,
            "reconnect_count": self._reconnect_count,
            "uptime_seconds": round(uptime, 2),
            "last_message_at": self._last_message_at,
        }

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _connect_loop(self) -> None:
        """Main connection loop with exponential back-off reconnection.

        Runs as a long-lived background task.  On each disconnection it
        computes the delay as::

            delay = min(initial_backoff * 2 ** attempt + jitter, max_backoff)

        where *jitter* is a random float in ``[0, 1)``.
        """
        attempt = 0

        while self._should_reconnect:
            try:
                if attempt == 0:
                    self._state = self.State.CONNECTING
                else:
                    self._state = self.State.RECONNECTING

                logger.info(
                    "[%s] Connecting (attempt %d)...", self._name, attempt + 1
                )

                self._ws = await connect(
                    self._url,
                    additional_headers=self._headers,
                    ping_interval=None,  # We handle pings ourselves
                    close_timeout=10,
                )

                # Connection succeeded -- reset attempt counter
                attempt = 0
                self._state = self.State.CONNECTED
                self._connected_since = time.monotonic()
                logger.info("[%s] Connected to %s", self._name, self._url)

                if self._on_connect is not None:
                    try:
                        await self._on_connect()
                    except Exception:
                        logger.exception(
                            "[%s] on_connect callback failed", self._name
                        )

                # Spin up receive and ping loops
                self._receive_task = asyncio.create_task(
                    self._receive_loop(), name=f"{self._name}-receive"
                )
                self._ping_task = asyncio.create_task(
                    self._ping_loop(), name=f"{self._name}-ping"
                )

                # Wait for the receive loop to end (connection lost / closed)
                await self._receive_task

            except asyncio.CancelledError:
                logger.debug("[%s] Connect loop cancelled", self._name)
                break

            except Exception as exc:
                if self._on_error is not None:
                    try:
                        await self._on_error(exc)
                    except Exception:
                        logger.exception(
                            "[%s] on_error callback failed", self._name
                        )

                logger.warning(
                    "[%s] Connection failed: %s", self._name, exc
                )

            finally:
                # Clean up sub-tasks
                for task in (self._receive_task, self._ping_task):
                    if task is not None and not task.done():
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass

                self._receive_task = None
                self._ping_task = None

                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None

                self._connected_since = None

            # Back-off before reconnecting
            if not self._should_reconnect:
                break

            attempt += 1
            if attempt > self._max_reconnect_attempts:
                logger.error(
                    "[%s] Exhausted %d reconnection attempts -- giving up",
                    self._name,
                    self._max_reconnect_attempts,
                )
                self._state = self.State.DISCONNECTED
                break

            self._reconnect_count += 1
            jitter = random.random()  # noqa: S311 -- not security-sensitive
            delay = min(
                self._initial_backoff * (2 ** (attempt - 1)) + jitter,
                self._max_backoff,
            )
            logger.info(
                "[%s] Reconnecting in %.1f s (attempt %d/%d)",
                self._name,
                delay,
                attempt,
                self._max_reconnect_attempts,
            )
            await asyncio.sleep(delay)

        # If we exit the loop without an explicit disconnect call, fire
        # the on_disconnect callback.
        if self._state != self.State.DISCONNECTED:
            self._state = self.State.DISCONNECTED
            if self._on_disconnect is not None:
                try:
                    await self._on_disconnect()
                except Exception:
                    logger.exception(
                        "[%s] on_disconnect callback failed", self._name
                    )

    async def _receive_loop(self) -> None:
        """Read messages from the WebSocket and dispatch to callback."""
        assert self._ws is not None  # noqa: S101

        try:
            async for message in self._ws:
                self._messages_received += 1
                self._last_message_at = time.monotonic()
                try:
                    await self._on_message(message)
                except Exception:
                    logger.exception(
                        "[%s] on_message callback raised an exception",
                        self._name,
                    )
        except websockets.ConnectionClosedOK:
            logger.info("[%s] Connection closed normally", self._name)
        except websockets.ConnectionClosedError as exc:
            logger.warning(
                "[%s] Connection closed with error: %s", self._name, exc
            )
        except asyncio.CancelledError:
            logger.debug("[%s] Receive loop cancelled", self._name)
            raise
        except Exception as exc:
            logger.error("[%s] Unexpected receive error: %s", self._name, exc)

    async def _ping_loop(self) -> None:
        """Periodically ping the server to detect silent connection drops.

        If a pong is not received within ``ping_timeout`` seconds the
        connection is considered dead and the WebSocket is closed, causing
        the receive loop to exit and triggering a reconnection.
        """
        assert self._ws is not None  # noqa: S101

        try:
            while True:
                await asyncio.sleep(self._ping_interval)
                try:
                    pong = await self._ws.ping()
                    await asyncio.wait_for(pong, timeout=self._ping_timeout)
                    logger.debug("[%s] Pong received", self._name)
                except asyncio.TimeoutError:
                    logger.warning(
                        "[%s] Pong timeout (%.0f s) -- closing connection",
                        self._name,
                        self._ping_timeout,
                    )
                    await self._ws.close()
                    return
                except websockets.ConnectionClosed:
                    return
        except asyncio.CancelledError:
            logger.debug("[%s] Ping loop cancelled", self._name)
            raise

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"WebSocketManager(name={self._name!r}, "
            f"state={self._state.value!r}, url={self._url!r})"
        )
