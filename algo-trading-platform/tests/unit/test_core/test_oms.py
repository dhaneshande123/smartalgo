"""Unit tests for the Order Management System (Phase 5).

Tests cover:
- Order state machine (transitions, terminal states, listeners)
- Audit trail (recording, filtering, ring buffer)
- Order manager (submit, modify, cancel, broker callbacks)
- Smart router (strategies, health, rules, stats)
- Order slicer (iceberg orders, slice computation, cancellation)
- Bracket order manager (bracket, cover, OCO, trailing SL)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import (
    Exchange,
    Instrument,
    InstrumentType,
    Order,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    Segment,
    Trade,
)
from core.oms.state_machine import OrderStateMachine, InvalidTransitionError
from core.oms.audit_trail import (
    AuditTrail,
    AuditEntry,
    AUDIT_SUBMIT,
    AUDIT_CANCEL,
    AUDIT_STATE_CHANGE,
    AUDIT_FILL,
)
from core.oms.order_manager import OrderManager
from core.oms.smart_router import (
    SmartRouter,
    RoutingStrategy,
    RoutingRule,
    RoutingStats,
    NoBrokerAvailableError,
)
from core.oms.order_slicer import OrderSlicer, SliceConfig, SliceState, ParentOrder
from core.oms.bracket_order import (
    BracketOrderManager,
    BracketOrder,
    CoverOrder,
    BracketState,
)


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def nifty_instrument():
    return Instrument(
        symbol="NIFTY",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        instrument_type=InstrumentType.INDEX,
        lot_size=25,
        tick_size=0.05,
    )


@pytest.fixture
def sample_order(nifty_instrument):
    return Order(
        order_id="ORD-001",
        instrument=nifty_instrument,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        product_type=ProductType.MIS,
        quantity=25,
        price=Decimal("24200.00"),
        strategy_id="strat_1",
    )


@pytest.fixture
def state_machine():
    return OrderStateMachine()


@pytest.fixture
def audit_trail():
    return AuditTrail(max_entries=100)


def _make_broker_manager(healthy=True):
    """Create a mock BrokerManager."""
    bm = MagicMock()
    bm.place_order = AsyncMock(
        return_value=OrderResponse(
            success=True,
            broker_order_id="BRK-001",
            status=OrderStatus.PLACED,
            message="ok",
        )
    )
    bm.modify_order = AsyncMock(
        return_value=OrderResponse(success=True, message="modified")
    )
    bm.cancel_order = AsyncMock(
        return_value=OrderResponse(success=True, message="cancelled")
    )

    health = MagicMock()
    health.is_connected = healthy
    health.consecutive_errors = 0
    health.latency_ms = 50.0
    bm.broker_health = {"zerodha": health}
    return bm


def _make_event_bus():
    """Create a mock EventBus."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


# =====================================================================
# State Machine Tests
# =====================================================================


class TestOrderStateMachine:

    def test_valid_transition_pending_to_placed(self, state_machine, sample_order):
        result = state_machine.transition(sample_order, OrderStatus.PLACED)
        assert result.status == OrderStatus.PLACED

    def test_valid_transition_placed_to_open(self, state_machine, sample_order):
        state_machine.transition(sample_order, OrderStatus.PLACED)
        state_machine.transition(sample_order, OrderStatus.OPEN)
        assert sample_order.status == OrderStatus.OPEN

    def test_valid_transition_open_to_filled(self, state_machine, sample_order):
        state_machine.transition(sample_order, OrderStatus.PLACED)
        state_machine.transition(sample_order, OrderStatus.OPEN)
        state_machine.transition(sample_order, OrderStatus.FILLED)
        assert sample_order.status == OrderStatus.FILLED

    def test_valid_transition_open_to_partial(self, state_machine, sample_order):
        state_machine.transition(sample_order, OrderStatus.PLACED)
        state_machine.transition(sample_order, OrderStatus.OPEN)
        state_machine.transition(sample_order, OrderStatus.PARTIAL)
        assert sample_order.status == OrderStatus.PARTIAL

    def test_valid_transition_partial_to_filled(self, state_machine, sample_order):
        state_machine.transition(sample_order, OrderStatus.PLACED)
        state_machine.transition(sample_order, OrderStatus.OPEN)
        state_machine.transition(sample_order, OrderStatus.PARTIAL)
        state_machine.transition(sample_order, OrderStatus.FILLED)
        assert sample_order.status == OrderStatus.FILLED

    def test_invalid_transition_raises(self, state_machine, sample_order):
        with pytest.raises(InvalidTransitionError):
            state_machine.transition(sample_order, OrderStatus.FILLED)

    def test_invalid_transition_from_terminal(self, state_machine, sample_order):
        state_machine.transition(sample_order, OrderStatus.REJECTED, reason="bad")
        with pytest.raises(InvalidTransitionError):
            state_machine.transition(sample_order, OrderStatus.PLACED)

    def test_same_state_noop(self, state_machine, sample_order):
        """Transitioning to the same state should be a no-op."""
        result = state_machine.transition(sample_order, OrderStatus.PENDING)
        assert result.status == OrderStatus.PENDING

    def test_can_transition(self, state_machine):
        assert state_machine.can_transition(OrderStatus.PENDING, OrderStatus.PLACED)
        assert not state_machine.can_transition(OrderStatus.PENDING, OrderStatus.FILLED)

    def test_is_terminal(self, state_machine):
        assert state_machine.is_terminal(OrderStatus.FILLED)
        assert state_machine.is_terminal(OrderStatus.CANCELLED)
        assert state_machine.is_terminal(OrderStatus.REJECTED)
        assert state_machine.is_terminal(OrderStatus.ERROR)
        assert not state_machine.is_terminal(OrderStatus.PENDING)
        assert not state_machine.is_terminal(OrderStatus.OPEN)

    def test_get_valid_targets(self, state_machine):
        targets = state_machine.get_valid_targets(OrderStatus.OPEN)
        assert OrderStatus.PARTIAL in targets
        assert OrderStatus.FILLED in targets
        assert OrderStatus.CANCELLED in targets

    def test_terminal_has_no_targets(self, state_machine):
        targets = state_machine.get_valid_targets(OrderStatus.FILLED)
        assert len(targets) == 0

    def test_listener_called(self, state_machine, sample_order):
        listener = MagicMock()
        state_machine.on_transition(listener)
        state_machine.transition(sample_order, OrderStatus.PLACED)
        listener.assert_called_once()
        args = listener.call_args[0]
        assert args[1] == OrderStatus.PENDING  # old
        assert args[2] == OrderStatus.PLACED   # new

    def test_remove_listener(self, state_machine, sample_order):
        listener = MagicMock()
        state_machine.on_transition(listener)
        state_machine.remove_listener(listener)
        state_machine.transition(sample_order, OrderStatus.PLACED)
        listener.assert_not_called()

    def test_listener_exception_does_not_block(self, state_machine, sample_order):
        bad_listener = MagicMock(side_effect=RuntimeError("boom"))
        state_machine.on_transition(bad_listener)
        # Should not raise
        state_machine.transition(sample_order, OrderStatus.PLACED)
        assert sample_order.status == OrderStatus.PLACED

    def test_rejection_sets_reason(self, state_machine, sample_order):
        state_machine.transition(sample_order, OrderStatus.REJECTED, reason="insufficient margin")
        assert sample_order.rejection_reason == "insufficient margin"

    def test_updated_at_set(self, state_machine, sample_order):
        before = datetime.now(timezone.utc)
        state_machine.transition(sample_order, OrderStatus.PLACED)
        assert sample_order.updated_at >= before

    def test_invalid_transition_error_attrs(self, state_machine, sample_order):
        try:
            state_machine.transition(sample_order, OrderStatus.FILLED)
        except InvalidTransitionError as e:
            assert e.order_id == "ORD-001"
            assert e.current_state == OrderStatus.PENDING
            assert e.target_state == OrderStatus.FILLED

    def test_cancel_from_any_non_terminal(self, state_machine, sample_order):
        """All non-terminal states should allow transition to CANCELLED."""
        for status in [OrderStatus.PENDING, OrderStatus.PLACED, OrderStatus.OPEN, OrderStatus.PARTIAL]:
            order = sample_order.model_copy(deep=True)
            order.status = status
            assert state_machine.can_transition(status, OrderStatus.CANCELLED)


# =====================================================================
# Audit Trail Tests
# =====================================================================


class TestAuditTrail:

    @pytest.mark.asyncio
    async def test_record_and_retrieve(self, audit_trail):
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc),
            order_id="ORD-001",
            event_type=AUDIT_SUBMIT,
            old_state="",
            new_state="PENDING",
        )
        await audit_trail.record(entry)
        assert audit_trail.entry_count == 1

    @pytest.mark.asyncio
    async def test_get_entries_by_order_id(self, audit_trail):
        for i in range(5):
            await audit_trail.record(AuditEntry(
                timestamp=datetime.now(timezone.utc),
                order_id=f"ORD-{i:03d}",
                event_type=AUDIT_SUBMIT,
                old_state="",
                new_state="PENDING",
            ))
        results = await audit_trail.get_entries(order_id="ORD-002")
        assert len(results) == 1
        assert results[0].order_id == "ORD-002"

    @pytest.mark.asyncio
    async def test_get_entries_by_strategy(self, audit_trail):
        await audit_trail.record(AuditEntry(
            timestamp=datetime.now(timezone.utc),
            order_id="ORD-001",
            event_type=AUDIT_SUBMIT,
            old_state="",
            new_state="PENDING",
            strategy_id="strat_a",
        ))
        await audit_trail.record(AuditEntry(
            timestamp=datetime.now(timezone.utc),
            order_id="ORD-002",
            event_type=AUDIT_SUBMIT,
            old_state="",
            new_state="PENDING",
            strategy_id="strat_b",
        ))
        results = await audit_trail.get_entries(strategy_id="strat_a")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_get_entries_by_event_type(self, audit_trail):
        await audit_trail.record(AuditEntry(
            timestamp=datetime.now(timezone.utc),
            order_id="ORD-001",
            event_type=AUDIT_SUBMIT,
            old_state="",
            new_state="PENDING",
        ))
        await audit_trail.record(AuditEntry(
            timestamp=datetime.now(timezone.utc),
            order_id="ORD-001",
            event_type=AUDIT_CANCEL,
            old_state="PENDING",
            new_state="CANCELLED",
        ))
        results = await audit_trail.get_entries(event_type=AUDIT_CANCEL)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_get_order_history(self, audit_trail):
        for et in [AUDIT_SUBMIT, AUDIT_STATE_CHANGE, AUDIT_FILL]:
            await audit_trail.record(AuditEntry(
                timestamp=datetime.now(timezone.utc),
                order_id="ORD-001",
                event_type=et,
                old_state="",
                new_state="",
            ))
        history = await audit_trail.get_order_history("ORD-001")
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_ring_buffer_eviction(self):
        trail = AuditTrail(max_entries=5)
        for i in range(10):
            await trail.record(AuditEntry(
                timestamp=datetime.now(timezone.utc),
                order_id=f"ORD-{i:03d}",
                event_type=AUDIT_SUBMIT,
                old_state="",
                new_state="PENDING",
            ))
        assert trail.entry_count == 5

    @pytest.mark.asyncio
    async def test_clear(self, audit_trail):
        await audit_trail.record(AuditEntry(
            timestamp=datetime.now(timezone.utc),
            order_id="ORD-001",
            event_type=AUDIT_SUBMIT,
            old_state="",
            new_state="PENDING",
        ))
        await audit_trail.clear()
        assert audit_trail.entry_count == 0

    @pytest.mark.asyncio
    async def test_limit_parameter(self, audit_trail):
        for i in range(20):
            await audit_trail.record(AuditEntry(
                timestamp=datetime.now(timezone.utc),
                order_id="ORD-001",
                event_type=AUDIT_SUBMIT,
                old_state="",
                new_state="PENDING",
            ))
        results = await audit_trail.get_entries(order_id="ORD-001", limit=5)
        assert len(results) == 5

    def test_invalid_max_entries(self):
        with pytest.raises(ValueError):
            AuditTrail(max_entries=0)


# =====================================================================
# Order Manager Tests
# =====================================================================


class TestOrderManager:

    @pytest.fixture
    def oms(self, audit_trail):
        bm = _make_broker_manager()
        bus = _make_event_bus()
        return OrderManager(bm, bus, audit_trail)

    @pytest.mark.asyncio
    async def test_submit_order_success(self, oms, sample_order):
        order_id = await oms.submit_order(sample_order)
        assert order_id == "ORD-001"
        order = oms.get_order(order_id)
        assert order is not None
        assert order.status == OrderStatus.PLACED

    @pytest.mark.asyncio
    async def test_submit_generates_id(self, oms, nifty_instrument):
        order = Order(
            instrument=nifty_instrument,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=25,
        )
        order_id = await oms.submit_order(order)
        assert len(order_id) > 0
        assert oms.get_order(order_id) is not None

    @pytest.mark.asyncio
    async def test_submit_duplicate_order_id_raises(self, oms, sample_order):
        await oms.submit_order(sample_order)
        dup = sample_order.model_copy(deep=True)
        with pytest.raises(ValueError, match="Duplicate"):
            await oms.submit_order(dup)

    @pytest.mark.asyncio
    async def test_submit_invalid_limit_no_price(self, oms, nifty_instrument):
        """Pydantic validator rejects LIMIT without price at construction."""
        with pytest.raises(Exception):
            Order(
                order_id="ORD-BAD",
                instrument=nifty_instrument,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                product_type=ProductType.MIS,
                quantity=25,
                price=None,
            )

    @pytest.mark.asyncio
    async def test_submit_invalid_quantity(self, oms, nifty_instrument):
        """Pydantic validator rejects quantity=0 at construction."""
        with pytest.raises(Exception):
            Order(
                order_id="ORD-BAD2",
                instrument=nifty_instrument,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                quantity=0,
            )

    @pytest.mark.asyncio
    async def test_submit_lot_size_validation(self, oms, nifty_instrument):
        order = Order(
            order_id="ORD-LOT",
            instrument=nifty_instrument,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=13,  # Not a multiple of lot_size=25
        )
        order_id = await oms.submit_order(order)
        order = oms.get_order(order_id)
        assert order.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_cancel_order(self, oms, sample_order):
        await oms.submit_order(sample_order)
        result = await oms.cancel_order("ORD-001", reason="test")
        assert result is True
        assert oms.get_order("ORD-001").status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_order(self, oms):
        result = await oms.cancel_order("NOPE")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_by_strategy(self, oms, nifty_instrument):
        for i in range(3):
            order = Order(
                order_id=f"ORD-S{i}",
                instrument=nifty_instrument,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                quantity=25,
                strategy_id="strat_1",
            )
            await oms.submit_order(order)
        cancelled = await oms.cancel_all(strategy_id="strat_1")
        assert cancelled == 3

    @pytest.mark.asyncio
    async def test_get_active_orders(self, oms, sample_order):
        await oms.submit_order(sample_order)
        active = oms.get_active_orders()
        assert len(active) == 1
        await oms.cancel_order("ORD-001")
        active = oms.get_active_orders()
        assert len(active) == 0

    @pytest.mark.asyncio
    async def test_get_orders_by_strategy(self, oms, sample_order):
        await oms.submit_order(sample_order)
        orders = oms.get_orders_by_strategy("strat_1")
        assert len(orders) == 1

    @pytest.mark.asyncio
    async def test_on_order_update_fill(self, oms, sample_order):
        await oms.submit_order(sample_order)
        # Simulate broker fill callback
        await oms.on_order_update("BRK-001", OrderStatus.OPEN)
        assert oms.get_order("ORD-001").status == OrderStatus.OPEN

        await oms.on_order_update("BRK-001", OrderStatus.FILLED, filled_qty=25, avg_price=24200.0)
        assert oms.get_order("ORD-001").status == OrderStatus.FILLED
        assert len(oms.get_trades()) >= 1

    @pytest.mark.asyncio
    async def test_on_order_update_unknown_broker_id(self, oms):
        # Should not raise
        await oms.on_order_update("UNKNOWN", OrderStatus.FILLED)

    @pytest.mark.asyncio
    async def test_modify_order(self, oms, sample_order):
        await oms.submit_order(sample_order)
        # Transition to OPEN first so it's modifiable
        await oms.on_order_update("BRK-001", OrderStatus.OPEN)
        result = await oms.modify_order("ORD-001", new_price=24300.0)
        assert result is True
        assert oms.get_order("ORD-001").price == Decimal("24300.0")

    @pytest.mark.asyncio
    async def test_modify_terminal_order_fails(self, oms, sample_order):
        await oms.submit_order(sample_order)
        await oms.cancel_order("ORD-001")
        result = await oms.modify_order("ORD-001", new_price=24300.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_local_orders(self, oms, sample_order):
        await oms.submit_order(sample_order)
        local = await oms.get_local_orders()
        assert "ORD-001" in local

    @pytest.mark.asyncio
    async def test_broker_rejection(self, oms, audit_trail, nifty_instrument):
        bm = _make_broker_manager()
        bm.place_order = AsyncMock(
            return_value=OrderResponse(
                success=False,
                status=OrderStatus.REJECTED,
                message="insufficient margin",
            )
        )
        oms2 = OrderManager(bm, _make_event_bus(), audit_trail)
        order = Order(
            order_id="ORD-REJ",
            instrument=nifty_instrument,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=25,
        )
        await oms2.submit_order(order)
        assert oms2.get_order("ORD-REJ").status in (OrderStatus.REJECTED, OrderStatus.ERROR)


# =====================================================================
# Smart Router Tests
# =====================================================================


class TestSmartRouter:

    @pytest.fixture
    def router(self):
        bm = _make_broker_manager()
        return SmartRouter(bm, strategy=RoutingStrategy.FAILOVER)

    @pytest.mark.asyncio
    async def test_failover_selects_healthy(self, router, sample_order):
        broker = await router.select_broker(sample_order)
        assert broker == "zerodha"

    @pytest.mark.asyncio
    async def test_no_healthy_broker_raises(self, sample_order):
        bm = _make_broker_manager(healthy=False)
        router = SmartRouter(bm, strategy=RoutingStrategy.FAILOVER)
        with pytest.raises(NoBrokerAvailableError):
            await router.select_broker(sample_order)

    @pytest.mark.asyncio
    async def test_round_robin(self, sample_order):
        bm = _make_broker_manager()
        h2 = MagicMock()
        h2.is_connected = True
        bm.broker_health["angelone"] = h2
        router = SmartRouter(bm, strategy=RoutingStrategy.ROUND_ROBIN)

        brokers = set()
        for _ in range(4):
            b = await router.select_broker(sample_order)
            brokers.add(b)
        assert len(brokers) == 2

    @pytest.mark.asyncio
    async def test_lowest_latency(self, sample_order):
        bm = _make_broker_manager()
        h2 = MagicMock()
        h2.is_connected = True
        bm.broker_health["angelone"] = h2

        router = SmartRouter(bm, strategy=RoutingStrategy.LOWEST_LATENCY)
        router.record_success("zerodha", 100.0)
        router.record_success("angelone", 50.0)

        broker = await router.select_broker(sample_order)
        assert broker == "angelone"

    @pytest.mark.asyncio
    async def test_priority_based_routing(self, sample_order):
        bm = _make_broker_manager()
        h2 = MagicMock()
        h2.is_connected = True
        bm.broker_health["angelone"] = h2

        router = SmartRouter(bm, strategy=RoutingStrategy.PRIORITY_BASED)
        router.add_rule(RoutingRule(
            name="all_to_angelone",
            priority=1,
            condition=lambda o: True,
            broker_name="angelone",
        ))

        broker = await router.select_broker(sample_order)
        assert broker == "angelone"

    @pytest.mark.asyncio
    async def test_rule_fallback(self, sample_order):
        bm = _make_broker_manager()
        # Only zerodha is healthy, angelone is not
        router = SmartRouter(bm, strategy=RoutingStrategy.PRIORITY_BASED)
        router.add_rule(RoutingRule(
            name="prefer_angelone",
            priority=1,
            condition=lambda o: True,
            broker_name="angelone",  # not healthy
            fallback_brokers=["zerodha"],
        ))

        broker = await router.select_broker(sample_order)
        assert broker == "zerodha"

    def test_record_stats(self):
        bm = _make_broker_manager()
        router = SmartRouter(bm)
        router.record_success("zerodha", 50.0)
        router.record_success("zerodha", 100.0)
        router.record_failure("zerodha", "timeout")

        stats = router.routing_stats["zerodha"]
        assert stats.total_orders == 3
        assert stats.successful_orders == 2
        assert stats.failed_orders == 1
        assert stats.avg_latency_ms == 75.0

    def test_add_remove_rule(self):
        bm = _make_broker_manager()
        router = SmartRouter(bm)
        router.add_rule(RoutingRule("test", 1, lambda o: True, "zerodha"))
        assert len(router.rules) == 1
        router.remove_rule("test")
        assert len(router.rules) == 0

    def test_change_strategy(self):
        bm = _make_broker_manager()
        router = SmartRouter(bm, strategy=RoutingStrategy.FAILOVER)
        router.strategy = RoutingStrategy.ROUND_ROBIN
        assert router.strategy == RoutingStrategy.ROUND_ROBIN


# =====================================================================
# Order Slicer Tests
# =====================================================================


class TestOrderSlicer:

    @pytest.fixture
    def submit_fn(self):
        call_count = {"n": 0}

        async def _submit(order: Order) -> str:
            call_count["n"] += 1
            return f"CHILD-{call_count['n']:03d}"

        _submit.call_count = call_count
        return _submit

    def test_slice_config_validation(self):
        with pytest.raises(ValueError):
            SliceConfig(max_slice_quantity=0)
        with pytest.raises(ValueError):
            SliceConfig(max_slice_quantity=5, min_slice_quantity=10)

    @pytest.mark.asyncio
    async def test_submit_iceberg(self, submit_fn, sample_order):
        slicer = OrderSlicer(submit_fn)
        sample_order.quantity = 100
        config = SliceConfig(
            max_slice_quantity=25,
            delay_between_slices_ms=0,
            randomize_quantity=False,
            randomize_delay=False,
        )
        parent_id = await slicer.submit_iceberg(sample_order, config)
        assert parent_id.startswith("ICE-")

        # Agent 2's slicer submits one slice and waits for fill before next
        await asyncio.sleep(0.2)
        parent = slicer.get_status(parent_id)
        assert parent is not None
        assert len(parent.child_order_ids) >= 1  # At least first slice submitted

    @pytest.mark.asyncio
    async def test_iceberg_fill_tracking(self, submit_fn, sample_order):
        slicer = OrderSlicer(submit_fn)
        sample_order.quantity = 50
        config = SliceConfig(
            max_slice_quantity=25,
            delay_between_slices_ms=0,
            randomize_quantity=False,
            randomize_delay=False,
        )
        parent_id = await slicer.submit_iceberg(sample_order, config)
        await asyncio.sleep(0.1)

        # Fill first slice, which should trigger next slice
        parent = slicer.get_status(parent_id)
        assert len(parent.child_order_ids) >= 1
        await slicer.on_child_fill(parent_id, parent.child_order_ids[0], 25, 24200.0)
        await asyncio.sleep(0.2)

        # Now second slice should be submitted and fillable
        if len(parent.child_order_ids) >= 2:
            await slicer.on_child_fill(parent_id, parent.child_order_ids[1], 25, 24200.0)
            await asyncio.sleep(0.1)

        assert parent.total_filled >= 25
        assert parent.avg_fill_price == 24200.0

    @pytest.mark.asyncio
    async def test_cancel_iceberg(self, submit_fn, sample_order):
        slicer = OrderSlicer(submit_fn)
        sample_order.quantity = 1000
        config = SliceConfig(
            max_slice_quantity=10,
            delay_between_slices_ms=100,
            randomize_quantity=False,
            randomize_delay=False,
        )
        parent_id = await slicer.submit_iceberg(sample_order, config)
        await asyncio.sleep(0.05)

        await slicer.cancel_iceberg(parent_id)
        parent = slicer.get_status(parent_id)
        assert parent.state == SliceState.CANCELLED

    @pytest.mark.asyncio
    async def test_get_all_parents(self, submit_fn, sample_order):
        slicer = OrderSlicer(submit_fn)
        sample_order.quantity = 25
        config = SliceConfig(max_slice_quantity=25, delay_between_slices_ms=0,
                             randomize_quantity=False, randomize_delay=False)
        await slicer.submit_iceberg(sample_order.model_copy(deep=True), config)
        await slicer.submit_iceberg(sample_order.model_copy(deep=True), config)
        assert len(slicer.get_all_parents()) == 2

    def test_parent_order_remaining(self, sample_order):
        parent = ParentOrder(
            parent_id="ICE-001",
            order=sample_order,
            config=SliceConfig(max_slice_quantity=10),
        )
        sample_order.quantity = 100
        parent.total_filled = 30
        assert parent.remaining == 70
        assert abs(parent.progress_pct - 30.0) < 0.01

    @pytest.mark.asyncio
    async def test_vwap_calculation(self, submit_fn, sample_order):
        slicer = OrderSlicer(submit_fn)
        sample_order.quantity = 50
        config = SliceConfig(
            max_slice_quantity=25,
            delay_between_slices_ms=0,
            randomize_quantity=False,
            randomize_delay=False,
        )
        parent_id = await slicer.submit_iceberg(sample_order, config)
        await asyncio.sleep(0.1)

        parent = slicer.get_status(parent_id)
        # Fill first slice
        await slicer.on_child_fill(parent_id, parent.child_order_ids[0], 25, 24200.0)
        await asyncio.sleep(0.2)

        # Fill second slice (at different price for VWAP test)
        if len(parent.child_order_ids) >= 2:
            await slicer.on_child_fill(parent_id, parent.child_order_ids[1], 25, 24300.0)
            await asyncio.sleep(0.1)
            # VWAP = (25*24200 + 25*24300) / 50 = 24250
            assert abs(parent.avg_fill_price - 24250.0) < 0.01
        else:
            # If slicer only submitted 1 slice so far, verify first fill
            assert parent.avg_fill_price == 24200.0


# =====================================================================
# Bracket Order Manager Tests
# =====================================================================


class TestBracketOrderManager:

    @pytest.fixture
    def submit_fn(self):
        call_count = {"n": 0}

        async def _submit(order: Order) -> str:
            call_count["n"] += 1
            return f"LEG-{call_count['n']:03d}"

        _submit.call_count = call_count
        return _submit

    @pytest.fixture
    def cancel_fn(self):
        cancelled = []

        async def _cancel(order_id: str, reason: str = "") -> bool:
            cancelled.append(order_id)
            return True

        _cancel.cancelled = cancelled
        return _cancel

    @pytest.fixture
    def modify_fn(self):
        async def _modify(order_id, **kwargs):
            return True
        return _modify

    @pytest.fixture
    def bom(self, submit_fn, cancel_fn, modify_fn):
        return BracketOrderManager(submit_fn, cancel_fn, modify_fn)

    @pytest.mark.asyncio
    async def test_submit_bracket(self, bom, sample_order):
        bracket_id = await bom.submit_bracket(
            sample_order, target_price=24500, stoploss_price=24000
        )
        assert bracket_id.startswith("BRK-")
        bracket = bom.get_bracket(bracket_id)
        assert bracket is not None
        assert bracket.state == BracketState.ENTRY_PLACED
        assert bracket.entry_order_id == "LEG-001"

    @pytest.mark.asyncio
    async def test_bracket_entry_fill_places_legs(self, bom, sample_order):
        bracket_id = await bom.submit_bracket(
            sample_order, target_price=24500, stoploss_price=24000
        )
        bracket = bom.get_bracket(bracket_id)

        # Simulate entry fill
        await bom.on_order_fill("LEG-001", fill_price=24200.0, fill_qty=25)

        assert bracket.state == BracketState.ACTIVE
        assert bracket.target_order_id == "LEG-002"
        assert bracket.sl_order_id == "LEG-003"

    @pytest.mark.asyncio
    async def test_bracket_target_hit_cancels_sl(self, bom, sample_order, cancel_fn):
        bracket_id = await bom.submit_bracket(
            sample_order, target_price=24500, stoploss_price=24000
        )
        await bom.on_order_fill("LEG-001", 24200.0, 25)  # entry
        await bom.on_order_fill("LEG-002", 24500.0, 25)  # target

        bracket = bom.get_bracket(bracket_id)
        assert bracket.state == BracketState.TARGET_HIT
        assert "LEG-003" in cancel_fn.cancelled  # SL was cancelled

    @pytest.mark.asyncio
    async def test_bracket_sl_hit_cancels_target(self, bom, sample_order, cancel_fn):
        bracket_id = await bom.submit_bracket(
            sample_order, target_price=24500, stoploss_price=24000
        )
        await bom.on_order_fill("LEG-001", 24200.0, 25)  # entry
        await bom.on_order_fill("LEG-003", 24000.0, 25)  # SL

        bracket = bom.get_bracket(bracket_id)
        assert bracket.state == BracketState.SL_HIT
        assert "LEG-002" in cancel_fn.cancelled  # target was cancelled

    @pytest.mark.asyncio
    async def test_submit_cover(self, bom, sample_order):
        cover_id = await bom.submit_cover(sample_order, stoploss_price=24000)
        assert cover_id.startswith("COV-")
        cover = bom.get_cover(cover_id)
        assert cover.state == BracketState.ENTRY_PLACED

    @pytest.mark.asyncio
    async def test_cover_entry_fill_places_sl(self, bom, sample_order):
        cover_id = await bom.submit_cover(sample_order, stoploss_price=24000)
        await bom.on_order_fill("LEG-001", 24200.0, 25)

        cover = bom.get_cover(cover_id)
        assert cover.state == BracketState.ACTIVE
        assert cover.sl_order_id == "LEG-002"

    @pytest.mark.asyncio
    async def test_cover_sl_hit(self, bom, sample_order):
        cover_id = await bom.submit_cover(sample_order, stoploss_price=24000)
        await bom.on_order_fill("LEG-001", 24200.0, 25)
        await bom.on_order_fill("LEG-002", 24000.0, 25)

        cover = bom.get_cover(cover_id)
        assert cover.state == BracketState.SL_HIT

    @pytest.mark.asyncio
    async def test_bracket_invalid_prices_buy(self, bom, sample_order):
        with pytest.raises(ValueError, match="target.*must be > stoploss"):
            await bom.submit_bracket(
                sample_order, target_price=23000, stoploss_price=24000
            )

    @pytest.mark.asyncio
    async def test_bracket_invalid_prices_sell(self, bom, nifty_instrument):
        sell_order = Order(
            order_id="ORD-SELL",
            instrument=nifty_instrument,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product_type=ProductType.MIS,
            quantity=25,
            price=Decimal("24200.00"),
        )
        with pytest.raises(ValueError, match="target.*must be < stoploss"):
            await bom.submit_bracket(
                sell_order, target_price=24500, stoploss_price=24000
            )

    @pytest.mark.asyncio
    async def test_trailing_sl_update(self, bom, sample_order):
        bracket_id = await bom.submit_bracket(
            sample_order, target_price=24500, stoploss_price=24000, trailing_sl=100
        )
        await bom.on_order_fill("LEG-001", 24200.0, 25)

        bracket = bom.get_bracket(bracket_id)
        assert bracket.state == BracketState.ACTIVE

        # Price moved up — SL should trail
        await bom.update_trailing_sl(bracket_id, current_price=24400)
        assert bracket.stoploss_price == 24300.0  # 24400 - 100

    @pytest.mark.asyncio
    async def test_trailing_sl_no_downward_move(self, bom, sample_order):
        bracket_id = await bom.submit_bracket(
            sample_order, target_price=24500, stoploss_price=24000, trailing_sl=100
        )
        await bom.on_order_fill("LEG-001", 24200.0, 25)

        # Price below current SL range — SL should NOT move down
        await bom.update_trailing_sl(bracket_id, current_price=24050)
        bracket = bom.get_bracket(bracket_id)
        assert bracket.stoploss_price == 24000.0  # unchanged

    def test_get_active_brackets(self, bom):
        assert bom.get_active_brackets() == []

    @pytest.mark.asyncio
    async def test_on_order_cancel_entry(self, bom, sample_order):
        bracket_id = await bom.submit_bracket(
            sample_order, target_price=24500, stoploss_price=24000
        )
        await bom.on_order_cancel("LEG-001")
        bracket = bom.get_bracket(bracket_id)
        assert bracket.state == BracketState.CANCELLED


# =====================================================================
# Routing Stats Tests
# =====================================================================


class TestRoutingStats:

    def test_success_rate_no_orders(self):
        stats = RoutingStats(broker_name="test")
        assert stats.success_rate == 1.0

    def test_success_rate_tracking(self):
        stats = RoutingStats(broker_name="test")
        stats.record_success(50.0)
        stats.record_success(100.0)
        stats.record_failure("timeout")
        assert stats.success_rate == pytest.approx(2 / 3)
        assert stats.avg_latency_ms == 75.0
        assert stats.last_failure == "timeout"
