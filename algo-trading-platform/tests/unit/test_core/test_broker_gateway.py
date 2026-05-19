"""
Comprehensive unit tests for Phase 2 — Broker Gateway.

Covers:
    - AsyncRateLimiter (token bucket, acquire, try_acquire, metrics, reset)
    - BrokerManager (registration, connect, failover, kill switch, health)
    - OrderReconciler (discrepancy detection, handlers, start/stop, history)
    - WebSocketManager (initial state, metrics)
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.broker_gateway.base import BaseBroker
from core.broker_gateway.rate_limiter import AsyncRateLimiter
from core.broker_gateway.manager import BrokerHealth, BrokerManager
from core.broker_gateway.reconciliation import (
    DiscrepancyType,
    OrderReconciler,
    ReconciliationResult,
)
from core.broker_gateway.ws_manager import WebSocketManager
from core.models import (
    Exchange,
    Instrument,
    InstrumentType,
    MarginInfo,
    OptionChain,
    Order,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
    Segment,
    Trade,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_instrument() -> Instrument:
    """Create a simple equity instrument for test orders."""
    return Instrument(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        instrument_type=InstrumentType.STOCK,
        lot_size=1,
        token="738561",
    )


def _make_order(
    order_id: str = "ORD001",
    status: OrderStatus = OrderStatus.PENDING,
    broker_order_id: str | None = None,
    filled_quantity: int = 0,
    average_price: Decimal = Decimal("0"),
) -> Order:
    """Create a minimal Order for testing."""
    return Order(
        order_id=order_id,
        instrument=_make_instrument(),
        order_type=OrderType.MARKET,
        side=OrderSide.BUY,
        product_type=ProductType.MIS,
        quantity=10,
        status=status,
        broker_order_id=broker_order_id,
        filled_quantity=filled_quantity,
        average_price=average_price,
    )


class MockBroker(BaseBroker):
    """Concrete mock broker that implements all BaseBroker abstract methods.

    Every method is backed by a controllable return value or exception so
    tests can configure behaviour per-scenario.
    """

    def __init__(
        self,
        name: str = "mock",
        *,
        connect_raises: Exception | None = None,
        place_order_raises: Exception | None = None,
        is_connected_value: bool = True,
    ) -> None:
        self.name = name
        self._connected = False
        self._connect_raises = connect_raises
        self._place_order_raises = place_order_raises
        self._is_connected_value = is_connected_value
        self._orders: list[Order] = []
        self._positions: list[Position] = []
        self._cancel_all_responses: list[OrderResponse] = []

    # -- lifecycle --

    async def connect(self) -> None:
        if self._connect_raises:
            raise self._connect_raises
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected(self) -> bool:
        return self._is_connected_value

    # -- orders --

    async def place_order(self, order: Order) -> OrderResponse:
        if self._place_order_raises:
            raise self._place_order_raises
        return OrderResponse(
            success=True,
            order_id=order.order_id,
            broker_order_id=f"BRK-{order.order_id}",
            message="placed",
            status=OrderStatus.PLACED,
        )

    async def modify_order(self, order_id: str, modifications: dict[str, Any]) -> OrderResponse:
        return OrderResponse(success=True, order_id=order_id, message="modified")

    async def cancel_order(self, order_id: str) -> OrderResponse:
        return OrderResponse(success=True, order_id=order_id, message="cancelled")

    async def cancel_all_orders(self) -> list[OrderResponse]:
        return self._cancel_all_responses

    # -- positions / account --

    async def get_positions(self) -> list[Position]:
        return self._positions

    async def get_order_book(self) -> list[Order]:
        return list(self._orders)

    async def get_trade_book(self) -> list[Trade]:
        return []

    async def get_margins(self) -> MarginInfo:
        return MarginInfo()

    # -- market data --

    async def get_ltp(self, instruments: list[str]) -> dict[str, float]:
        return {i: 100.0 for i in instruments}

    async def get_option_chain(self, symbol: str, expiry: date) -> OptionChain:
        return OptionChain(
            underlying_symbol=symbol,
            underlying_price=Decimal("24000"),
            expiry=expiry,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]:
        return []

    # -- streaming --

    async def subscribe_ticks(self, instruments: list[str], callback: Any) -> None:
        pass

    async def unsubscribe_ticks(self, instruments: list[str]) -> None:
        pass

    async def subscribe_order_updates(self, callback: Any) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 1.  AsyncRateLimiter Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAsyncRateLimiter:
    """Tests for the token-bucket rate limiter."""

    @pytest.mark.asyncio
    async def test_initial_tokens_equal_burst(self):
        """Bucket starts full — available tokens should equal burst."""
        limiter = AsyncRateLimiter(rate=5.0, burst=5)
        assert limiter.available_tokens == pytest.approx(5.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_acquire_consumes_token(self):
        """A single acquire() should reduce available tokens by 1."""
        limiter = AsyncRateLimiter(rate=10.0, burst=5)
        await limiter.acquire()
        assert limiter.available_tokens < 5.0
        assert limiter.total_requests == 1

    @pytest.mark.asyncio
    async def test_try_acquire_returns_true_when_tokens_available(self):
        """try_acquire() succeeds when the bucket has tokens."""
        limiter = AsyncRateLimiter(rate=5.0, burst=3)
        result = await limiter.try_acquire()
        assert result is True
        assert limiter.total_requests == 1

    @pytest.mark.asyncio
    async def test_try_acquire_returns_false_when_empty(self):
        """try_acquire() returns False when no tokens are left."""
        limiter = AsyncRateLimiter(rate=1.0, burst=1)
        # Drain the single token.
        ok = await limiter.try_acquire()
        assert ok is True
        # Immediately try again — should fail without waiting.
        ok = await limiter.try_acquire()
        assert ok is False

    @pytest.mark.asyncio
    async def test_acquire_blocks_when_empty(self):
        """acquire() should block (sleep) when the bucket is empty."""
        limiter = AsyncRateLimiter(rate=100.0, burst=1)
        # Drain the token.
        await limiter.acquire()
        # Next acquire must wait for refill.  With rate=100 the wait is ~10 ms.
        await limiter.acquire()
        assert limiter.total_requests == 2
        assert limiter.total_waits >= 1

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self):
        """After draining, tokens should refill based on rate * elapsed time."""
        limiter = AsyncRateLimiter(rate=1000.0, burst=5)
        # Drain all tokens.
        for _ in range(5):
            await limiter.acquire()
        assert limiter.available_tokens < 1.0
        # Wait a small amount and check refill.
        await asyncio.sleep(0.01)  # 10 ms -> ~10 tokens at 1000/s, capped at burst=5
        assert limiter.available_tokens > 0.0

    @pytest.mark.asyncio
    async def test_rate_limit_respects_configured_rate(self):
        """With rate=3 and burst=1, three sequential acquires need ~0.66 s."""
        limiter = AsyncRateLimiter(rate=100.0, burst=1)
        # Perform 3 acquires — the first is free, the next two wait.
        for _ in range(3):
            await limiter.acquire()
        assert limiter.total_requests == 3
        # At least 1 wait should have occurred.
        assert limiter.total_waits >= 1

    @pytest.mark.asyncio
    async def test_burst_allows_multiple_tokens_at_once(self):
        """acquire(tokens=N) should succeed when burst >= N."""
        limiter = AsyncRateLimiter(rate=5.0, burst=5)
        await limiter.acquire(tokens=3)
        assert limiter.total_requests == 1
        assert limiter.available_tokens == pytest.approx(2.0, abs=0.2)

    @pytest.mark.asyncio
    async def test_acquire_rejects_tokens_exceeding_burst(self):
        """acquire(tokens) raises ValueError if tokens > burst."""
        limiter = AsyncRateLimiter(rate=5.0, burst=3)
        with pytest.raises(ValueError, match="Cannot acquire 5 tokens"):
            await limiter.acquire(tokens=5)

    @pytest.mark.asyncio
    async def test_reset_restores_full_capacity(self):
        """reset() fills the bucket back to burst size."""
        limiter = AsyncRateLimiter(rate=5.0, burst=5)
        # Drain.
        for _ in range(5):
            await limiter.acquire()
        assert limiter.available_tokens < 1.0
        limiter.reset()
        assert limiter.available_tokens == pytest.approx(5.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_metrics_tracking(self):
        """Metrics dict reports total_requests, total_waits, rate, burst."""
        limiter = AsyncRateLimiter(rate=100.0, burst=2)
        await limiter.acquire()
        await limiter.acquire()
        # Third acquire will need to wait.
        await limiter.acquire()

        m = limiter.metrics
        assert m["total_requests"] == 3
        assert m["total_waits"] >= 1
        assert m["rate"] == 100.0
        assert m["burst"] == 2

    def test_constructor_rejects_nonpositive_rate(self):
        """Rate <= 0 should raise ValueError."""
        with pytest.raises(ValueError, match="rate must be positive"):
            AsyncRateLimiter(rate=0, burst=1)
        with pytest.raises(ValueError, match="rate must be positive"):
            AsyncRateLimiter(rate=-1.0, burst=1)

    def test_constructor_rejects_burst_less_than_one(self):
        """Burst < 1 should raise ValueError."""
        with pytest.raises(ValueError, match="burst must be >= 1"):
            AsyncRateLimiter(rate=1.0, burst=0)


# ═══════════════════════════════════════════════════════════════════════════
# 2.  BrokerManager Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestBrokerHealth:
    """Tests for the BrokerHealth tracker."""

    def test_initial_state(self):
        h = BrokerHealth("test_broker")
        assert h.is_connected is False
        assert h.consecutive_errors == 0
        assert h.total_requests == 0

    def test_record_success_resets_consecutive_errors(self):
        h = BrokerHealth("test_broker")
        h.is_connected = True
        h.record_error()
        h.record_error()
        assert h.consecutive_errors == 2
        h.record_success(latency_ms=5.0)
        assert h.consecutive_errors == 0
        assert h.total_requests == 3  # 2 errors + 1 success

    def test_record_success_tracks_latency(self):
        h = BrokerHealth("test_broker")
        h.is_connected = True
        h.record_success(10.0)
        h.record_success(20.0)
        assert h.avg_latency_ms == pytest.approx(15.0)

    def test_is_healthy_requires_connected_and_low_errors(self):
        h = BrokerHealth("test_broker")
        h.is_connected = True
        assert h.is_healthy is True
        # Push consecutive errors to 5 -> unhealthy.
        for _ in range(5):
            h.record_error()
        assert h.is_healthy is False

    def test_snapshot_returns_dict(self):
        h = BrokerHealth("test_broker")
        h.is_connected = True
        h.record_success(10.0)
        snap = h.snapshot()
        assert snap["broker"] == "test_broker"
        assert snap["connected"] is True
        assert snap["healthy"] is True


class TestBrokerManager:
    """Tests for BrokerManager routing, failover, and aggregation."""

    def _make_manager(
        self,
        primary: MockBroker | None = None,
        backup: MockBroker | None = None,
    ) -> BrokerManager:
        p = primary or MockBroker(name="primary")
        b = backup or MockBroker(name="backup")
        mgr = BrokerManager(
            primary_broker_name="primary",
            backup_broker_name="backup",
        )
        mgr.register_broker(p)
        mgr.register_broker(b)
        return mgr

    def test_register_broker_adds_broker(self):
        mgr = BrokerManager()
        broker = MockBroker(name="zerodha")
        mgr.register_broker(broker)
        assert "zerodha" in mgr.all_brokers
        assert mgr.get_broker("zerodha") is broker

    def test_get_broker_raises_for_unknown(self):
        mgr = BrokerManager()
        with pytest.raises(KeyError, match="not registered"):
            mgr.get_broker("nonexistent")

    @pytest.mark.asyncio
    async def test_connect_all_connects_all_brokers(self):
        mgr = self._make_manager()
        results = await mgr.connect_all()
        assert results["primary"] is True
        assert results["backup"] is True
        assert mgr._active_broker_name == "primary"

    @pytest.mark.asyncio
    async def test_connect_all_falls_back_to_backup(self):
        primary = MockBroker(name="primary", connect_raises=ConnectionError("down"))
        mgr = self._make_manager(primary=primary)
        results = await mgr.connect_all()
        assert results["primary"] is False
        assert results["backup"] is True
        assert mgr._active_broker_name == "backup"

    @pytest.mark.asyncio
    async def test_place_order_routes_to_active(self):
        mgr = self._make_manager()
        await mgr.connect_all()
        order = _make_order()
        resp = await mgr.place_order(order)
        assert resp.success is True
        assert resp.broker_order_id == f"BRK-{order.order_id}"

    @pytest.mark.asyncio
    async def test_place_order_fails_over_to_backup(self):
        primary = MockBroker(name="primary", place_order_raises=RuntimeError("API error"))
        backup = MockBroker(name="backup")
        mgr = self._make_manager(primary=primary, backup=backup)
        await mgr.connect_all()
        # Primary should fail, backup should succeed.
        order = _make_order()
        resp = await mgr.place_order(order)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_place_order_returns_error_when_all_fail(self):
        primary = MockBroker(name="primary", place_order_raises=RuntimeError("fail"))
        backup = MockBroker(name="backup", place_order_raises=RuntimeError("also fail"))
        mgr = self._make_manager(primary=primary, backup=backup)
        await mgr.connect_all()
        order = _make_order()
        resp = await mgr.place_order(order)
        assert resp.success is False
        assert resp.status == OrderStatus.ERROR

    @pytest.mark.asyncio
    async def test_kill_switch_cancels_on_all_brokers(self):
        primary = MockBroker(name="primary")
        primary._cancel_all_responses = [
            OrderResponse(success=True, order_id="O1", message="cancelled"),
        ]
        backup = MockBroker(name="backup")
        backup._cancel_all_responses = [
            OrderResponse(success=True, order_id="O2", message="cancelled"),
        ]
        mgr = self._make_manager(primary=primary, backup=backup)
        await mgr.connect_all()
        results = await mgr.kill_switch()
        assert "primary" in results
        assert "backup" in results
        assert len(results["primary"]) == 1
        assert len(results["backup"]) == 1

    @pytest.mark.asyncio
    async def test_get_all_positions_aggregates(self):
        primary = MockBroker(name="primary")
        primary._positions = [
            Position(instrument=_make_instrument(), quantity=10),
        ]
        backup = MockBroker(name="backup")
        backup._positions = [
            Position(instrument=_make_instrument(), quantity=-5),
        ]
        mgr = self._make_manager(primary=primary, backup=backup)
        await mgr.connect_all()
        positions = await mgr.get_all_positions()
        assert "primary" in positions
        assert "backup" in positions
        assert len(positions["primary"]) == 1
        assert len(positions["backup"]) == 1

    @pytest.mark.asyncio
    async def test_health_check_auto_failover_when_primary_down(self):
        primary = MockBroker(name="primary", is_connected_value=False)
        backup = MockBroker(name="backup", is_connected_value=True)
        mgr = self._make_manager(primary=primary, backup=backup)
        await mgr.connect_all()
        # Force active to primary (simulating it was up before).
        mgr._active_broker_name = "primary"
        mgr._health["primary"].is_connected = True

        results = await mgr.health_check()
        assert results["primary"] is False
        assert results["backup"] is True
        # Manager should have switched to backup.
        assert mgr._active_broker_name == "backup"

    @pytest.mark.asyncio
    async def test_health_check_restores_primary_when_back(self):
        primary = MockBroker(name="primary", is_connected_value=True)
        backup = MockBroker(name="backup", is_connected_value=True)
        mgr = self._make_manager(primary=primary, backup=backup)
        await mgr.connect_all()
        # Simulate failover had occurred.
        mgr._active_broker_name = "backup"

        results = await mgr.health_check()
        assert results["primary"] is True
        # Should restore primary.
        assert mgr._active_broker_name == "primary"

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        mgr = self._make_manager()
        await mgr.connect_all()
        await mgr.disconnect_all()
        for h in mgr._health.values():
            assert h.is_connected is False


# ═══════════════════════════════════════════════════════════════════════════
# 3.  OrderReconciler Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestOrderReconciler:
    """Tests for order reconciliation engine."""

    def _setup_reconciler(
        self,
        local_orders: dict[str, Order] | None = None,
        broker_orders: list[Order] | None = None,
    ) -> OrderReconciler:
        broker = MockBroker(name="test_broker")
        broker._orders = broker_orders or []

        async def get_local() -> dict[str, Order]:
            return local_orders or {}

        return OrderReconciler(broker=broker, get_local_orders=get_local)

    @pytest.mark.asyncio
    async def test_no_discrepancies_when_matching(self):
        """Clean reconciliation when local and broker match exactly."""
        local = {
            "ORD1": _make_order(
                order_id="ORD1",
                status=OrderStatus.FILLED,
                broker_order_id="BRK1",
                filled_quantity=10,
                average_price=Decimal("100"),
            ),
        }
        broker = [
            _make_order(
                order_id="ORD1",
                status=OrderStatus.FILLED,
                broker_order_id="BRK1",
                filled_quantity=10,
                average_price=Decimal("100"),
            ),
        ]
        reconciler = self._setup_reconciler(local_orders=local, broker_orders=broker)
        result = await reconciler.reconcile_once()
        assert result.success is True
        assert result.discrepancies_found == 0

    @pytest.mark.asyncio
    async def test_detects_status_mismatch(self):
        """Detects when local status differs from broker status."""
        local = {
            "ORD1": _make_order(
                order_id="ORD1",
                status=OrderStatus.OPEN,
                broker_order_id="BRK1",
            ),
        }
        broker = [
            _make_order(
                order_id="ORD1",
                status=OrderStatus.FILLED,
                broker_order_id="BRK1",
                filled_quantity=10,
                average_price=Decimal("100"),
            ),
        ]
        reconciler = self._setup_reconciler(local_orders=local, broker_orders=broker)
        result = await reconciler.reconcile_once()
        assert result.discrepancies_found >= 1
        types = [d.discrepancy_type for d in result.discrepancies]
        assert DiscrepancyType.STATUS_MISMATCH in types

    @pytest.mark.asyncio
    async def test_detects_fill_mismatch(self):
        """Detects when filled quantities differ."""
        local = {
            "ORD1": _make_order(
                order_id="ORD1",
                status=OrderStatus.FILLED,
                broker_order_id="BRK1",
                filled_quantity=5,
                average_price=Decimal("100"),
            ),
        }
        broker = [
            _make_order(
                order_id="ORD1",
                status=OrderStatus.FILLED,
                broker_order_id="BRK1",
                filled_quantity=10,
                average_price=Decimal("100"),
            ),
        ]
        reconciler = self._setup_reconciler(local_orders=local, broker_orders=broker)
        result = await reconciler.reconcile_once()
        types = [d.discrepancy_type for d in result.discrepancies]
        assert DiscrepancyType.FILL_MISMATCH in types

    @pytest.mark.asyncio
    async def test_detects_missing_local(self):
        """Detects broker orders not tracked locally."""
        # No local orders, but broker has an OPEN order.
        broker = [
            _make_order(
                order_id="ORD_BROKER",
                status=OrderStatus.OPEN,
                broker_order_id="BRK_ORPHAN",
            ),
        ]
        reconciler = self._setup_reconciler(local_orders={}, broker_orders=broker)
        result = await reconciler.reconcile_once()
        types = [d.discrepancy_type for d in result.discrepancies]
        assert DiscrepancyType.MISSING_LOCAL in types

    @pytest.mark.asyncio
    async def test_detects_missing_broker(self):
        """Detects local orders missing from the broker."""
        local = {
            "ORD1": _make_order(
                order_id="ORD1",
                status=OrderStatus.OPEN,
                broker_order_id="BRK1",
            ),
        }
        # Broker returns empty order book.
        reconciler = self._setup_reconciler(local_orders=local, broker_orders=[])
        result = await reconciler.reconcile_once()
        types = [d.discrepancy_type for d in result.discrepancies]
        assert DiscrepancyType.MISSING_BROKER in types

    @pytest.mark.asyncio
    async def test_discrepancy_handler_called(self):
        """Registered handler is called when discrepancies are found."""
        local = {
            "ORD1": _make_order(
                order_id="ORD1",
                status=OrderStatus.OPEN,
                broker_order_id="BRK1",
            ),
        }
        broker = [
            _make_order(
                order_id="ORD1",
                status=OrderStatus.FILLED,
                broker_order_id="BRK1",
                filled_quantity=10,
                average_price=Decimal("100"),
            ),
        ]
        reconciler = self._setup_reconciler(local_orders=local, broker_orders=broker)

        handler = AsyncMock()
        reconciler.on_discrepancy(handler)

        await reconciler.reconcile_once()
        handler.assert_awaited_once()
        # Handler receives a list of Discrepancy objects.
        args = handler.call_args[0]
        assert len(args[0]) >= 1

    @pytest.mark.asyncio
    async def test_start_and_stop_periodic(self):
        """start() launches a background task; stop() cancels it."""
        reconciler = self._setup_reconciler()
        await reconciler.start(interval=100.0)  # Long interval so it won't actually run.
        assert reconciler._running is True
        assert reconciler._task is not None

        await reconciler.stop()
        assert reconciler._running is False
        assert reconciler._task is None

    @pytest.mark.asyncio
    async def test_history_is_maintained(self):
        """Each reconcile_once() adds to history."""
        reconciler = self._setup_reconciler()
        await reconciler.reconcile_once()
        await reconciler.reconcile_once()
        assert len(reconciler.history) == 2
        assert reconciler.last_result is not None
        assert reconciler.last_result.success is True

    @pytest.mark.asyncio
    async def test_no_discrepancy_for_pending_missing_on_broker(self):
        """PENDING orders missing from broker are expected — no discrepancy."""
        local = {
            "ORD1": _make_order(
                order_id="ORD1",
                status=OrderStatus.PENDING,
                broker_order_id="BRK1",
            ),
        }
        reconciler = self._setup_reconciler(local_orders=local, broker_orders=[])
        result = await reconciler.reconcile_once()
        assert result.discrepancies_found == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4.  WebSocketManager Tests (basic — no real WS)
# ═══════════════════════════════════════════════════════════════════════════


class TestWebSocketManager:
    """Basic state and metrics tests for WebSocketManager.

    We cannot test real WS connections in unit tests, but we can verify
    the object initialises correctly and reports the right state.
    """

    def _make_ws(self) -> WebSocketManager:
        return WebSocketManager(
            url="wss://broker.example.com/ws",
            on_message=AsyncMock(),
            name="test-ws",
        )

    def test_initial_state_is_disconnected(self):
        ws = self._make_ws()
        assert ws.state == WebSocketManager.State.DISCONNECTED

    def test_is_connected_false_before_connect(self):
        ws = self._make_ws()
        assert ws.is_connected is False

    def test_metrics_initialized_to_zero(self):
        ws = self._make_ws()
        m = ws.metrics
        assert m["messages_received"] == 0
        assert m["messages_sent"] == 0
        assert m["reconnect_count"] == 0
        assert m["uptime_seconds"] == 0.0
        assert m["last_message_at"] is None

    def test_repr_includes_name_and_state(self):
        ws = self._make_ws()
        r = repr(ws)
        assert "test-ws" in r
        assert "disconnected" in r
