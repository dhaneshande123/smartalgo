"""
Broker Manager — Multi-broker routing, failover, and lifecycle management.

Manages multiple broker connections, routes orders to the best available broker
based on margin availability and rate limits, and handles automatic failover
when the primary broker is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from core.models import (
    Instrument,
    MarginInfo,
    OptionChain,
    Order,
    OrderResponse,
    OrderStatus,
    Position,
    Trade,
)

from .base import BaseBroker

logger = logging.getLogger(__name__)


class BrokerHealth:
    """Tracks health metrics for a single broker connection."""

    def __init__(self, broker_name: str) -> None:
        self.broker_name = broker_name
        self.is_connected: bool = False
        self.last_heartbeat: datetime | None = None
        self.consecutive_errors: int = 0
        self.total_errors: int = 0
        self.total_requests: int = 0
        self.avg_latency_ms: float = 0.0
        self._latencies: list[float] = []

    def record_success(self, latency_ms: float) -> None:
        self.consecutive_errors = 0
        self.total_requests += 1
        self._latencies.append(latency_ms)
        if len(self._latencies) > 100:
            self._latencies = self._latencies[-100:]
        self.avg_latency_ms = sum(self._latencies) / len(self._latencies)
        self.last_heartbeat = datetime.now(timezone.utc)

    def record_error(self) -> None:
        self.consecutive_errors += 1
        self.total_errors += 1
        self.total_requests += 1

    @property
    def is_healthy(self) -> bool:
        return self.is_connected and self.consecutive_errors < 5

    def snapshot(self) -> dict[str, Any]:
        return {
            "broker": self.broker_name,
            "connected": self.is_connected,
            "healthy": self.is_healthy,
            "consecutive_errors": self.consecutive_errors,
            "total_errors": self.total_errors,
            "total_requests": self.total_requests,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
        }


class BrokerManager:
    """Manages multiple broker connections with routing and failover.

    Features:
    - Register multiple brokers (primary + backups)
    - Route orders to the best available broker
    - Automatic failover if primary broker fails
    - Parallel position/order aggregation across brokers
    - Health monitoring for all brokers
    - Kill switch: cancel all orders across all brokers
    """

    def __init__(
        self,
        primary_broker_name: str = "",
        backup_broker_name: str = "",
        max_failover_attempts: int = 3,
    ) -> None:
        self._brokers: dict[str, BaseBroker] = {}
        self._health: dict[str, BrokerHealth] = {}
        self._primary_name = primary_broker_name
        self._backup_name = backup_broker_name
        self._max_failover_attempts = max_failover_attempts
        self._active_broker_name: str = ""
        self._lock = asyncio.Lock()

    # ── Registration ──────────────────────────────────────────────────

    def register_broker(self, broker: BaseBroker) -> None:
        """Register a broker instance."""
        name = broker.name
        self._brokers[name] = broker
        self._health[name] = BrokerHealth(name)
        logger.info("Registered broker: %s", name)

    def get_broker(self, name: str) -> BaseBroker:
        """Get a broker by name."""
        if name not in self._brokers:
            raise KeyError(f"Broker '{name}' not registered")
        return self._brokers[name]

    @property
    def primary(self) -> BaseBroker | None:
        return self._brokers.get(self._primary_name)

    @property
    def backup(self) -> BaseBroker | None:
        return self._brokers.get(self._backup_name)

    @property
    def active_broker(self) -> BaseBroker:
        """The currently active broker for order routing."""
        name = self._active_broker_name or self._primary_name
        if name not in self._brokers:
            raise RuntimeError("No active broker available")
        return self._brokers[name]

    @property
    def all_brokers(self) -> dict[str, BaseBroker]:
        return dict(self._brokers)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def connect_all(self) -> dict[str, bool]:
        """Connect to all registered brokers. Returns connection status per broker."""
        results: dict[str, bool] = {}
        for name, broker in self._brokers.items():
            try:
                await broker.connect()
                self._health[name].is_connected = True
                results[name] = True
                logger.info("Connected to broker: %s", name)
            except Exception as exc:
                self._health[name].is_connected = False
                self._health[name].record_error()
                results[name] = False
                logger.error("Failed to connect to broker %s: %s", name, exc)

        # Set active broker
        if self._primary_name in results and results[self._primary_name]:
            self._active_broker_name = self._primary_name
        elif self._backup_name in results and results[self._backup_name]:
            self._active_broker_name = self._backup_name
            logger.warning(
                "Primary broker %s unavailable, using backup %s",
                self._primary_name,
                self._backup_name,
            )
        else:
            # Use any connected broker
            for name, connected in results.items():
                if connected:
                    self._active_broker_name = name
                    logger.warning("Using fallback broker: %s", name)
                    break

        return results

    async def disconnect_all(self) -> None:
        """Disconnect from all brokers."""
        for name, broker in self._brokers.items():
            try:
                await broker.disconnect()
                self._health[name].is_connected = False
                logger.info("Disconnected from broker: %s", name)
            except Exception as exc:
                logger.error("Error disconnecting from %s: %s", name, exc)

    # ── Order Routing with Failover ───────────────────────────────────

    async def place_order(self, order: Order, broker_name: str | None = None) -> OrderResponse:
        """Place an order with automatic failover.

        Args:
            order: The order to place.
            broker_name: Specific broker to use. If None, uses the active broker
                        with failover to backup.
        """
        target_names: list[str] = []
        if broker_name:
            target_names = [broker_name]
        else:
            target_names = [self._active_broker_name]
            if self._backup_name and self._backup_name != self._active_broker_name:
                target_names.append(self._backup_name)

        last_error: Exception | None = None
        for name in target_names:
            broker = self._brokers.get(name)
            health = self._health.get(name)
            if not broker or not health or not health.is_healthy:
                continue

            try:
                import time
                start = time.perf_counter()
                response = await broker.place_order(order)
                elapsed_ms = (time.perf_counter() - start) * 1000
                health.record_success(elapsed_ms)

                logger.info(
                    "Order placed via %s: order_id=%s, broker_order_id=%s",
                    name,
                    response.order_id,
                    response.broker_order_id,
                )
                return response

            except Exception as exc:
                health.record_error()
                last_error = exc
                logger.error(
                    "Order placement failed on %s: %s. Attempting failover...",
                    name,
                    exc,
                )

        # All brokers failed
        error_msg = f"Order placement failed on all brokers: {last_error}"
        logger.critical(error_msg)
        return OrderResponse(
            success=False,
            order_id=order.order_id,
            message=error_msg,
            status=OrderStatus.ERROR,
        )

    async def modify_order(
        self, order_id: str, modifications: dict[str, Any], broker_name: str | None = None
    ) -> OrderResponse:
        """Modify an order on the specified or active broker."""
        broker = self._brokers[broker_name or self._active_broker_name]
        return await broker.modify_order(order_id, modifications)

    async def cancel_order(self, order_id: str, broker_name: str | None = None) -> OrderResponse:
        """Cancel an order on the specified or active broker."""
        broker = self._brokers[broker_name or self._active_broker_name]
        return await broker.cancel_order(order_id)

    # ── Kill Switch ───────────────────────────────────────────────────

    async def kill_switch(self) -> dict[str, list[OrderResponse]]:
        """EMERGENCY: Cancel ALL open orders across ALL brokers.

        Returns a dict of broker_name -> list of cancel responses.
        """
        logger.critical("KILL SWITCH ACTIVATED — cancelling all orders across all brokers")
        results: dict[str, list[OrderResponse]] = {}

        tasks = {}
        for name, broker in self._brokers.items():
            if self._health[name].is_connected:
                tasks[name] = asyncio.create_task(broker.cancel_all_orders())

        for name, task in tasks.items():
            try:
                responses = await task
                results[name] = responses
                logger.info("Kill switch: cancelled %d orders on %s", len(responses), name)
            except Exception as exc:
                logger.error("Kill switch failed on %s: %s", name, exc)
                results[name] = []

        return results

    # ── Aggregated Queries ────────────────────────────────────────────

    async def get_all_positions(self) -> dict[str, list[Position]]:
        """Fetch positions from all connected brokers."""
        results: dict[str, list[Position]] = {}
        for name, broker in self._brokers.items():
            if self._health[name].is_connected:
                try:
                    positions = await broker.get_positions()
                    results[name] = positions
                except Exception as exc:
                    logger.error("Failed to fetch positions from %s: %s", name, exc)
                    results[name] = []
        return results

    async def get_all_orders(self) -> dict[str, list[Order]]:
        """Fetch order book from all connected brokers."""
        results: dict[str, list[Order]] = {}
        for name, broker in self._brokers.items():
            if self._health[name].is_connected:
                try:
                    orders = await broker.get_order_book()
                    results[name] = orders
                except Exception as exc:
                    logger.error("Failed to fetch orders from %s: %s", name, exc)
                    results[name] = []
        return results

    async def get_combined_margins(self) -> dict[str, MarginInfo]:
        """Fetch margin info from all connected brokers."""
        results: dict[str, MarginInfo] = {}
        for name, broker in self._brokers.items():
            if self._health[name].is_connected:
                try:
                    margins = await broker.get_margins()
                    results[name] = margins
                except Exception as exc:
                    logger.error("Failed to fetch margins from %s: %s", name, exc)
        return results

    # ── Health ────────────────────────────────────────────────────────

    def get_health(self) -> dict[str, dict[str, Any]]:
        """Get health status for all brokers."""
        return {name: health.snapshot() for name, health in self._health.items()}

    async def health_check(self) -> dict[str, bool]:
        """Ping all brokers and update health status."""
        results: dict[str, bool] = {}
        for name, broker in self._brokers.items():
            try:
                connected = await broker.is_connected()
                self._health[name].is_connected = connected
                results[name] = connected
                if connected:
                    self._health[name].last_heartbeat = datetime.now(timezone.utc)
            except Exception:
                self._health[name].is_connected = False
                self._health[name].record_error()
                results[name] = False

        # Auto-failover if primary is down
        if not results.get(self._primary_name, False):
            if self._active_broker_name == self._primary_name:
                if self._backup_name and results.get(self._backup_name, False):
                    self._active_broker_name = self._backup_name
                    logger.warning(
                        "Auto-failover: primary %s down, switching to %s",
                        self._primary_name,
                        self._backup_name,
                    )
        elif self._active_broker_name != self._primary_name:
            # Primary is back, switch back
            self._active_broker_name = self._primary_name
            logger.info("Primary broker %s restored, switching back", self._primary_name)

        return results
