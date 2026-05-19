"""
Smart Order Router -- selects the optimal broker for each order based on
health, latency, routing rules, and availability.

Supports multiple strategies: round-robin, lowest latency, priority-based
rule matching, and simple failover.  Automatically excludes unhealthy brokers
and tracks per-broker success/failure statistics.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from core.models import Order

from core.broker_gateway.manager import BrokerManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------


class RoutingStrategy(str, Enum):
    """Available order routing strategies."""

    ROUND_ROBIN = "round_robin"
    LOWEST_LATENCY = "lowest_latency"
    BEST_MARGIN = "best_margin"
    PRIORITY_BASED = "priority_based"
    FAILOVER = "failover"


@dataclass
class RoutingRule:
    """A routing rule that maps a condition to a preferred broker.

    Attributes:
        name:             Human-readable rule name.
        priority:         Lower number = higher priority.
        condition:        Callable receiving an :class:`Order` and returning
                          ``True`` if the rule applies.
        broker_name:      Preferred broker for matching orders.
        fallback_brokers: Ordered list of fallback broker names.
    """

    name: str
    priority: int
    condition: Callable[[Order], bool]
    broker_name: str
    fallback_brokers: list[str] = field(default_factory=list)


@dataclass
class RoutingStats:
    """Per-broker routing statistics."""

    broker_name: str
    total_orders: int = 0
    successful_orders: int = 0
    failed_orders: int = 0
    avg_latency_ms: float = 0.0
    _latency_sum: float = field(default=0.0, repr=False)
    last_failure: str = ""
    last_failure_time: datetime | None = None

    def record_success(self, latency_ms: float) -> None:
        """Record a successful order routing."""
        self.total_orders += 1
        self.successful_orders += 1
        self._latency_sum += latency_ms
        self.avg_latency_ms = self._latency_sum / self.successful_orders

    def record_failure(self, error: str) -> None:
        """Record a failed order routing."""
        self.total_orders += 1
        self.failed_orders += 1
        self.last_failure = error
        self.last_failure_time = datetime.now(timezone.utc)

    @property
    def success_rate(self) -> float:
        """Success rate as a fraction in [0, 1]."""
        if self.total_orders == 0:
            return 1.0
        return self.successful_orders / self.total_orders


class NoBrokerAvailableError(Exception):
    """Raised when no healthy broker can handle the order."""


# ---------------------------------------------------------------------------
# Smart Router
# ---------------------------------------------------------------------------


class SmartRouter:
    """Routes orders to the optimal broker.

    Features
    --------
    * Multiple routing strategies (round robin, lowest latency, priority
      rules, failover).
    * Automatic exclusion of unhealthy brokers.
    * Per-broker success / failure / latency tracking.
    * Configurable routing rules for instrument-level routing.

    Usage::

        router = SmartRouter(broker_manager, strategy=RoutingStrategy.LOWEST_LATENCY)
        router.add_rule(RoutingRule(
            name="options_to_zerodha",
            priority=1,
            condition=lambda o: o.instrument.segment.value == "OPTIONS",
            broker_name="zerodha",
        ))
        broker_name = await router.select_broker(order)
    """

    def __init__(
        self,
        broker_manager: BrokerManager,
        strategy: RoutingStrategy = RoutingStrategy.FAILOVER,
    ) -> None:
        self._broker_manager = broker_manager
        self._strategy = strategy
        self._rules: list[RoutingRule] = []
        self._round_robin_index = 0
        self._stats: dict[str, RoutingStats] = {}

    # -- rules ---------------------------------------------------------------

    def add_rule(self, rule: RoutingRule) -> None:
        """Add a routing rule (kept sorted by priority)."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)
        logger.info("Routing rule added: %s (priority=%d)", rule.name, rule.priority)

    def remove_rule(self, name: str) -> None:
        """Remove a rule by name.  No-op if not found."""
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        if len(self._rules) < before:
            logger.info("Routing rule removed: %s", name)

    # -- broker selection -----------------------------------------------------

    async def select_broker(self, order: Order) -> str:
        """Select the best broker for *order*.

        Delegates to the active routing strategy.

        Returns:
            The name of the selected broker.

        Raises:
            NoBrokerAvailableError: If no healthy broker is available.
        """
        dispatch = {
            RoutingStrategy.ROUND_ROBIN: self._route_round_robin,
            RoutingStrategy.LOWEST_LATENCY: self._route_lowest_latency,
            RoutingStrategy.PRIORITY_BASED: self._route_priority_based,
            RoutingStrategy.FAILOVER: self._route_failover,
            RoutingStrategy.BEST_MARGIN: self._route_failover,  # fallback
        }
        handler = dispatch.get(self._strategy, self._route_failover)
        return await handler(order)

    # -- strategy implementations --------------------------------------------

    async def _route_round_robin(self, order: Order) -> str:
        """Distribute orders evenly across healthy brokers."""
        healthy = self._get_healthy_brokers()
        if not healthy:
            raise NoBrokerAvailableError("No healthy brokers available")

        idx = self._round_robin_index % len(healthy)
        self._round_robin_index += 1
        chosen = healthy[idx]
        logger.debug("Round-robin selected broker: %s", chosen)
        return chosen

    async def _route_lowest_latency(self, order: Order) -> str:
        """Route to the broker with the lowest average latency."""
        healthy = self._get_healthy_brokers()
        if not healthy:
            raise NoBrokerAvailableError("No healthy brokers available")

        # Pick the broker with lowest avg latency from our stats
        best: str | None = None
        best_latency = float("inf")

        for name in healthy:
            stats = self._stats.get(name)
            if stats is None or stats.total_orders == 0:
                # No data yet — give it a chance
                return name
            if stats.avg_latency_ms < best_latency:
                best_latency = stats.avg_latency_ms
                best = name

        if best is None:
            best = healthy[0]

        logger.debug(
            "Lowest-latency selected broker: %s (%.1fms)", best, best_latency
        )
        return best

    async def _route_priority_based(self, order: Order) -> str:
        """Use priority-ordered rules to select a broker."""
        healthy_set = set(self._get_healthy_brokers())
        if not healthy_set:
            raise NoBrokerAvailableError("No healthy brokers available")

        # Check rules in priority order
        for rule in self._rules:
            try:
                if rule.condition(order):
                    if rule.broker_name in healthy_set:
                        logger.debug(
                            "Rule '%s' matched, selected broker: %s",
                            rule.name,
                            rule.broker_name,
                        )
                        return rule.broker_name

                    # Try fallbacks
                    for fb in rule.fallback_brokers:
                        if fb in healthy_set:
                            logger.debug(
                                "Rule '%s' matched but %s unhealthy, fallback: %s",
                                rule.name,
                                rule.broker_name,
                                fb,
                            )
                            return fb
            except Exception as exc:
                logger.warning(
                    "Routing rule '%s' condition raised: %s", rule.name, exc
                )

        # No rule matched — fall back to failover
        return await self._route_failover(order)

    async def _route_failover(self, order: Order) -> str:
        """Route to the primary broker, falling back to backups."""
        healthy = self._get_healthy_brokers()
        if not healthy:
            raise NoBrokerAvailableError("No healthy brokers available")

        # Prefer brokers with higher success rate
        healthy_sorted = sorted(
            healthy,
            key=lambda n: self._stats.get(n, RoutingStats(n)).success_rate,
            reverse=True,
        )
        chosen = healthy_sorted[0]
        logger.debug("Failover selected broker: %s", chosen)
        return chosen

    # -- health checks -------------------------------------------------------

    def _get_healthy_brokers(self) -> list[str]:
        """Return names of all connected, healthy brokers."""
        healthy: list[str] = []
        for name, health in self._broker_manager.broker_health.items():
            if health.is_connected:
                healthy.append(name)
        return healthy

    # -- statistics recording ------------------------------------------------

    def record_success(self, broker_name: str, latency_ms: float) -> None:
        """Record a successful order routing to *broker_name*."""
        stats = self._stats.setdefault(
            broker_name, RoutingStats(broker_name=broker_name)
        )
        stats.record_success(latency_ms)

    def record_failure(self, broker_name: str, error: str) -> None:
        """Record a failed order routing to *broker_name*."""
        stats = self._stats.setdefault(
            broker_name, RoutingStats(broker_name=broker_name)
        )
        stats.record_failure(error)

    # -- accessors -----------------------------------------------------------

    @property
    def strategy(self) -> RoutingStrategy:
        """The active routing strategy."""
        return self._strategy

    @strategy.setter
    def strategy(self, value: RoutingStrategy) -> None:
        """Change the active routing strategy."""
        self._strategy = value
        logger.info("Routing strategy changed to %s", value.value)

    @property
    def routing_stats(self) -> dict[str, RoutingStats]:
        """Per-broker routing statistics."""
        return dict(self._stats)

    @property
    def rules(self) -> list[RoutingRule]:
        """Current routing rules (sorted by priority)."""
        return list(self._rules)
