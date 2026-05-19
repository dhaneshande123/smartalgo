"""
Comprehensive unit tests for core/models.py — Pydantic v2 data models.

Covers enums, validators, serialization round-trips, edge cases, and
the TransactionCharges.calculate() class method.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.models import (
    Alert,
    AlertLevel,
    Candle,
    Event,
    Exchange,
    Greeks,
    Instrument,
    InstrumentType,
    MarginInfo,
    OptionChain,
    OptionContract,
    OptionType,
    Order,
    OrderEvent,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    PayoffDiagram,
    PayoffPoint,
    PnLSnapshot,
    Position,
    PortfolioGreeks,
    ProductType,
    RiskEvent,
    RiskMetrics,
    Segment,
    StrategyConfig,
    StrategyState,
    StrategyStatus,
    Tick,
    TickEvent,
    TimeFrame,
    Trade,
    TradeEvent,
    TradingMode,
    TransactionCharges,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
TODAY = date.today()


def _make_equity_instrument(**overrides) -> Instrument:
    defaults = dict(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        instrument_type=InstrumentType.STOCK,
    )
    defaults.update(overrides)
    return Instrument(**defaults)


def _make_option_instrument(**overrides) -> Instrument:
    defaults = dict(
        symbol="NIFTY24MAR22000CE",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.CALL_OPTION,
        strike=Decimal("22000"),
        option_type=OptionType.CE,
        expiry=TODAY,
        lot_size=50,
        underlying="NIFTY",
    )
    defaults.update(overrides)
    return Instrument(**defaults)


# ===================================================================
# 1. Enum completeness
# ===================================================================


class TestEnums:
    def test_exchange_values(self):
        assert set(Exchange) == {
            Exchange.NSE, Exchange.BSE, Exchange.NFO,
            Exchange.BFO, Exchange.CDS, Exchange.MCX,
        }

    def test_segment_values(self):
        assert set(Segment) == {
            Segment.EQUITY, Segment.FNO, Segment.CURRENCY, Segment.COMMODITY,
        }

    def test_instrument_type_values(self):
        assert set(InstrumentType) == {
            InstrumentType.STOCK, InstrumentType.INDEX,
            InstrumentType.FUTURE, InstrumentType.CALL_OPTION,
            InstrumentType.PUT_OPTION,
        }

    def test_order_type_values(self):
        assert set(OrderType) == {
            OrderType.MARKET, OrderType.LIMIT,
            OrderType.SL, OrderType.SL_M, OrderType.GTT,
        }

    def test_order_side_values(self):
        assert set(OrderSide) == {OrderSide.BUY, OrderSide.SELL}

    def test_order_status_values(self):
        assert set(OrderStatus) == {
            OrderStatus.PENDING, OrderStatus.PLACED, OrderStatus.OPEN,
            OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED,
            OrderStatus.REJECTED, OrderStatus.ERROR,
        }

    def test_product_type_values(self):
        assert set(ProductType) == {
            ProductType.MIS, ProductType.NRML, ProductType.CNC,
        }

    def test_strategy_status_values(self):
        assert set(StrategyStatus) == {
            StrategyStatus.INITIALIZING, StrategyStatus.RUNNING,
            StrategyStatus.PAUSED, StrategyStatus.STOPPED,
            StrategyStatus.ERROR,
        }

    def test_timeframe_values(self):
        expected = {"TICK", "S1", "S5", "M1", "M5", "M15", "M30", "H1", "D1", "W1", "MO1"}
        assert {t.value for t in TimeFrame} == expected

    def test_option_type_values(self):
        assert set(OptionType) == {OptionType.CE, OptionType.PE}

    def test_alert_level_values(self):
        assert set(AlertLevel) == {AlertLevel.INFO, AlertLevel.WARNING, AlertLevel.CRITICAL}

    def test_trading_mode_values(self):
        assert set(TradingMode) == {TradingMode.LIVE, TradingMode.PAPER, TradingMode.BACKTEST}

    def test_enum_str_mixin(self):
        """All enums are str enums and their .value equals the expected string."""
        assert Exchange.NSE.value == "NSE"
        assert OrderSide.BUY.value == "BUY"


# ===================================================================
# 2. Instrument validation
# ===================================================================


class TestInstrument:
    def test_valid_equity_instrument(self):
        inst = _make_equity_instrument()
        assert inst.symbol == "RELIANCE"
        assert inst.instrument_type == InstrumentType.STOCK

    def test_valid_option_instrument(self):
        inst = _make_option_instrument()
        assert inst.strike == Decimal("22000")
        assert inst.option_type == OptionType.CE
        assert inst.expiry == TODAY

    def test_option_missing_strike_raises(self):
        with pytest.raises(ValidationError, match="strike"):
            _make_option_instrument(strike=None)

    def test_option_missing_option_type_raises(self):
        with pytest.raises(ValidationError, match="option_type"):
            _make_option_instrument(option_type=None)

    def test_option_missing_expiry_raises(self):
        with pytest.raises(ValidationError, match="expiry"):
            _make_option_instrument(expiry=None)

    def test_put_option_requires_all_fields(self):
        with pytest.raises(ValidationError):
            Instrument(
                symbol="NIFTY24MAR22000PE",
                exchange=Exchange.NFO,
                segment=Segment.FNO,
                instrument_type=InstrumentType.PUT_OPTION,
                # missing strike, option_type, expiry
            )

    def test_stock_does_not_require_option_fields(self):
        inst = _make_equity_instrument()
        assert inst.strike is None
        assert inst.option_type is None
        assert inst.expiry is None

    def test_lot_size_must_be_positive(self):
        with pytest.raises(ValidationError):
            _make_equity_instrument(lot_size=0)

    def test_tick_size_non_negative(self):
        inst = _make_equity_instrument(tick_size=Decimal("0"))
        assert inst.tick_size == Decimal("0")

    def test_serialization_round_trip(self):
        inst = _make_option_instrument()
        data = inst.model_dump(mode="json")
        restored = Instrument.model_validate(data)
        assert restored == inst


# ===================================================================
# 3. Candle OHLC validator
# ===================================================================


class TestCandle:
    def _make_candle(self, **overrides):
        defaults = dict(
            instrument_id="RELIANCE",
            symbol="RELIANCE",
            timeframe=TimeFrame.M5,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("105"),
            timestamp=NOW,
        )
        defaults.update(overrides)
        return Candle(**defaults)

    def test_valid_candle(self):
        c = self._make_candle()
        assert c.high >= c.low
        assert c.low <= c.open <= c.high
        assert c.low <= c.close <= c.high

    def test_high_less_than_low_raises(self):
        with pytest.raises(ValidationError, match="high must be >= low"):
            self._make_candle(high=Decimal("90"), low=Decimal("95"))

    def test_open_below_low_raises(self):
        with pytest.raises(ValidationError, match="open must be between low and high"):
            self._make_candle(open=Decimal("80"))

    def test_open_above_high_raises(self):
        with pytest.raises(ValidationError, match="open must be between low and high"):
            self._make_candle(open=Decimal("120"))

    def test_close_below_low_raises(self):
        with pytest.raises(ValidationError, match="close must be between low and high"):
            self._make_candle(close=Decimal("80"))

    def test_close_above_high_raises(self):
        with pytest.raises(ValidationError, match="close must be between low and high"):
            self._make_candle(close=Decimal("120"))

    def test_all_equal_ohlc(self):
        """Doji candle where O=H=L=C is valid."""
        c = self._make_candle(
            open=Decimal("100"), high=Decimal("100"),
            low=Decimal("100"), close=Decimal("100"),
        )
        assert c.open == c.close == c.high == c.low

    def test_volume_non_negative(self):
        with pytest.raises(ValidationError):
            self._make_candle(volume=-1)

    def test_zero_prices_valid(self):
        """Edge: zero prices are allowed if OHLC constraints hold."""
        c = self._make_candle(
            open=Decimal("0"), high=Decimal("0"),
            low=Decimal("0"), close=Decimal("0"),
        )
        assert c.high == Decimal("0")

    def test_serialization_round_trip(self):
        c = self._make_candle()
        data = c.model_dump(mode="json")
        restored = Candle.model_validate(data)
        assert restored == c


# ===================================================================
# 4. Order validation
# ===================================================================


class TestOrder:
    def _make_order(self, **overrides):
        defaults = dict(
            instrument=_make_equity_instrument(),
            order_type=OrderType.MARKET,
            side=OrderSide.BUY,
            product_type=ProductType.MIS,
            quantity=10,
        )
        defaults.update(overrides)
        return Order(**defaults)

    def test_valid_market_order(self):
        o = self._make_order()
        assert o.status == OrderStatus.PENDING
        assert o.quantity == 10

    def test_limit_order_requires_price(self):
        with pytest.raises(ValidationError, match="LIMIT orders require a price"):
            self._make_order(order_type=OrderType.LIMIT, price=None)

    def test_limit_order_with_price_ok(self):
        o = self._make_order(order_type=OrderType.LIMIT, price=Decimal("100"))
        assert o.price == Decimal("100")

    def test_sl_order_requires_trigger_price(self):
        with pytest.raises(ValidationError, match="SL / SL_M orders require a trigger_price"):
            self._make_order(order_type=OrderType.SL, trigger_price=None)

    def test_sl_m_order_requires_trigger_price(self):
        with pytest.raises(ValidationError, match="SL / SL_M orders require a trigger_price"):
            self._make_order(order_type=OrderType.SL_M, trigger_price=None)

    def test_sl_order_with_trigger_ok(self):
        o = self._make_order(order_type=OrderType.SL, trigger_price=Decimal("95"), price=Decimal("94"))
        assert o.trigger_price == Decimal("95")

    def test_quantity_must_be_positive(self):
        with pytest.raises(ValidationError):
            self._make_order(quantity=0)

    def test_negative_quantity_fails(self):
        with pytest.raises(ValidationError):
            self._make_order(quantity=-5)

    def test_order_id_auto_generated(self):
        o1 = self._make_order()
        o2 = self._make_order()
        assert o1.order_id != o2.order_id
        assert len(o1.order_id) == 32  # uuid4 hex

    def test_tag_max_length(self):
        with pytest.raises(ValidationError):
            self._make_order(tag="a" * 21)

    def test_serialization_round_trip(self):
        o = self._make_order(order_type=OrderType.LIMIT, price=Decimal("500"))
        data = o.model_dump(mode="json")
        restored = Order.model_validate(data)
        assert restored.price == o.price
        assert restored.instrument.symbol == o.instrument.symbol


# ===================================================================
# 5. TransactionCharges.calculate()
# ===================================================================


class TestTransactionCharges:
    def test_equity_delivery_buy(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.BUY,
            instrument_type=InstrumentType.STOCK,
            quantity=100,
            price=Decimal("1000"),
            is_intraday=False,
        )
        # turnover = 100_000
        assert tc.brokerage == Decimal("20.00")  # min(20, 100000*0.0003=30) = 20
        assert tc.stt == Decimal("100.00")  # 100000 * 0.001
        assert tc.stamp_duty > Decimal("0")  # buy side has stamp duty
        assert tc.total > Decimal("0")

    def test_equity_delivery_sell_no_stamp_duty(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.SELL,
            instrument_type=InstrumentType.STOCK,
            quantity=100,
            price=Decimal("1000"),
            is_intraday=False,
        )
        assert tc.stamp_duty == Decimal("0.00")

    def test_equity_intraday_sell_stt(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.SELL,
            instrument_type=InstrumentType.STOCK,
            quantity=100,
            price=Decimal("1000"),
            is_intraday=True,
        )
        # Intraday sell: 0.025% STT
        expected_stt = (Decimal("100000") * Decimal("0.00025")).quantize(Decimal("0.01"))
        assert tc.stt == expected_stt

    def test_equity_intraday_buy_no_stt(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.BUY,
            instrument_type=InstrumentType.STOCK,
            quantity=100,
            price=Decimal("1000"),
            is_intraday=True,
        )
        assert tc.stt == Decimal("0.00")

    def test_future_sell_stt(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.SELL,
            instrument_type=InstrumentType.FUTURE,
            quantity=50,
            price=Decimal("22000"),
        )
        turnover = Decimal("1100000")
        expected_stt = (turnover * Decimal("0.0002")).quantize(Decimal("0.01"))
        assert tc.stt == expected_stt

    def test_future_buy_no_stt(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.BUY,
            instrument_type=InstrumentType.FUTURE,
            quantity=50,
            price=Decimal("22000"),
        )
        assert tc.stt == Decimal("0.00")

    def test_option_sell_stt(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.SELL,
            instrument_type=InstrumentType.CALL_OPTION,
            quantity=50,
            price=Decimal("200"),
        )
        turnover = Decimal("10000")
        expected_stt = (turnover * Decimal("0.001")).quantize(Decimal("0.01"))
        assert tc.stt == expected_stt

    def test_option_buy_no_stt(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.BUY,
            instrument_type=InstrumentType.PUT_OPTION,
            quantity=50,
            price=Decimal("200"),
        )
        assert tc.stt == Decimal("0.00")

    def test_gst_is_18_pct_of_brokerage_plus_txn(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.BUY,
            instrument_type=InstrumentType.STOCK,
            quantity=100,
            price=Decimal("1000"),
        )
        expected_gst = ((tc.brokerage + tc.exchange_txn_fee) * Decimal("0.18")).quantize(Decimal("0.01"))
        assert tc.gst == expected_gst

    def test_total_equals_sum_of_components(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.BUY,
            instrument_type=InstrumentType.STOCK,
            quantity=100,
            price=Decimal("1000"),
        )
        manual_total = (
            tc.brokerage + tc.stt + tc.exchange_txn_fee
            + tc.gst + tc.sebi_fee + tc.stamp_duty
        )
        # Allow rounding difference of 0.01
        assert abs(tc.total - manual_total) <= Decimal("0.01")

    def test_index_type_zero_charges(self):
        tc = TransactionCharges.calculate(
            side=OrderSide.BUY,
            instrument_type=InstrumentType.INDEX,
            quantity=1,
            price=Decimal("22000"),
        )
        assert tc.stt == Decimal("0.00")
        assert tc.exchange_txn_fee == Decimal("0.00")

    def test_small_turnover_brokerage_capped(self):
        """When turnover is small, brokerage = turnover * 0.03% (not Rs 20)."""
        tc = TransactionCharges.calculate(
            side=OrderSide.BUY,
            instrument_type=InstrumentType.STOCK,
            quantity=1,
            price=Decimal("10"),
        )
        # turnover = 10, brokerage = min(20, 10*0.0003=0.003) = 0.003 -> 0.00
        assert tc.brokerage == Decimal("0.00")


# ===================================================================
# 6. Event models — payload validators
# ===================================================================


class TestEventModels:
    def test_tick_event_valid(self):
        e = TickEvent(
            timestamp=NOW,
            source="market_data",
            payload={"tick": {"ltp": 100}},
        )
        assert e.event_type == "TICK"

    def test_tick_event_missing_tick_key(self):
        with pytest.raises(ValidationError, match="tick"):
            TickEvent(timestamp=NOW, source="md", payload={"data": 1})

    def test_order_event_valid(self):
        e = OrderEvent(
            timestamp=NOW,
            source="order_manager",
            payload={"order": {"id": "abc"}},
        )
        assert e.event_type == "ORDER"

    def test_order_event_missing_order_key(self):
        with pytest.raises(ValidationError, match="order"):
            OrderEvent(timestamp=NOW, source="om", payload={})

    def test_trade_event_valid(self):
        e = TradeEvent(
            timestamp=NOW,
            source="execution",
            payload={"trade": {"id": "xyz"}},
        )
        assert e.event_type == "TRADE"

    def test_trade_event_missing_trade_key(self):
        with pytest.raises(ValidationError, match="trade"):
            TradeEvent(timestamp=NOW, source="exec", payload={"fill": 1})

    def test_risk_event_valid(self):
        e = RiskEvent(
            timestamp=NOW,
            source="risk_engine",
            payload={"risk": {"var": 5000}},
        )
        assert e.event_type == "RISK"

    def test_risk_event_missing_risk_key(self):
        with pytest.raises(ValidationError, match="risk"):
            RiskEvent(timestamp=NOW, source="risk", payload={"alert": True})

    def test_base_event_no_payload_constraint(self):
        e = Event(
            event_type="CUSTOM",
            timestamp=NOW,
            source="test",
            payload={"anything": True},
        )
        assert e.payload == {"anything": True}

    def test_event_id_auto_generated(self):
        e1 = Event(event_type="A", timestamp=NOW, source="s")
        e2 = Event(event_type="A", timestamp=NOW, source="s")
        assert e1.event_id != e2.event_id


# ===================================================================
# 7. Serialization / deserialization round-trips
# ===================================================================


class TestSerializationRoundTrips:
    def test_tick_round_trip(self):
        t = Tick(
            instrument_id="REL",
            symbol="RELIANCE",
            ltp=Decimal("2500"),
            timestamp=NOW,
            exchange=Exchange.NSE,
        )
        data = t.model_dump(mode="json")
        restored = Tick.model_validate(data)
        assert restored.ltp == t.ltp

    def test_trade_round_trip(self):
        inst = _make_equity_instrument()
        t = Trade(
            order_id="ORD-1",
            instrument=inst,
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("2500"),
            timestamp=NOW,
        )
        data = t.model_dump(mode="json")
        restored = Trade.model_validate(data)
        assert restored.quantity == 10

    def test_position_round_trip(self):
        inst = _make_equity_instrument()
        p = Position(instrument=inst, quantity=-10, average_price=Decimal("2500"))
        data = p.model_dump(mode="json")
        restored = Position.model_validate(data)
        assert restored.quantity == -10

    def test_option_contract_round_trip(self):
        inst = _make_option_instrument()
        oc = OptionContract(
            instrument=inst,
            strike=Decimal("22000"),
            option_type=OptionType.CE,
            expiry=TODAY,
            iv=0.18,
            delta=0.45,
        )
        data = oc.model_dump(mode="json")
        restored = OptionContract.model_validate(data)
        assert restored.iv == pytest.approx(0.18)

    def test_greeks_round_trip(self):
        g = Greeks(delta=0.5, gamma=0.02, theta=-5.0, vega=12.0, charm=0.01)
        data = g.model_dump(mode="json")
        restored = Greeks.model_validate(data)
        assert restored.delta == pytest.approx(0.5)

    def test_strategy_config_round_trip(self):
        sc = StrategyConfig(
            name="IronCondor",
            class_path="strategies.iron_condor.IronCondor",
            mode=TradingMode.PAPER,
            params={"width": 200},
        )
        data = sc.model_dump(mode="json")
        restored = StrategyConfig.model_validate(data)
        assert restored.params["width"] == 200

    def test_pnl_snapshot_round_trip(self):
        pnl = PnLSnapshot(strategy_id="S1", timestamp=NOW)
        data = pnl.model_dump(mode="json")
        restored = PnLSnapshot.model_validate(data)
        assert restored.strategy_id == "S1"

    def test_json_string_round_trip(self):
        """Test model -> JSON string -> model."""
        inst = _make_equity_instrument()
        json_str = inst.model_dump_json()
        restored = Instrument.model_validate_json(json_str)
        assert restored == inst


# ===================================================================
# 8. Edge cases
# ===================================================================


class TestEdgeCases:
    def test_negative_price_order_fails(self):
        """Negative price should be rejected by ge=0 constraint."""
        with pytest.raises(ValidationError):
            Order(
                instrument=_make_equity_instrument(),
                order_type=OrderType.LIMIT,
                side=OrderSide.BUY,
                product_type=ProductType.MIS,
                quantity=1,
                price=Decimal("-10"),
            )

    def test_zero_price_limit_order_ok(self):
        """Zero price passes ge=0 but is still set."""
        o = Order(
            instrument=_make_equity_instrument(),
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            product_type=ProductType.MIS,
            quantity=1,
            price=Decimal("0"),
        )
        assert o.price == Decimal("0")

    def test_negative_trigger_price_fails(self):
        with pytest.raises(ValidationError):
            Order(
                instrument=_make_equity_instrument(),
                order_type=OrderType.SL,
                side=OrderSide.BUY,
                product_type=ProductType.MIS,
                quantity=1,
                trigger_price=Decimal("-1"),
            )

    def test_trade_negative_quantity_fails(self):
        with pytest.raises(ValidationError):
            Trade(
                order_id="O1",
                instrument=_make_equity_instrument(),
                side=OrderSide.BUY,
                quantity=-1,
                price=Decimal("100"),
                timestamp=NOW,
            )

    def test_trade_zero_quantity_fails(self):
        with pytest.raises(ValidationError):
            Trade(
                order_id="O1",
                instrument=_make_equity_instrument(),
                side=OrderSide.BUY,
                quantity=0,
                price=Decimal("100"),
                timestamp=NOW,
            )

    def test_margin_utilization_pct_capped(self):
        with pytest.raises(ValidationError):
            MarginInfo(utilization_pct=101.0)

    def test_risk_metrics_margin_util_bounds(self):
        with pytest.raises(ValidationError):
            RiskMetrics(margin_utilization=1.5, timestamp=NOW)

    def test_strategy_config_empty_name_fails(self):
        with pytest.raises(ValidationError):
            StrategyConfig(name="", class_path="a.b.C")

    def test_alert_id_auto_generated(self):
        a = Alert(level=AlertLevel.INFO, source="test", message="hello", timestamp=NOW)
        assert len(a.alert_id) == 32

    def test_order_response_basic(self):
        r = OrderResponse(success=True, order_id="abc", message="placed")
        assert r.success is True

    def test_payoff_diagram_defaults(self):
        pd = PayoffDiagram(strategy_name="IronCondor")
        assert pd.legs == []
        assert pd.points == []
        assert pd.breakevens == []

    def test_portfolio_greeks_defaults(self):
        pg = PortfolioGreeks(timestamp=NOW)
        assert pg.net_delta == 0.0

    def test_option_chain_defaults(self):
        oc = OptionChain(
            underlying_symbol="NIFTY",
            underlying_price=Decimal("22000"),
            expiry=TODAY,
            timestamp=NOW,
        )
        assert oc.contracts == []
        assert oc.pcr == 0.0
