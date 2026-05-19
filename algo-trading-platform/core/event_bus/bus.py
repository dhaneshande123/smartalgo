"""
Event Bus — internal pub/sub message bus for the algo trading platform.

Provides topic-based publish/subscribe with priority queues, event filtering,
dead letter queue, and metrics. Ships with an in-memory backend (asyncio) and
a stubbed Redis Streams backend for future production use.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Callable, Awaitable
import uuid

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event priority
# ---------------------------------------------------------------------------

class EventPriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3  # risk events, kill switch


# ---------------------------------------------------------------------------
# Event wrapper
# ---------------------------------------------------------------------------

@dataclass
class BusEvent:
    topic: str
    event_type: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    priority: EventPriority = EventPriority.NORMAL
    source: str = ""
    correlation_id: str = ""

    def __lt__(self, other: "BusEvent") -> bool:
        """Higher numeric priority value means higher importance — sort descending."""
        if not isinstance(other, BusEvent):
            return NotImplemented
        # PriorityQueue is a min-heap, so negate priority so CRITICAL (3) comes first.
        return (-self.priority, self.timestamp) < (-other.priority, other.timestamp)


# ---------------------------------------------------------------------------
# Subscriber callback type
# ---------------------------------------------------------------------------

EventHandler = Callable[[BusEvent], Awaitable[None]]


# ---------------------------------------------------------------------------
# Subscription info
# ---------------------------------------------------------------------------

@dataclass
class Subscription:
    topic: str
    handler: EventHandler
    subscriber_id: str
    subscription_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    event_type_filter: str | None = None
    predicate: Callable[[BusEvent], bool] | None = None


# ---------------------------------------------------------------------------
# Dead-letter entry
# ---------------------------------------------------------------------------

@dataclass
class DeadLetterEntry:
    event: BusEvent
    subscriber_id: str
    error: str
    failed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class EventBusMetrics:
    """Track event bus performance metrics."""

    def __init__(self) -> None:
        self.published: int = 0
        self.consumed: int = 0
        self.failed: int = 0
        self._latencies: list[float] = []

    def record_latency(self, seconds: float) -> None:
        self._latencies.append(seconds)

    @property
    def avg_latency(self) -> float:
        if not self._latencies:
            return 0.0
        return sum(self._latencies) / len(self._latencies)

    @property
    def max_latency(self) -> float:
        if not self._latencies:
            return 0.0
        return max(self._latencies)

    def snapshot(self) -> dict[str, Any]:
        return {
            "published": self.published,
            "consumed": self.consumed,
            "failed": self.failed,
            "avg_latency_ms": round(self.avg_latency * 1000, 3),
            "max_latency_ms": round(self.max_latency * 1000, 3),
        }

    def reset(self) -> None:
        self.published = 0
        self.consumed = 0
        self.failed = 0
        self._latencies.clear()


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseEventBus(ABC):
    @abstractmethod
    async def publish(
        self,
        topic: str,
        event_type: str,
        payload: dict,
        priority: EventPriority = EventPriority.NORMAL,
        source: str = "",
        correlation_id: str = "",
    ) -> str:
        """Publish an event. Returns the event_id."""
        ...

    @abstractmethod
    async def subscribe(
        self,
        topic: str,
        handler: EventHandler,
        subscriber_id: str = "",
        event_type_filter: str | None = None,
        predicate: Callable[[BusEvent], bool] | None = None,
    ) -> str:
        """Subscribe to a topic. Returns the subscription_id."""
        ...

    @abstractmethod
    async def unsubscribe(self, subscription_id: str) -> None:
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start processing events."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop — drain in-flight events then shut down."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

class InMemoryEventBus(BaseEventBus):
    """In-memory event bus using asyncio priority queues.

    Each topic gets its own ``asyncio.PriorityQueue``.  A dedicated consumer
    task per topic pulls events in priority order and fans them out to all
    matching subscribers.  Failed handler invocations are caught, logged, and
    sent to the dead-letter queue.
    """

    def __init__(self, max_queue_size: int = 10_000) -> None:
        self._max_queue_size = max_queue_size
        # topic -> PriorityQueue[BusEvent]
        self._queues: dict[str, asyncio.PriorityQueue[BusEvent]] = {}
        # subscription_id -> Subscription
        self._subscriptions: dict[str, Subscription] = {}
        # topic -> list[subscription_id]
        self._topic_subs: dict[str, list[str]] = defaultdict(list)
        # consumer tasks keyed by topic
        self._consumers: dict[str, asyncio.Task] = {}
        self._running = False
        self.metrics = EventBusMetrics()
        self.dead_letter_queue: list[DeadLetterEntry] = []

    # -- helpers -------------------------------------------------------------

    def _ensure_queue(self, topic: str) -> asyncio.PriorityQueue[BusEvent]:
        if topic not in self._queues:
            self._queues[topic] = asyncio.PriorityQueue(maxsize=self._max_queue_size)
        return self._queues[topic]

    def _ensure_consumer(self, topic: str) -> None:
        """Spin up a consumer task for *topic* if the bus is running and one
        does not already exist (or the previous one finished)."""
        if not self._running:
            return
        existing = self._consumers.get(topic)
        if existing is not None and not existing.done():
            return
        self._consumers[topic] = asyncio.create_task(
            self._consume(topic), name=f"eventbus-consumer-{topic}"
        )

    # -- public API ----------------------------------------------------------

    async def publish(
        self,
        topic: str,
        event_type: str,
        payload: dict,
        priority: EventPriority = EventPriority.NORMAL,
        source: str = "",
        correlation_id: str = "",
    ) -> str:
        event = BusEvent(
            topic=topic,
            event_type=event_type,
            payload=payload,
            priority=priority,
            source=source,
            correlation_id=correlation_id,
        )
        q = self._ensure_queue(topic)
        await q.put(event)
        self.metrics.published += 1
        logger.debug("Published %s [%s] priority=%s", event.event_id, topic, priority.name)
        # Make sure there is a consumer running for this topic
        self._ensure_consumer(topic)
        return event.event_id

    async def subscribe(
        self,
        topic: str,
        handler: EventHandler,
        subscriber_id: str = "",
        event_type_filter: str | None = None,
        predicate: Callable[[BusEvent], bool] | None = None,
    ) -> str:
        sub = Subscription(
            topic=topic,
            handler=handler,
            subscriber_id=subscriber_id or str(uuid.uuid4())[:8],
            event_type_filter=event_type_filter,
            predicate=predicate,
        )
        self._subscriptions[sub.subscription_id] = sub
        self._topic_subs[topic].append(sub.subscription_id)
        logger.debug(
            "Subscribed %s to %s (filter=%s)", sub.subscriber_id, topic, event_type_filter
        )
        return sub.subscription_id

    async def unsubscribe(self, subscription_id: str) -> None:
        sub = self._subscriptions.pop(subscription_id, None)
        if sub is None:
            return
        subs = self._topic_subs.get(sub.topic, [])
        if subscription_id in subs:
            subs.remove(subscription_id)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Start consumer tasks for any topics that already have queues
        for topic in list(self._queues):
            self._ensure_consumer(topic)
        logger.info("InMemoryEventBus started")

    async def stop(self, timeout: float = 5.0) -> None:
        if not self._running:
            return
        self._running = False
        # Drain: put a sentinel event into each queue so consumer tasks wake up
        sentinel = BusEvent(
            topic="__sentinel__",
            event_type="__stop__",
            payload={},
            priority=EventPriority.CRITICAL,
        )
        for topic, q in self._queues.items():
            try:
                q.put_nowait(sentinel)
            except asyncio.QueueFull:
                pass
        # Wait for consumers to finish
        if self._consumers:
            done, pending = await asyncio.wait(
                self._consumers.values(), timeout=timeout
            )
            for t in pending:
                t.cancel()
        self._consumers.clear()
        logger.info("InMemoryEventBus stopped")

    # -- DLQ helpers ---------------------------------------------------------

    def get_dead_letters(self, limit: int = 100) -> list[DeadLetterEntry]:
        return self.dead_letter_queue[-limit:]

    def clear_dead_letters(self) -> int:
        count = len(self.dead_letter_queue)
        self.dead_letter_queue.clear()
        return count

    # -- internal consumer ---------------------------------------------------

    async def _consume(self, topic: str) -> None:
        q = self._queues[topic]
        while self._running:
            try:
                event: BusEvent | None = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if event.event_type == "__stop__":
                # Shutdown sentinel
                break
            await self._dispatch(topic, event)
            q.task_done()

    async def _dispatch(self, topic: str, event: BusEvent) -> None:
        sub_ids = list(self._topic_subs.get(topic, []))
        for sid in sub_ids:
            sub = self._subscriptions.get(sid)
            if sub is None:
                continue
            # Apply event_type filter
            if sub.event_type_filter and event.event_type != sub.event_type_filter:
                continue
            # Apply custom predicate
            if sub.predicate is not None:
                try:
                    if not sub.predicate(event):
                        continue
                except Exception:
                    logger.warning("Predicate raised for subscriber %s", sub.subscriber_id)
                    continue

            start = time.monotonic()
            try:
                await sub.handler(event)
                elapsed = time.monotonic() - start
                self.metrics.consumed += 1
                self.metrics.record_latency(elapsed)
            except Exception as exc:
                elapsed = time.monotonic() - start
                self.metrics.failed += 1
                self.metrics.record_latency(elapsed)
                logger.error(
                    "Handler %s failed for event %s: %s",
                    sub.subscriber_id,
                    event.event_id,
                    exc,
                )
                self.dead_letter_queue.append(
                    DeadLetterEntry(
                        event=event,
                        subscriber_id=sub.subscriber_id,
                        error=str(exc),
                    )
                )


# ---------------------------------------------------------------------------
# Redis Streams stub
# ---------------------------------------------------------------------------

class RedisEventBus(BaseEventBus):
    """Redis Streams-based event bus (stub).

    Full implementation will arrive in Phase 3 when the Redis dependency is
    added.  For now every method raises ``NotImplementedError``.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379", **kwargs: Any) -> None:
        self.redis_url = redis_url
        self._extra = kwargs

    async def publish(self, topic: str, event_type: str, payload: dict,
                      priority: EventPriority = EventPriority.NORMAL,
                      source: str = "", correlation_id: str = "") -> str:
        raise NotImplementedError("Redis backend coming in Phase 3")

    async def subscribe(self, topic: str, handler: EventHandler,
                        subscriber_id: str = "", event_type_filter: str | None = None,
                        predicate: Callable[[BusEvent], bool] | None = None) -> str:
        raise NotImplementedError("Redis backend coming in Phase 3")

    async def unsubscribe(self, subscription_id: str) -> None:
        raise NotImplementedError("Redis backend coming in Phase 3")

    async def start(self) -> None:
        raise NotImplementedError("Redis backend coming in Phase 3")

    async def stop(self) -> None:
        raise NotImplementedError("Redis backend coming in Phase 3")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_event_bus(backend: str = "memory", **kwargs: Any) -> BaseEventBus:
    """Create an event bus instance.

    Args:
        backend: ``"memory"`` or ``"redis"``.
        **kwargs: Backend-specific configuration (``redis_url``, ``max_queue_size``, etc.).
    """
    if backend == "memory":
        return InMemoryEventBus(**{k: v for k, v in kwargs.items() if k == "max_queue_size"})
    if backend == "redis":
        return RedisEventBus(**kwargs)
    raise ValueError(f"Unknown event bus backend: {backend!r}")


# ---------------------------------------------------------------------------
# Predefined topic constants
# ---------------------------------------------------------------------------

class Topics:
    TICKS = "market.ticks"
    CANDLES = "market.candles"
    OPTION_CHAIN = "market.option_chain"
    ORDERS = "trading.orders"
    TRADES = "trading.trades"
    POSITIONS = "trading.positions"
    RISK_ALERTS = "risk.alerts"
    RISK_BREACH = "risk.breach"
    STRATEGY_SIGNALS = "strategy.signals"
    STRATEGY_STATUS = "strategy.status"
    PNL_UPDATE = "pnl.update"
    SYSTEM_HEALTH = "system.health"
    KILL_SWITCH = "system.kill_switch"
