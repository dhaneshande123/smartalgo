"""
Comprehensive unit tests for core/event_bus/bus.py — event bus system.

Covers InMemoryEventBus publish/subscribe/unsubscribe, priority ordering,
filtering, dead letter queue, metrics, graceful shutdown, factory function,
Topics constants, and BusEvent comparison.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from core.event_bus import (
    BusEvent,
    EventBusMetrics,
    EventPriority,
    InMemoryEventBus,
    RedisEventBus,
    Subscription,
    Topics,
    create_event_bus,
)


# ===================================================================
# Helpers
# ===================================================================


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ===================================================================
# 1. BusEvent comparison for priority queue ordering
# ===================================================================


class TestBusEvent:
    def test_critical_before_normal(self):
        critical = BusEvent(
            topic="t", event_type="A", payload={},
            priority=EventPriority.CRITICAL, timestamp=_now(),
        )
        normal = BusEvent(
            topic="t", event_type="A", payload={},
            priority=EventPriority.NORMAL, timestamp=_now(),
        )
        # Critical should sort before normal (< returns True)
        assert critical < normal

    def test_same_priority_earlier_first(self):
        import time
        t1 = _now()
        time.sleep(0.001)
        t2 = _now()
        e1 = BusEvent(topic="t", event_type="A", payload={}, priority=EventPriority.NORMAL, timestamp=t1)
        e2 = BusEvent(topic="t", event_type="A", payload={}, priority=EventPriority.NORMAL, timestamp=t2)
        assert e1 < e2  # earlier timestamp first

    def test_high_before_low(self):
        high = BusEvent(topic="t", event_type="A", payload={}, priority=EventPriority.HIGH)
        low = BusEvent(topic="t", event_type="A", payload={}, priority=EventPriority.LOW)
        assert high < low

    def test_lt_not_implemented_for_non_busevent(self):
        e = BusEvent(topic="t", event_type="A", payload={})
        assert e.__lt__("not an event") is NotImplemented

    def test_default_fields(self):
        e = BusEvent(topic="t", event_type="X", payload={"k": "v"})
        assert e.priority == EventPriority.NORMAL
        assert e.source == ""
        assert e.correlation_id == ""
        assert len(e.event_id) > 0
        assert e.timestamp is not None


# ===================================================================
# 2. EventPriority
# ===================================================================


class TestEventPriority:
    def test_values(self):
        assert EventPriority.LOW == 0
        assert EventPriority.NORMAL == 1
        assert EventPriority.HIGH == 2
        assert EventPriority.CRITICAL == 3

    def test_ordering(self):
        assert EventPriority.LOW < EventPriority.NORMAL < EventPriority.HIGH < EventPriority.CRITICAL


# ===================================================================
# 3. InMemoryEventBus — publish / subscribe / unsubscribe
# ===================================================================


class TestInMemoryEventBusPubSub:
    @pytest.fixture
    async def bus(self):
        b = InMemoryEventBus()
        await b.start()
        yield b
        await b.stop()

    async def test_publish_returns_event_id(self, bus):
        eid = await bus.publish("topic.a", "TYPE_A", {"key": "val"})
        assert isinstance(eid, str)
        assert len(eid) > 0

    async def test_subscribe_returns_subscription_id(self, bus):
        async def handler(event):
            pass
        sid = await bus.subscribe("topic.a", handler, subscriber_id="s1")
        assert isinstance(sid, str)
        assert len(sid) > 0

    async def test_publish_and_receive(self, bus):
        received = []

        async def handler(event: BusEvent):
            received.append(event)

        await bus.subscribe("topic.a", handler, subscriber_id="sub1")
        await bus.publish("topic.a", "TYPE_A", {"data": 123})

        # Give the consumer a moment to process
        await asyncio.sleep(0.2)
        assert len(received) == 1
        assert received[0].payload == {"data": 123}
        assert received[0].event_type == "TYPE_A"

    async def test_multiple_subscribers_receive_same_event(self, bus):
        received1 = []
        received2 = []

        async def h1(event):
            received1.append(event)

        async def h2(event):
            received2.append(event)

        await bus.subscribe("topic.b", h1, subscriber_id="s1")
        await bus.subscribe("topic.b", h2, subscriber_id="s2")
        await bus.publish("topic.b", "T", {"x": 1})

        await asyncio.sleep(0.2)
        assert len(received1) == 1
        assert len(received2) == 1

    async def test_unsubscribe(self, bus):
        received = []

        async def handler(event):
            received.append(event)

        sid = await bus.subscribe("topic.c", handler, subscriber_id="sub-c")
        await bus.publish("topic.c", "T", {"before": True})
        await asyncio.sleep(0.2)
        assert len(received) == 1

        await bus.unsubscribe(sid)
        await bus.publish("topic.c", "T", {"after": True})
        await asyncio.sleep(0.2)
        assert len(received) == 1  # no new events

    async def test_unsubscribe_nonexistent_is_noop(self, bus):
        await bus.unsubscribe("nonexistent-id")  # should not raise

    async def test_different_topics_isolated(self, bus):
        received_a = []
        received_b = []

        async def ha(e):
            received_a.append(e)

        async def hb(e):
            received_b.append(e)

        await bus.subscribe("topic.a", ha)
        await bus.subscribe("topic.b", hb)

        await bus.publish("topic.a", "T", {"for": "a"})
        await asyncio.sleep(0.2)
        assert len(received_a) == 1
        assert len(received_b) == 0


# ===================================================================
# 4. Event priority ordering
# ===================================================================


class TestPriorityOrdering:
    async def test_critical_processed_before_normal(self):
        bus = InMemoryEventBus()
        order = []

        async def handler(event: BusEvent):
            order.append(event.priority)

        await bus.subscribe("t", handler, subscriber_id="prio-sub")

        # Publish normal first, then critical — queue is not yet being consumed
        q = bus._ensure_queue("t")
        normal_event = BusEvent(topic="t", event_type="T", payload={}, priority=EventPriority.NORMAL)
        low_event = BusEvent(topic="t", event_type="T", payload={}, priority=EventPriority.LOW)
        critical_event = BusEvent(topic="t", event_type="T", payload={}, priority=EventPriority.CRITICAL)

        await q.put(low_event)
        await q.put(normal_event)
        await q.put(critical_event)

        bus.metrics.published = 3

        await bus.start()
        await asyncio.sleep(0.3)
        await bus.stop()

        # Critical should be processed first (highest priority)
        assert len(order) == 3
        assert order[0] == EventPriority.CRITICAL
        assert order[-1] == EventPriority.LOW


# ===================================================================
# 5. Event type filtering
# ===================================================================


class TestEventTypeFiltering:
    async def test_event_type_filter(self):
        bus = InMemoryEventBus()
        await bus.start()

        received = []

        async def handler(event: BusEvent):
            received.append(event)

        await bus.subscribe(
            "topic.x", handler,
            subscriber_id="filtered",
            event_type_filter="SPECIAL",
        )

        await bus.publish("topic.x", "NORMAL", {"a": 1})
        await bus.publish("topic.x", "SPECIAL", {"b": 2})
        await bus.publish("topic.x", "OTHER", {"c": 3})

        await asyncio.sleep(0.3)
        await bus.stop()

        assert len(received) == 1
        assert received[0].event_type == "SPECIAL"


# ===================================================================
# 6. Custom predicate filtering
# ===================================================================


class TestPredicateFiltering:
    async def test_custom_predicate(self):
        bus = InMemoryEventBus()
        await bus.start()

        received = []

        async def handler(event: BusEvent):
            received.append(event)

        def only_important(event: BusEvent) -> bool:
            return event.payload.get("important", False)

        await bus.subscribe(
            "topic.pred", handler,
            subscriber_id="pred-sub",
            predicate=only_important,
        )

        await bus.publish("topic.pred", "T", {"important": False})
        await bus.publish("topic.pred", "T", {"important": True, "data": "yes"})

        await asyncio.sleep(0.3)
        await bus.stop()

        assert len(received) == 1
        assert received[0].payload["data"] == "yes"

    async def test_predicate_exception_skips_event(self):
        bus = InMemoryEventBus()
        await bus.start()

        received = []

        async def handler(event: BusEvent):
            received.append(event)

        def bad_predicate(event: BusEvent) -> bool:
            raise RuntimeError("predicate crash")

        await bus.subscribe(
            "topic.bad", handler,
            subscriber_id="bad-pred",
            predicate=bad_predicate,
        )

        await bus.publish("topic.bad", "T", {})
        await asyncio.sleep(0.3)
        await bus.stop()

        assert len(received) == 0  # skipped due to predicate error


# ===================================================================
# 7. Dead letter queue on handler failure
# ===================================================================


class TestDeadLetterQueue:
    async def test_failed_handler_goes_to_dlq(self):
        bus = InMemoryEventBus()
        await bus.start()

        async def bad_handler(event: BusEvent):
            raise ValueError("handler error")

        await bus.subscribe("topic.dlq", bad_handler, subscriber_id="bad-sub")
        await bus.publish("topic.dlq", "T", {"data": "will_fail"})

        await asyncio.sleep(0.3)
        await bus.stop()

        dlq = bus.get_dead_letters()
        assert len(dlq) == 1
        assert dlq[0].subscriber_id == "bad-sub"
        assert "handler error" in dlq[0].error
        assert dlq[0].event.payload == {"data": "will_fail"}

    async def test_clear_dead_letters(self):
        bus = InMemoryEventBus()
        await bus.start()

        async def bad_handler(event):
            raise RuntimeError("fail")

        await bus.subscribe("topic.dlq2", bad_handler, subscriber_id="s")
        await bus.publish("topic.dlq2", "T", {})
        await asyncio.sleep(0.3)
        await bus.stop()

        assert len(bus.get_dead_letters()) == 1
        count = bus.clear_dead_letters()
        assert count == 1
        assert len(bus.get_dead_letters()) == 0

    async def test_dlq_limit(self):
        from core.event_bus.bus import DeadLetterEntry

        bus = InMemoryEventBus()
        for i in range(150):
            bus.dead_letter_queue.append(
                DeadLetterEntry(
                    event=BusEvent(topic="t", event_type="T", payload={"i": i}),
                    subscriber_id="s",
                    error="err",
                )
            )
        result = bus.get_dead_letters(limit=10)
        assert len(result) == 10


# ===================================================================
# 8. Metrics tracking
# ===================================================================


class TestMetrics:
    async def test_published_count(self):
        bus = InMemoryEventBus()
        await bus.start()
        await bus.publish("t", "T", {})
        await bus.publish("t", "T", {})
        assert bus.metrics.published == 2
        await bus.stop()

    async def test_consumed_count(self):
        bus = InMemoryEventBus()
        await bus.start()

        async def handler(e):
            pass

        await bus.subscribe("t", handler, subscriber_id="s")
        await bus.publish("t", "T", {})
        await asyncio.sleep(0.3)
        await bus.stop()

        assert bus.metrics.consumed == 1

    async def test_failed_count(self):
        bus = InMemoryEventBus()
        await bus.start()

        async def bad(e):
            raise RuntimeError("fail")

        await bus.subscribe("t", bad, subscriber_id="s")
        await bus.publish("t", "T", {})
        await asyncio.sleep(0.3)
        await bus.stop()

        assert bus.metrics.failed == 1

    def test_metrics_snapshot(self):
        m = EventBusMetrics()
        m.published = 10
        m.consumed = 8
        m.failed = 2
        m.record_latency(0.001)
        m.record_latency(0.003)
        snap = m.snapshot()
        assert snap["published"] == 10
        assert snap["consumed"] == 8
        assert snap["failed"] == 2
        assert snap["avg_latency_ms"] > 0
        assert snap["max_latency_ms"] == pytest.approx(3.0, abs=0.1)

    def test_metrics_reset(self):
        m = EventBusMetrics()
        m.published = 5
        m.consumed = 3
        m.failed = 1
        m.record_latency(0.01)
        m.reset()
        assert m.published == 0
        assert m.consumed == 0
        assert m.failed == 0
        assert m.avg_latency == 0.0

    def test_avg_latency_empty(self):
        m = EventBusMetrics()
        assert m.avg_latency == 0.0
        assert m.max_latency == 0.0

    async def test_latency_recorded(self):
        bus = InMemoryEventBus()
        await bus.start()

        async def handler(e):
            pass

        await bus.subscribe("t", handler, subscriber_id="s")
        await bus.publish("t", "T", {})
        await asyncio.sleep(0.3)
        await bus.stop()

        assert bus.metrics.avg_latency >= 0
        assert len(bus.metrics._latencies) > 0


# ===================================================================
# 9. Graceful shutdown
# ===================================================================


class TestGracefulShutdown:
    async def test_stop_drains_queue(self):
        bus = InMemoryEventBus()
        received = []

        async def handler(event: BusEvent):
            received.append(event)

        await bus.subscribe("t", handler, subscriber_id="s")
        await bus.start()

        await bus.publish("t", "T", {"msg": "last"})
        await asyncio.sleep(0.2)
        await bus.stop()

        assert len(received) == 1

    async def test_stop_idempotent(self):
        bus = InMemoryEventBus()
        await bus.start()
        await bus.stop()
        await bus.stop()  # should not raise

    async def test_start_idempotent(self):
        bus = InMemoryEventBus()
        await bus.start()
        await bus.start()  # should not raise
        await bus.stop()

    async def test_running_flag(self):
        bus = InMemoryEventBus()
        assert bus._running is False
        await bus.start()
        assert bus._running is True
        await bus.stop()
        assert bus._running is False


# ===================================================================
# 10. create_event_bus factory
# ===================================================================


class TestCreateEventBus:
    def test_memory_backend(self):
        bus = create_event_bus("memory")
        assert isinstance(bus, InMemoryEventBus)

    def test_memory_backend_with_queue_size(self):
        bus = create_event_bus("memory", max_queue_size=500)
        assert isinstance(bus, InMemoryEventBus)
        assert bus._max_queue_size == 500

    def test_redis_backend(self):
        bus = create_event_bus("redis", redis_url="redis://myhost:6379")
        assert isinstance(bus, RedisEventBus)
        assert bus.redis_url == "redis://myhost:6379"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown event bus backend"):
            create_event_bus("kafka")

    def test_default_backend_is_memory(self):
        bus = create_event_bus()
        assert isinstance(bus, InMemoryEventBus)


# ===================================================================
# 11. Topics constants
# ===================================================================


class TestTopics:
    def test_tick_topic(self):
        assert Topics.TICKS == "market.ticks"

    def test_candles_topic(self):
        assert Topics.CANDLES == "market.candles"

    def test_orders_topic(self):
        assert Topics.ORDERS == "trading.orders"

    def test_trades_topic(self):
        assert Topics.TRADES == "trading.trades"

    def test_risk_alerts_topic(self):
        assert Topics.RISK_ALERTS == "risk.alerts"

    def test_kill_switch_topic(self):
        assert Topics.KILL_SWITCH == "system.kill_switch"

    def test_all_topics_are_strings(self):
        for attr in dir(Topics):
            if not attr.startswith("_"):
                assert isinstance(getattr(Topics, attr), str)

    def test_strategy_signals_topic(self):
        assert Topics.STRATEGY_SIGNALS == "strategy.signals"

    def test_pnl_update_topic(self):
        assert Topics.PNL_UPDATE == "pnl.update"


# ===================================================================
# 12. RedisEventBus stub
# ===================================================================


class TestRedisEventBusStub:
    async def test_publish_raises_not_implemented(self):
        bus = RedisEventBus()
        with pytest.raises(NotImplementedError):
            await bus.publish("t", "T", {})

    async def test_subscribe_raises_not_implemented(self):
        bus = RedisEventBus()
        with pytest.raises(NotImplementedError):
            await bus.subscribe("t", lambda e: None)

    async def test_unsubscribe_raises_not_implemented(self):
        bus = RedisEventBus()
        with pytest.raises(NotImplementedError):
            await bus.unsubscribe("id")

    async def test_start_raises_not_implemented(self):
        bus = RedisEventBus()
        with pytest.raises(NotImplementedError):
            await bus.start()

    async def test_stop_raises_not_implemented(self):
        bus = RedisEventBus()
        with pytest.raises(NotImplementedError):
            await bus.stop()


# ===================================================================
# 13. Subscription dataclass
# ===================================================================


class TestSubscription:
    def test_defaults(self):
        async def h(e):
            pass

        s = Subscription(topic="t", handler=h, subscriber_id="s1")
        assert s.topic == "t"
        assert s.subscriber_id == "s1"
        assert s.event_type_filter is None
        assert s.predicate is None
        assert len(s.subscription_id) > 0
