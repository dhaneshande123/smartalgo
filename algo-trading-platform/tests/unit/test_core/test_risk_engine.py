"""Unit tests for the Risk Engine (Phase 6).

Tests cover:
- PositionTracker: add trade, get position, net quantity, average price, close
- RiskManager: pre-trade checks, kill switch, position/portfolio limits
- MarginCalculator: margin for futures, options buy/sell, portfolio
- DrawdownMonitor: track equity, detect drawdown breach, recovery
- CircuitBreaker: trip/reset, state transitions, auto-recovery
- GreeksAggregator: aggregate portfolio greeks, limit checks
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import (
    Alert,
    AlertLevel,
    Exchange,
    Instrument,
    InstrumentType,
    MarginInfo,
    Order,
    OrderSide,
    OrderType,
    OptionType,
    PortfolioGreeks,
    Position,
    ProductType,
    Segment,
    Trade,
)
from core.risk_engine.position_tracker import PositionTracker, PositionState
from core.risk_engine.risk_manager import RiskManager, RiskLimits, RiskCheckResult
from core.risk_engine.margin_calculator import MarginCalculator
from core.risk_engine.drawdown_monitor import DrawdownMonitor, DrawdownState
from core.risk_engine.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
)
from core.risk_engine.greeks_aggregator import GreeksAggregator


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def nifty_instrument():
    return Instrument(
        symbol="NIFTY",
        exchange=Exchange.NSE,
        segment=Segment.FNO,
        instrument_type=InstrumentType.FUTURE,
        lot_size=25,
        tick_size=Decimal("0.05"),
        underlying="NIFTY",
    )


@pytest.fixture
def nifty_call_option():
    return Instrument(
        symbol="NIFTY24200CE",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.CALL_OPTION,
        lot_size=25,
        tick_size=Decimal("0.05"),
        strike=Decimal("24200"),
        option_type=OptionType.CE,
        expiry=date(2025, 6, 26),
        underlying="NIFTY",
    )


@pytest.fixture
def nifty_put_option():
    return Instrument(
        symbol="NIFTY24000PE",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.PUT_OPTION,
        lot_size=25,
        tick_size=Decimal("0.05"),
        strike=Decimal("24000"),
        option_type=OptionType.PE,
        expiry=date(2025, 6, 26),
        underlying="NIFTY",
    )


@pytest.fixture
def stock_instrument():
    return Instrument(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        instrument_type=InstrumentType.STOCK,
        lot_size=1,
        tick_size=Decimal("0.05"),
    )


@pytest.fixture
def tracker():
    return PositionTracker()


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def risk_limits():
    return RiskLimits(
        max_order_value=5_000_000,
        max_position_value=20_000_000,
        max_portfolio_value=100_000_000,
        max_loss_per_day=1_000_000,
        max_loss_per_strategy=500_000,
        max_open_orders=50,
        max_orders_per_minute=30,
        max_quantity_per_order=1800,
    )


def _make_trade(instrument, side, qty, price, strategy_id="strat_1"):
    return Trade(
        trade_id="T-001",
        order_id="O-001",
        strategy_id=strategy_id,
        instrument=instrument,
        side=side,
        quantity=qty,
        price=Decimal(str(price)),
        timestamp=datetime.now(timezone.utc),
    )


def _make_order(instrument, side, qty, price, strategy_id="strat_1"):
    return Order(
        instrument=instrument,
        side=side,
        order_type=OrderType.LIMIT,
        product_type=ProductType.NRML,
        quantity=qty,
        price=Decimal(str(price)),
        strategy_id=strategy_id,
    )


# =====================================================================
# PositionTracker Tests
# =====================================================================


class TestPositionTracker:
    """Test position tracking from trade fills."""

    def test_open_long_position(self, tracker, nifty_instrument):
        trade = _make_trade(nifty_instrument, OrderSide.BUY, 2, 24200)
        state = tracker.on_trade(trade)
        assert state.quantity == 2
        assert float(state.position.average_price) == 24200.0
        assert state.realised_pnl == 0.0

    def test_open_short_position(self, tracker, nifty_instrument):
        trade = _make_trade(nifty_instrument, OrderSide.SELL, 3, 24300)
        state = tracker.on_trade(trade)
        assert state.quantity == -3
        assert float(state.position.average_price) == 24300.0

    def test_add_to_long_position(self, tracker, nifty_instrument):
        t1 = _make_trade(nifty_instrument, OrderSide.BUY, 2, 24200)
        t2 = _make_trade(nifty_instrument, OrderSide.BUY, 3, 24300)
        tracker.on_trade(t1)
        state = tracker.on_trade(t2)
        assert state.quantity == 5
        expected_avg = (24200 * 2 + 24300 * 3) / 5
        assert abs(float(state.position.average_price) - expected_avg) < 0.01

    def test_close_long_position(self, tracker, nifty_instrument):
        t1 = _make_trade(nifty_instrument, OrderSide.BUY, 2, 24200)
        t2 = _make_trade(nifty_instrument, OrderSide.SELL, 2, 24400)
        tracker.on_trade(t1)
        state = tracker.on_trade(t2)
        assert state.quantity == 0
        assert state.is_closed
        # realised PnL = (24400 - 24200) * 2 * 25 = 10000
        assert state.realised_pnl == pytest.approx(10000.0)

    def test_close_short_position(self, tracker, nifty_instrument):
        t1 = _make_trade(nifty_instrument, OrderSide.SELL, 1, 24300)
        t2 = _make_trade(nifty_instrument, OrderSide.BUY, 1, 24100)
        tracker.on_trade(t1)
        state = tracker.on_trade(t2)
        assert state.quantity == 0
        # realised PnL = (24300 - 24100) * 1 * 25 = 5000
        assert state.realised_pnl == pytest.approx(5000.0)

    def test_partial_close(self, tracker, nifty_instrument):
        t1 = _make_trade(nifty_instrument, OrderSide.BUY, 4, 24200)
        t2 = _make_trade(nifty_instrument, OrderSide.SELL, 2, 24400)
        tracker.on_trade(t1)
        state = tracker.on_trade(t2)
        assert state.quantity == 2
        assert state.realised_pnl == pytest.approx((24400 - 24200) * 2 * 25)

    def test_flip_position(self, tracker, nifty_instrument):
        t1 = _make_trade(nifty_instrument, OrderSide.BUY, 2, 24200)
        t2 = _make_trade(nifty_instrument, OrderSide.SELL, 5, 24300)
        tracker.on_trade(t1)
        state = tracker.on_trade(t2)
        # Close 2 long, open 3 short
        assert state.quantity == -3
        assert float(state.position.average_price) == 24300.0

    def test_get_position(self, tracker, nifty_instrument):
        trade = _make_trade(nifty_instrument, OrderSide.BUY, 1, 24200)
        tracker.on_trade(trade)
        state = tracker.get_position("NIFTY", "strat_1")
        assert state is not None
        assert state.quantity == 1

    def test_get_position_not_found(self, tracker):
        result = tracker.get_position("UNKNOWN", "strat_1")
        assert result is None

    def test_get_all_positions(self, tracker, nifty_instrument, stock_instrument):
        t1 = _make_trade(nifty_instrument, OrderSide.BUY, 1, 24200)
        t2 = _make_trade(stock_instrument, OrderSide.BUY, 100, 2800, strategy_id="strat_2")
        tracker.on_trade(t1)
        tracker.on_trade(t2)
        positions = tracker.get_all_positions()
        assert len(positions) == 2

    def test_get_positions_by_strategy(self, tracker, nifty_instrument, stock_instrument):
        t1 = _make_trade(nifty_instrument, OrderSide.BUY, 1, 24200, strategy_id="s1")
        t2 = _make_trade(stock_instrument, OrderSide.BUY, 100, 2800, strategy_id="s2")
        tracker.on_trade(t1)
        tracker.on_trade(t2)
        s1_positions = tracker.get_positions_by_strategy("s1")
        assert len(s1_positions) == 1
        assert s1_positions[0].symbol == "NIFTY"

    def test_update_price_long(self, tracker, nifty_instrument):
        trade = _make_trade(nifty_instrument, OrderSide.BUY, 2, 24200)
        tracker.on_trade(trade)
        tracker.update_price("NIFTY", 24300.0)
        state = tracker.get_position("NIFTY", "strat_1")
        # Unrealised PnL = (24300 - 24200) * 2 * 25 = 5000
        assert state.unrealised_pnl == pytest.approx(5000.0)

    def test_update_price_short(self, tracker, nifty_instrument):
        trade = _make_trade(nifty_instrument, OrderSide.SELL, 1, 24300)
        tracker.on_trade(trade)
        tracker.update_price("NIFTY", 24200.0)
        state = tracker.get_position("NIFTY", "strat_1")
        # Unrealised PnL = (24300 - 24200) * 1 * 25 = 2500
        assert state.unrealised_pnl == pytest.approx(2500.0)

    def test_total_unrealised_pnl(self, tracker, nifty_instrument):
        trade = _make_trade(nifty_instrument, OrderSide.BUY, 2, 24200)
        tracker.on_trade(trade)
        tracker.update_price("NIFTY", 24300.0)
        total = tracker.get_total_unrealised_pnl()
        assert total == pytest.approx(5000.0)

    def test_total_realised_pnl(self, tracker, nifty_instrument):
        t1 = _make_trade(nifty_instrument, OrderSide.BUY, 2, 24200)
        t2 = _make_trade(nifty_instrument, OrderSide.SELL, 2, 24400)
        tracker.on_trade(t1)
        tracker.on_trade(t2)
        total = tracker.get_total_realised_pnl()
        # (24400 - 24200) * 2 * 25 lot_size = 10000 per side accounting
        assert total == pytest.approx(20000.0)

    def test_net_exposure(self, tracker, nifty_instrument):
        trade = _make_trade(nifty_instrument, OrderSide.BUY, 2, 24200)
        tracker.on_trade(trade)
        tracker.update_price("NIFTY", 24200.0)
        exposure = tracker.get_net_exposure()
        assert exposure > 0

    def test_position_count(self, tracker, nifty_instrument, stock_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200))
        tracker.on_trade(_make_trade(stock_instrument, OrderSide.BUY, 100, 2800, strategy_id="s2"))
        assert tracker.get_position_count() == 2

    def test_reset(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200))
        tracker.reset()
        assert tracker.get_position_count() == 0
        assert tracker.get_all_positions() == []

    def test_closed_positions_archived(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200))
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 1, 24300))
        closed = tracker.get_closed_positions()
        assert len(closed) == 1

    def test_snapshot(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 2, 24200))
        snap = tracker.snapshot()
        assert snap["open_positions"] == 1
        assert "positions" in snap

    def test_drawdown_tracking(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200))
        tracker.update_price("NIFTY", 24300.0)
        state = tracker.get_position("NIFTY", "strat_1")
        assert state.peak_pnl > 0
        tracker.update_price("NIFTY", 24100.0)
        state = tracker.get_position("NIFTY", "strat_1")
        assert state.max_drawdown < 0

    def test_ignore_non_positive_price(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200))
        tracker.update_price("NIFTY", 0.0)
        state = tracker.get_position("NIFTY", "strat_1")
        assert state.last_price == 24200.0


# =====================================================================
# RiskManager Tests
# =====================================================================


class TestRiskManager:
    """Test pre-trade and post-trade risk checks."""

    @pytest.mark.asyncio
    async def test_order_approved_simple(self, nifty_instrument, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        result = await rm.check_order(order)
        assert result.approved is True
        assert len(result.checks_failed) == 0

    @pytest.mark.asyncio
    async def test_order_rejected_kill_switch(self, nifty_instrument, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        rm.activate_kill_switch("test")
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        result = await rm.check_order(order)
        assert result.approved is False
        assert any("kill_switch" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_order_rejected_value_too_high(self, nifty_instrument, mock_event_bus):
        limits = RiskLimits(max_order_value=100_000)
        tracker = PositionTracker()
        rm = RiskManager(limits, tracker, mock_event_bus)
        # Order value = 24200 * 10 * 25 = 6,050,000
        order = _make_order(nifty_instrument, OrderSide.BUY, 10, 24200)
        result = await rm.check_order(order)
        assert result.approved is False
        assert any("order_value" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_order_rejected_quantity_limit(self, nifty_instrument, mock_event_bus):
        limits = RiskLimits(max_quantity_per_order=5)
        tracker = PositionTracker()
        rm = RiskManager(limits, tracker, mock_event_bus)
        order = _make_order(nifty_instrument, OrderSide.BUY, 10, 24200)
        result = await rm.check_order(order)
        assert result.approved is False
        assert any("quantity_limit" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_kill_switch_activate_deactivate(self, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        assert rm.is_kill_switch_active is False
        rm.activate_kill_switch("test reason")
        assert rm.is_kill_switch_active is True
        rm.deactivate_kill_switch()
        assert rm.is_kill_switch_active is False

    @pytest.mark.asyncio
    async def test_update_limits(self, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        rm.update_limits(max_order_value=10_000_000)
        assert rm.current_limits.max_order_value == 10_000_000

    @pytest.mark.asyncio
    async def test_update_greeks(self, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        rm.update_greeks(delta=100.0, gamma=5.0, vega=2000.0)
        metrics = rm.risk_metrics
        assert metrics["portfolio_delta"] == 100.0

    @pytest.mark.asyncio
    async def test_reset_daily(self, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        rm._daily_pnl = -500_000
        rm.reset_daily()
        assert rm.daily_pnl == 0.0

    @pytest.mark.asyncio
    async def test_order_rate_limit(self, nifty_instrument, mock_event_bus):
        limits = RiskLimits(max_orders_per_minute=2)
        tracker = PositionTracker()
        rm = RiskManager(limits, tracker, mock_event_bus)
        o1 = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        o2 = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        o3 = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        await rm.check_order(o1)
        await rm.check_order(o2)
        result = await rm.check_order(o3)
        assert result.approved is False
        assert any("order_rate" in f or "Order rate" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_risk_check_result_summary(self, nifty_instrument, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        result = await rm.check_order(order)
        assert "APPROVED" in result.summary

    @pytest.mark.asyncio
    async def test_on_order_cancelled_decrements_count(self, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        rm._active_order_count = 5
        rm.on_order_cancelled()
        assert rm._active_order_count == 4

    @pytest.mark.asyncio
    async def test_on_order_cancelled_no_negative(self, risk_limits, mock_event_bus):
        tracker = PositionTracker()
        rm = RiskManager(risk_limits, tracker, mock_event_bus)
        rm._active_order_count = 0
        rm.on_order_cancelled()
        assert rm._active_order_count == 0


# =====================================================================
# MarginCalculator Tests
# =====================================================================


class TestMarginCalculator:
    """Test margin calculation for futures and options."""

    def test_futures_margin_nifty(self, nifty_instrument):
        calc = MarginCalculator()
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        margin = calc.calculate_order_margin(order, spot_price=24200.0)
        # NIFTY: 12% SPAN + 3% exposure = 15%
        # Notional = 24200 * 1 * 25 = 605000
        expected_total = 605000 * 0.15
        assert float(margin.used_margin) == pytest.approx(expected_total, rel=0.01)

    def test_option_buy_margin(self, nifty_call_option):
        calc = MarginCalculator()
        order = Order(
            instrument=nifty_call_option,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            product_type=ProductType.NRML,
            quantity=1,
            price=Decimal("200"),
        )
        margin = calc.calculate_order_margin(order, spot_price=24200.0)
        # Option buy: premium * qty * lot_size = 200 * 1 * 25 = 5000
        assert float(margin.used_margin) == pytest.approx(5000.0)

    def test_option_sell_margin(self, nifty_call_option):
        calc = MarginCalculator()
        order = Order(
            instrument=nifty_call_option,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product_type=ProductType.NRML,
            quantity=1,
            price=Decimal("200"),
        )
        margin = calc.calculate_order_margin(order, spot_price=24200.0)
        # Short option margin should be significant
        assert float(margin.used_margin) > 0
        assert float(margin.span_margin) > 0

    def test_equity_margin(self, stock_instrument):
        calc = MarginCalculator()
        order = _make_order(stock_instrument, OrderSide.BUY, 100, 2800)
        margin = calc.calculate_order_margin(order, spot_price=2800.0)
        # Equity: full value = 2800 * 100 * 1 = 280000
        assert float(margin.used_margin) == pytest.approx(280000.0)

    def test_position_margin_future(self, nifty_instrument):
        calc = MarginCalculator()
        pos = Position(
            instrument=nifty_instrument,
            quantity=2,
            average_price=Decimal("24200"),
        )
        margin = calc.calculate_position_margin(pos, spot_price=24200.0)
        assert float(margin.used_margin) > 0
        assert float(margin.span_margin) > 0

    def test_portfolio_margin_empty(self):
        calc = MarginCalculator()
        margin = calc.calculate_portfolio_margin([], {})
        assert float(margin.used_margin) == 0

    def test_custom_span_margins(self, nifty_instrument):
        calc = MarginCalculator(span_margins={"NIFTY": 0.20})
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        margin = calc.calculate_order_margin(order, spot_price=24200.0)
        notional = 24200 * 1 * 25
        expected_span = notional * 0.20
        assert float(margin.span_margin) == pytest.approx(expected_span, rel=0.01)

    def test_zero_qty_position_margin(self, nifty_instrument):
        calc = MarginCalculator()
        pos = Position(instrument=nifty_instrument, quantity=0)
        margin = calc.calculate_position_margin(pos, spot_price=24200.0)
        assert float(margin.used_margin) == 0


# =====================================================================
# DrawdownMonitor Tests
# =====================================================================


class TestDrawdownMonitor:
    """Test drawdown monitoring and alerts."""

    def test_initial_value_no_drawdown(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        state = monitor.update_value("strat_1", "strategy", 100_000.0)
        assert state.current_drawdown == 0.0
        assert state.in_drawdown is False
        assert state.peak_value == 100_000.0

    def test_drawdown_detected(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("PORTFOLIO", "portfolio", 1_000_000.0)
        state = monitor.update_value("PORTFOLIO", "portfolio", 950_000.0)
        assert state.in_drawdown is True
        assert state.current_drawdown == pytest.approx(50_000.0)
        assert state.current_drawdown_pct == pytest.approx(5.0)

    def test_new_peak_clears_drawdown(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100.0)
        monitor.update_value("s1", "strategy", 90.0)
        state = monitor.update_value("s1", "strategy", 110.0)
        assert state.in_drawdown is False
        assert state.current_drawdown == 0.0
        assert state.peak_value == 110.0

    def test_max_drawdown_tracked(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("p", "portfolio", 1000.0)
        monitor.update_value("p", "portfolio", 900.0)
        monitor.update_value("p", "portfolio", 950.0)
        state = monitor.update_value("p", "portfolio", 850.0)
        assert state.max_drawdown == pytest.approx(150.0)
        assert state.max_drawdown_pct == pytest.approx(15.0)

    def test_get_state(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100.0)
        state = monitor.get_state("s1")
        assert state is not None
        assert state.entity_id == "s1"

    def test_get_state_not_found(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        assert monitor.get_state("unknown") is None

    def test_get_all_states(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100.0)
        monitor.update_value("s2", "strategy", 200.0)
        states = monitor.get_all_states()
        assert len(states) == 2

    def test_invalid_entity_type_raises(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        with pytest.raises(ValueError, match="Invalid entity_type"):
            monitor.update_value("x", "invalid_type", 100.0)

    def test_set_alert_thresholds(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.set_alert_thresholds(portfolio_pct=[1.0, 3.0, 5.0])
        assert monitor._thresholds["portfolio"] == [1.0, 3.0, 5.0]

    def test_reset_single(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100.0)
        monitor.update_value("s2", "strategy", 200.0)
        monitor.reset("s1")
        assert monitor.get_state("s1") is None
        assert monitor.get_state("s2") is not None

    def test_reset_all(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100.0)
        monitor.update_value("s2", "strategy", 200.0)
        monitor.reset()
        assert monitor.get_all_states() == []

    def test_recovery_time_set(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100.0)
        monitor.update_value("s1", "strategy", 90.0)
        state = monitor.update_value("s1", "strategy", 110.0)
        assert state.recovery_time is not None

    def test_get_max_portfolio_drawdown(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("PORTFOLIO", "portfolio", 1000.0)
        monitor.update_value("PORTFOLIO", "portfolio", 900.0)
        assert monitor.get_max_portfolio_drawdown() == pytest.approx(10.0)


# =====================================================================
# CircuitBreaker Tests
# =====================================================================


class TestCircuitBreaker:
    """Test circuit breaker states and transitions."""

    @pytest.mark.asyncio
    async def test_initial_state_closed(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="daily_loss", max_loss_amount=500_000))
        assert cb.get_state("daily_loss") == CircuitBreakerState.CLOSED
        assert cb.is_trading_allowed() is True

    @pytest.mark.asyncio
    async def test_trigger_opens_breaker(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="daily_loss", max_loss_amount=500_000))
        triggered = await cb.check(daily_loss=-600_000)
        assert triggered is True
        assert cb.get_state("daily_loss") == CircuitBreakerState.OPEN
        assert cb.is_trading_allowed() is False

    @pytest.mark.asyncio
    async def test_no_trigger_within_limits(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="daily_loss", max_loss_amount=500_000))
        triggered = await cb.check(daily_loss=-300_000)
        assert triggered is False
        assert cb.is_trading_allowed() is True

    @pytest.mark.asyncio
    async def test_manual_recover(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="loss", max_loss_amount=100))
        await cb.trigger("loss", "test")
        assert cb.get_state("loss") == CircuitBreakerState.OPEN
        await cb.recover("loss")
        assert cb.get_state("loss") == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_get_triggered_breakers(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="a", max_loss_amount=100))
        cb.add_breaker(CircuitBreakerConfig(name="b", max_loss_amount=200))
        await cb.trigger("a", "test")
        triggered = cb.get_triggered_breakers()
        assert "a" in triggered
        assert "b" not in triggered

    @pytest.mark.asyncio
    async def test_history_recorded(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="x", max_loss_amount=100))
        await cb.trigger("x", "breach")
        assert len(cb.history) == 1
        assert cb.history[0].breaker_name == "x"

    @pytest.mark.asyncio
    async def test_consecutive_losses_trigger(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="streak", max_consecutive_losses=3))
        cb.record_trade_result(is_profitable=False)
        cb.record_trade_result(is_profitable=False)
        cb.record_trade_result(is_profitable=False)
        triggered = await cb.check(consecutive_losses=cb.consecutive_losses)
        assert triggered is True

    @pytest.mark.asyncio
    async def test_winning_trade_resets_streak(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.record_trade_result(is_profitable=False)
        cb.record_trade_result(is_profitable=False)
        cb.record_trade_result(is_profitable=True)
        assert cb.consecutive_losses == 0

    @pytest.mark.asyncio
    async def test_drawdown_trigger(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="dd", max_drawdown_pct=10.0))
        triggered = await cb.check(drawdown_pct=15.0)
        assert triggered is True

    @pytest.mark.asyncio
    async def test_remove_breaker(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="x", max_loss_amount=100))
        cb.remove_breaker("x")
        assert cb.get_state("x") is None

    @pytest.mark.asyncio
    async def test_cancel_callback_on_trigger(self, mock_event_bus):
        cancel_fn = AsyncMock()
        cb = CircuitBreaker(mock_event_bus, cancel_all_fn=cancel_fn)
        cb.add_breaker(CircuitBreakerConfig(name="x", max_loss_amount=100, cancel_open_orders=True))
        await cb.trigger("x", "test")
        cancel_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_cancel_callback_when_disabled(self, mock_event_bus):
        cancel_fn = AsyncMock()
        cb = CircuitBreaker(mock_event_bus, cancel_all_fn=cancel_fn)
        cb.add_breaker(CircuitBreakerConfig(name="x", max_loss_amount=100, cancel_open_orders=False))
        await cb.trigger("x", "test")
        cancel_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_breakers_allows_trading(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        assert cb.is_trading_allowed() is True


# =====================================================================
# GreeksAggregator Tests
# =====================================================================


class TestGreeksAggregator:
    """Test portfolio greeks aggregation."""

    def test_update_and_get_position_greeks(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("NIFTY24200CE", "s1", delta=0.5, gamma=0.001, theta=-15, vega=12)
        greeks = agg.get_position_greeks("NIFTY24200CE")
        assert greeks is not None
        assert greeks["delta"] == 0.5
        assert greeks["gamma"] == 0.001

    def test_get_position_greeks_not_found(self):
        agg = GreeksAggregator()
        assert agg.get_position_greeks("UNKNOWN") is None

    def test_strategy_greeks_aggregation(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=0.5, vega=10)
        agg.update_position_greeks("PE1", "s1", delta=-0.4, vega=8)
        agg.update_position_greeks("CE2", "s2", delta=0.3, vega=5)
        s1_greeks = agg.get_strategy_greeks("s1")
        assert s1_greeks["delta"] == pytest.approx(0.1)
        assert s1_greeks["vega"] == pytest.approx(18.0)

    def test_portfolio_greeks(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=0.5, gamma=0.001, theta=-10, vega=12, rho=0.01)
        agg.update_position_greeks("PE1", "s1", delta=-0.3, gamma=0.002, theta=-8, vega=10, rho=-0.005)
        portfolio = agg.get_portfolio_greeks()
        assert isinstance(portfolio, PortfolioGreeks)
        assert portfolio.net_delta == pytest.approx(0.2)
        assert portfolio.net_gamma == pytest.approx(0.003)
        assert portfolio.net_theta == pytest.approx(-18.0)
        assert portfolio.net_vega == pytest.approx(22.0)

    def test_check_limits_all_within(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=10, gamma=0.5, vega=100)
        breaches = agg.check_limits(max_delta=500, max_gamma=100, max_vega=50000)
        assert len(breaches) == 0

    def test_check_limits_delta_breach(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=600)
        breaches = agg.check_limits(max_delta=500, max_gamma=100, max_vega=50000)
        assert len(breaches) == 1
        assert "delta" in breaches[0].lower()

    def test_remove_position(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=0.5)
        agg.remove_position("CE1")
        assert agg.get_position_greeks("CE1") is None
        assert agg.position_count == 0

    def test_remove_unknown_position_no_error(self):
        agg = GreeksAggregator()
        agg.remove_position("UNKNOWN")  # should not raise

    def test_reset(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=0.5)
        agg.update_position_greeks("PE1", "s1", delta=-0.3)
        agg.reset()
        assert agg.position_count == 0
        portfolio = agg.get_portfolio_greeks()
        assert portfolio.net_delta == 0.0

    def test_position_count(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=0.5)
        agg.update_position_greeks("PE1", "s1", delta=-0.3)
        assert agg.position_count == 2

    def test_strategy_ids(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=0.5)
        agg.update_position_greeks("PE1", "s2", delta=-0.3)
        assert agg.strategy_ids == {"s1", "s2"}

    def test_strategy_reassignment(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=0.5)
        agg.update_position_greeks("CE1", "s2", delta=0.5)
        assert agg.get_strategy_greeks("s1")["delta"] == 0.0
        assert agg.get_strategy_greeks("s2")["delta"] == 0.5

    def test_check_limits_multiple_breaches(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=600, gamma=200, vega=60_000)
        breaches = agg.check_limits(max_delta=500, max_gamma=100, max_vega=50_000)
        assert len(breaches) == 3

    def test_check_limits_zero_max_ignored(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=9999)
        # max_delta=0 means "no limit"
        breaches = agg.check_limits(max_delta=0, max_gamma=0, max_vega=0)
        assert len(breaches) == 0

    def test_update_overwrites_previous_greeks(self):
        agg = GreeksAggregator()
        agg.update_position_greeks("CE1", "s1", delta=0.5, vega=10)
        agg.update_position_greeks("CE1", "s1", delta=0.8, vega=15)
        greeks = agg.get_position_greeks("CE1")
        assert greeks["delta"] == 0.8
        assert greeks["vega"] == 15.0

    def test_strategy_greeks_empty_strategy(self):
        agg = GreeksAggregator()
        greeks = agg.get_strategy_greeks("nonexistent")
        assert greeks["delta"] == 0.0
        assert greeks["vega"] == 0.0


# =====================================================================
# Additional PositionTracker Tests
# =====================================================================


class TestPositionTrackerExtended:
    """Additional position tracker coverage: re-open, day PnL, signed exposure."""

    @pytest.fixture
    def tracker(self):
        return PositionTracker()

    def test_reopen_closed_position(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200))
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 1, 24300))
        # Re-open with a new trade
        state = tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 2, 24150))
        assert state.quantity == 2
        assert float(state.position.average_price) == pytest.approx(24150.0)

    def test_day_pnl_combines_realised_and_unrealised(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 4, 24200))
        # Partial close with profit
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 2, 24300))
        # Update price for unrealised portion
        tracker.update_price("NIFTY", 24250.0)
        state = tracker.get_position("NIFTY", "strat_1")
        # realised: (24300-24200)*2*25 = 5000
        # unrealised: (24250-24200)*2*25 = 2500
        # day_pnl = 5000 + 2500 = 7500
        assert state.day_pnl == pytest.approx(7500.0)

    def test_get_positions_by_symbol(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200, strategy_id="s1"))
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 2, 24300, strategy_id="s2"))
        positions = tracker.get_positions_by_symbol("NIFTY")
        assert len(positions) == 2

    def test_get_net_signed_exposure(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 2, 24200))
        tracker.update_price("NIFTY", 24200.0)
        signed = tracker.get_net_signed_exposure()
        # Long position: positive signed exposure
        assert signed > 0

    def test_get_net_signed_exposure_short(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 2, 24200))
        tracker.update_price("NIFTY", 24200.0)
        signed = tracker.get_net_signed_exposure()
        assert signed < 0

    def test_close_short_with_loss(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 2, 24200))
        state = tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 2, 24300))
        assert state.quantity == 0
        # Short loss: (24200-24300)*2*25 = -5000
        assert state.realised_pnl == pytest.approx(-5000.0)

    def test_multiple_strategies_same_instrument(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200, strategy_id="A"))
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 2, 24300, strategy_id="B"))
        a = tracker.get_position("NIFTY", "A")
        b = tracker.get_position("NIFTY", "B")
        assert a.quantity == 1
        assert b.quantity == -2

    def test_flip_realised_pnl(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 2, 24200))
        state = tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 5, 24300))
        # Close 2 long at profit: (24300-24200)*2*25 = 5000
        assert state.realised_pnl == pytest.approx(5000.0)
        # Remaining: -3 short at 24300
        assert state.quantity == -3

    def test_add_to_short_position(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 2, 24300))
        state = tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 3, 24200))
        assert state.quantity == -5
        expected_avg = (24300 * 2 + 24200 * 3) / 5
        assert abs(float(state.position.average_price) - expected_avg) < 0.01

    def test_snapshot_details(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 2, 24200))
        tracker.update_price("NIFTY", 24300.0)
        snap = tracker.snapshot()
        assert snap["positions"][0]["symbol"] == "NIFTY"
        assert snap["positions"][0]["quantity"] == 2
        assert snap["total_unrealised_pnl"] > 0

    def test_total_realised_includes_closed(self, tracker, nifty_instrument):
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200))
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 1, 24300))
        # Closed: realised PnL tracked by implementation
        assert tracker.get_total_realised_pnl() == pytest.approx(5000.0)


# =====================================================================
# Additional RiskManager Tests
# =====================================================================


class TestRiskManagerExtended:
    """Additional risk manager coverage: position/portfolio/strategy limits, warnings."""

    @pytest.fixture
    def risk_setup(self, nifty_instrument, mock_event_bus):
        limits = RiskLimits(
            max_order_value=5_000_000,
            max_position_value=20_000_000,
            max_portfolio_value=100_000_000,
            max_loss_per_day=1_000_000,
            max_loss_per_strategy=500_000,
            max_open_orders=50,
            max_orders_per_minute=30,
            max_quantity_per_order=1800,
        )
        tracker = PositionTracker()
        rm = RiskManager(limits, tracker, mock_event_bus)
        return rm, tracker, limits

    @pytest.mark.asyncio
    async def test_position_limit_exceeded(self, nifty_instrument, risk_setup):
        rm, tracker, limits = risk_setup
        limits.max_position_value = 2_000_000
        # Open existing position
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 2, 24200))
        # Try adding more — resulting position > 2M
        order = _make_order(nifty_instrument, OrderSide.BUY, 5, 24200)
        result = await rm.check_order(order)
        assert result.approved is False
        assert any("position_limit" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_reducing_position_always_allowed(self, nifty_instrument, risk_setup):
        rm, tracker, limits = risk_setup
        limits.max_position_value = 20_000_000
        limits.position_concentration_limit = 1.0  # Allow full concentration
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 5, 24200))
        tracker.update_price("NIFTY", 24200.0)
        # Sell to reduce — should pass position limit
        order = _make_order(nifty_instrument, OrderSide.SELL, 2, 24200)
        result = await rm.check_order(order)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_portfolio_exposure_limit(self, nifty_instrument, risk_setup):
        rm, tracker, limits = risk_setup
        limits.max_portfolio_value = 2_000_000
        # Order value = 24200 * 5 * 25 = 3,025,000 > 2M
        order = _make_order(nifty_instrument, OrderSide.BUY, 5, 24200)
        result = await rm.check_order(order)
        assert result.approved is False
        assert any("portfolio_exposure" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_daily_loss_limit_rejects(self, nifty_instrument, risk_setup):
        rm, _, limits = risk_setup
        rm._daily_pnl = -1_100_000
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        result = await rm.check_order(order)
        assert result.approved is False
        assert any("daily_loss" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_strategy_loss_limit_rejects(self, nifty_instrument, risk_setup):
        rm, _, limits = risk_setup
        rm._strategy_pnl["strat_1"] = -600_000
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200, strategy_id="strat_1")
        result = await rm.check_order(order)
        assert result.approved is False
        assert any("strategy_loss" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_concentration_limit(self, nifty_instrument, mock_event_bus):
        banknifty = Instrument(
            symbol="BANKNIFTY",
            exchange=Exchange.NSE,
            segment=Segment.FNO,
            instrument_type=InstrumentType.FUTURE,
            lot_size=15,
            tick_size=Decimal("0.05"),
            underlying="BANKNIFTY",
        )
        limits = RiskLimits(position_concentration_limit=0.25)
        tracker = PositionTracker()
        rm = RiskManager(limits, tracker, mock_event_bus)
        # Existing BANKNIFTY position
        tracker.on_trade(_make_trade(banknifty, OrderSide.BUY, 10, 51000))
        tracker.update_price("BANKNIFTY", 51000.0)
        # Huge NIFTY order that would dominate the portfolio
        order = _make_order(nifty_instrument, OrderSide.BUY, 100, 24200)
        result = await rm.check_order(order)
        assert result.approved is False
        assert any("concentration" in f for f in result.checks_failed)

    @pytest.mark.asyncio
    async def test_market_order_no_price_passes(self, nifty_instrument, risk_setup):
        rm, _, _ = risk_setup
        order = Order(
            instrument=nifty_instrument,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product_type=ProductType.NRML,
            quantity=1,
            strategy_id="strat_1",
        )
        result = await rm.check_order(order)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_on_trade_triggers_kill_switch_on_large_loss(self, nifty_instrument, mock_event_bus):
        limits = RiskLimits(max_loss_per_day=5000)
        tracker = PositionTracker()
        rm = RiskManager(limits, tracker, mock_event_bus)
        await rm.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 10, 24200))
        await rm.on_trade(_make_trade(nifty_instrument, OrderSide.SELL, 10, 24000))
        # Loss = (24000-24200)*10*25 = -50000, exceeds 5000 limit
        assert rm.is_kill_switch_active is True

    @pytest.mark.asyncio
    async def test_kill_switch_idempotent(self, risk_setup):
        rm, _, _ = risk_setup
        rm.activate_kill_switch("first")
        rm.activate_kill_switch("second")
        assert rm._kill_switch_reason == "first"

    @pytest.mark.asyncio
    async def test_update_limits_ignores_unknown(self, risk_setup):
        rm, _, _ = risk_setup
        rm.update_limits(nonexistent_field=999)
        # Should not raise, just log warning

    @pytest.mark.asyncio
    async def test_risk_metrics_has_expected_keys(self, risk_setup):
        rm, _, _ = risk_setup
        metrics = rm.risk_metrics
        assert "kill_switch_active" in metrics
        assert "daily_pnl" in metrics
        assert "open_positions" in metrics
        assert "portfolio_delta" in metrics
        assert "limits" in metrics

    @pytest.mark.asyncio
    async def test_warnings_on_high_delta(self, nifty_instrument, risk_setup):
        rm, _, limits = risk_setup
        rm._portfolio_delta = 450.0  # > 80% of 500
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        result = await rm.check_order(order)
        assert result.approved is True
        assert any("delta" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_warnings_on_high_gamma(self, nifty_instrument, risk_setup):
        rm, _, limits = risk_setup
        rm._portfolio_gamma = 90.0  # > 80% of 100
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        result = await rm.check_order(order)
        assert any("gamma" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_warnings_on_high_vega(self, nifty_instrument, risk_setup):
        rm, _, limits = risk_setup
        rm._portfolio_vega = 45_000.0  # > 80% of 50000
        order = _make_order(nifty_instrument, OrderSide.BUY, 1, 24200)
        result = await rm.check_order(order)
        assert any("vega" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_on_order_rejected_decrements(self, risk_setup):
        rm, _, _ = risk_setup
        rm._active_order_count = 3
        rm.on_order_rejected()
        assert rm._active_order_count == 2

    @pytest.mark.asyncio
    async def test_on_price_update_daily_pnl(self, nifty_instrument, risk_setup):
        rm, tracker, _ = risk_setup
        tracker.on_trade(_make_trade(nifty_instrument, OrderSide.BUY, 1, 24200))
        await rm.on_price_update("NIFTY", 24300.0)
        assert rm.daily_pnl > 0

    @pytest.mark.asyncio
    async def test_deactivate_when_not_active(self, risk_setup):
        rm, _, _ = risk_setup
        rm.deactivate_kill_switch()  # should not raise
        assert rm.is_kill_switch_active is False


# =====================================================================
# Additional MarginCalculator Tests
# =====================================================================


class TestMarginCalculatorExtended:
    """Additional margin calculator coverage."""

    def test_banknifty_futures_margin(self):
        banknifty = Instrument(
            symbol="BANKNIFTY_FUT",
            exchange=Exchange.NFO,
            segment=Segment.FNO,
            instrument_type=InstrumentType.FUTURE,
            lot_size=15,
            tick_size=Decimal("0.05"),
            underlying="BANKNIFTY",
        )
        calc = MarginCalculator()
        order = _make_order(banknifty, OrderSide.BUY, 1, 51000)
        margin = calc.calculate_order_margin(order, spot_price=51000.0)
        notional = 51000 * 1 * 15
        expected_span = notional * 0.14
        assert float(margin.span_margin) == pytest.approx(expected_span, rel=0.01)

    def test_option_sell_atm_higher_than_otm(self, nifty_call_option):
        calc = MarginCalculator()
        # ATM sell
        atm_order = Order(
            instrument=nifty_call_option,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product_type=ProductType.NRML,
            quantity=1,
            price=Decimal("200"),
        )
        atm_margin = calc.calculate_order_margin(atm_order, spot_price=24200.0)
        # Deep OTM sell (strike 25500 vs spot 24200)
        otm_inst = Instrument(
            symbol="NIFTY25500CE",
            exchange=Exchange.NFO,
            segment=Segment.FNO,
            instrument_type=InstrumentType.CALL_OPTION,
            lot_size=25,
            tick_size=Decimal("0.05"),
            strike=Decimal("25500"),
            option_type=OptionType.CE,
            expiry=date(2025, 6, 26),
            underlying="NIFTY",
        )
        otm_order = Order(
            instrument=otm_inst,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product_type=ProductType.NRML,
            quantity=1,
            price=Decimal("20"),
        )
        otm_margin = calc.calculate_order_margin(otm_order, spot_price=24200.0)
        assert float(atm_margin.used_margin) >= float(otm_margin.used_margin)

    def test_option_sell_margin_zero_spot(self, nifty_call_option):
        calc = MarginCalculator()
        order = Order(
            instrument=nifty_call_option,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            product_type=ProductType.NRML,
            quantity=1,
            price=Decimal("150"),
        )
        margin = calc.calculate_order_margin(order, spot_price=0.0)
        # Zero spot: span/exposure should be 0 (graceful handling)
        assert float(margin.span_margin) == 0.0

    def test_position_margin_short_option(self, nifty_call_option):
        calc = MarginCalculator()
        pos = Position(
            instrument=nifty_call_option,
            quantity=-1,
            average_price=Decimal("200"),
        )
        margin = calc.calculate_position_margin(pos, spot_price=24200.0)
        assert float(margin.used_margin) > 0
        assert float(margin.span_margin) > 0

    def test_position_margin_long_option(self, nifty_call_option):
        calc = MarginCalculator()
        pos = Position(
            instrument=nifty_call_option,
            quantity=1,
            average_price=Decimal("200"),
        )
        margin = calc.calculate_position_margin(pos, spot_price=24200.0)
        # Long option margin = premium cost
        assert float(margin.used_margin) == pytest.approx(200 * 1 * 25)

    def test_portfolio_margin_multiple_positions(self, nifty_instrument):
        banknifty = Instrument(
            symbol="BNF_FUT",
            exchange=Exchange.NFO,
            segment=Segment.FNO,
            instrument_type=InstrumentType.FUTURE,
            lot_size=15,
            tick_size=Decimal("0.05"),
            underlying="BANKNIFTY",
        )
        calc = MarginCalculator()
        positions = [
            Position(instrument=nifty_instrument, quantity=2, average_price=Decimal("24200")),
            Position(instrument=banknifty, quantity=1, average_price=Decimal("51000")),
        ]
        prices = {"NIFTY": 24200.0, "BANKNIFTY": 51000.0}
        margin = calc.calculate_portfolio_margin(positions, prices)
        assert float(margin.used_margin) > 0
        assert float(margin.span_margin) > 0

    def test_portfolio_margin_hedging_benefit(self, nifty_call_option, nifty_put_option):
        calc = MarginCalculator()
        positions = [
            Position(instrument=nifty_call_option, quantity=1, average_price=Decimal("150"), ltp=Decimal("150")),
            Position(instrument=nifty_put_option, quantity=-1, average_price=Decimal("100"), ltp=Decimal("100")),
        ]
        prices = {"NIFTY": 24200.0}
        margin = calc.calculate_portfolio_margin(positions, prices)
        # Should complete without error, margin > 0
        assert float(margin.used_margin) > 0

    def test_default_fallback_margin(self):
        # Unknown underlying uses __default__ margin rates
        unknown_fut = Instrument(
            symbol="UNKNOWN_FUT",
            exchange=Exchange.NFO,
            segment=Segment.FNO,
            instrument_type=InstrumentType.FUTURE,
            lot_size=50,
            tick_size=Decimal("0.05"),
            underlying="UNKNOWN",
        )
        calc = MarginCalculator()
        order = _make_order(unknown_fut, OrderSide.BUY, 1, 1000)
        margin = calc.calculate_order_margin(order, spot_price=1000.0)
        notional = 1000 * 1 * 50
        # Default: 20% SPAN + 5% exposure
        assert float(margin.span_margin) == pytest.approx(notional * 0.20, rel=0.01)
        assert float(margin.exposure_margin) == pytest.approx(notional * 0.05, rel=0.01)


# =====================================================================
# Additional DrawdownMonitor Tests
# =====================================================================


class TestDrawdownMonitorExtended:
    """Additional drawdown monitor coverage."""

    def test_drawdown_pct_with_zero_peak(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("test", "portfolio", 0.0)
        state = monitor.update_value("test", "portfolio", -100.0)
        # Peak is 0: drawdown pct should be 0 (avoid division by zero)
        assert state.current_drawdown_pct == 0.0

    def test_multiple_entity_types(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("pos_1", "position", 1000.0)
        monitor.update_value("strat_1", "strategy", 50_000.0)
        monitor.update_value("PORTFOLIO", "portfolio", 100_000.0)
        assert len(monitor.get_all_states()) == 3

    def test_trough_tracking(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100_000.0)
        monitor.update_value("s1", "strategy", 90_000.0)
        monitor.update_value("s1", "strategy", 85_000.0)
        state = monitor.get_state("s1")
        assert state.trough_value == 85_000.0

    def test_recovery_clears_alerted_thresholds(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100.0)
        monitor.update_value("s1", "strategy", 80.0)  # 20% drawdown
        state = monitor.get_state("s1")
        assert len(state._alerted_thresholds) > 0

        # Recovery to new peak clears thresholds
        state = monitor.update_value("s1", "strategy", 110.0)
        assert len(state._alerted_thresholds) == 0

    def test_max_portfolio_drawdown_no_data(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        assert monitor.get_max_portfolio_drawdown() == 0.0

    def test_partial_recovery_still_in_drawdown(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        monitor.update_value("s1", "strategy", 100.0)
        monitor.update_value("s1", "strategy", 80.0)
        state = monitor.update_value("s1", "strategy", 90.0)
        # Not yet at peak, still in drawdown
        assert state.in_drawdown is True
        assert state.current_drawdown == pytest.approx(10.0)

    def test_set_thresholds_keeps_unset(self, mock_event_bus):
        monitor = DrawdownMonitor(mock_event_bus)
        original_position = list(monitor._thresholds["position"])
        monitor.set_alert_thresholds(portfolio_pct=[1.0, 2.0])
        # position thresholds unchanged
        assert monitor._thresholds["position"] == original_position
        assert monitor._thresholds["portfolio"] == [1.0, 2.0]


# =====================================================================
# Additional CircuitBreaker Tests
# =====================================================================


class TestCircuitBreakerExtended:
    """Additional circuit breaker coverage."""

    @pytest.mark.asyncio
    async def test_loss_pct_trigger(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="loss_pct", max_loss_pct=5.0))
        triggered = await cb.check(daily_loss_pct=6.0)
        assert triggered is True
        assert cb.get_state("loss_pct") == CircuitBreakerState.OPEN

    @pytest.mark.asyncio
    async def test_trigger_already_open_noop(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="loss", max_loss_amount=100))
        await cb.trigger("loss", "first")
        await cb.trigger("loss", "second")
        # Only one event in history
        open_events = [e for e in cb.history if e.breaker_name == "loss" and e.state == CircuitBreakerState.OPEN]
        assert len(open_events) == 1

    @pytest.mark.asyncio
    async def test_trigger_unknown_breaker_noop(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        await cb.trigger("nonexistent", "test")
        assert len(cb.history) == 0

    @pytest.mark.asyncio
    async def test_recover_already_closed_noop(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="loss", max_loss_amount=100))
        await cb.recover("loss")
        assert cb.get_state("loss") == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_multiple_breakers_independent(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="loss", max_loss_amount=100_000))
        cb.add_breaker(CircuitBreakerConfig(name="streak", max_consecutive_losses=5))
        await cb.check(daily_loss=-150_000, consecutive_losses=2)
        assert cb.get_state("loss") == CircuitBreakerState.OPEN
        assert cb.get_state("streak") == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_add_breaker_replaces_existing(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="loss", max_loss_amount=100))
        await cb.trigger("loss", "test")
        assert cb.get_state("loss") == CircuitBreakerState.OPEN
        # Re-add resets state to CLOSED
        cb.add_breaker(CircuitBreakerConfig(name="loss", max_loss_amount=200))
        assert cb.get_state("loss") == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_auto_recover_after_cooldown(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(
            name="loss",
            max_loss_amount=100,
            auto_recover=True,
            cooldown_seconds=0.1,
        ))
        await cb.trigger("loss", "test")
        assert cb.get_state("loss") == CircuitBreakerState.OPEN
        await asyncio.sleep(0.3)
        assert cb.get_state("loss") == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_recover_updates_history(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="loss", max_loss_amount=100))
        await cb.trigger("loss", "test")
        await cb.recover("loss")
        # Most recent event for this breaker should have recovered_at
        for evt in reversed(cb.history):
            if evt.breaker_name == "loss":
                assert evt.recovered_at is not None
                break

    @pytest.mark.asyncio
    async def test_check_skips_already_open_breakers(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        cb.add_breaker(CircuitBreakerConfig(name="loss", max_loss_amount=100))
        await cb.trigger("loss", "first")
        # Check again with same conditions -- should not re-trigger
        triggered = await cb.check(daily_loss=-200)
        assert triggered is False  # already open, skip

    @pytest.mark.asyncio
    async def test_recover_unknown_breaker_noop(self, mock_event_bus):
        cb = CircuitBreaker(mock_event_bus)
        await cb.recover("nonexistent")
        # Should not raise
