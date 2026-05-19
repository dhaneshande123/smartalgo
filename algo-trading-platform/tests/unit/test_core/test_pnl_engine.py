"""Unit tests for the P&L Engine (ChargesCalculator, TradeBook, PnLCalculator).

Tests cover:
- ChargesCalculator: STT calculation for options/futures/equity, exchange fees,
  GST, stamp duty, total charges, day/strategy breakdowns
- TradeBook: record_trade, get_trades, filter by symbol/date/strategy, summaries
- PnLCalculator: realised P&L for closed trades, unrealised P&L with market
  prices, MTM updates, portfolio/strategy aggregation, snapshots
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core.models import (
    Exchange,
    Instrument,
    InstrumentType,
    OrderSide,
    PnLSnapshot,
    ProductType,
    Segment,
    Trade,
    TransactionCharges,
)
from core.pnl_engine.charges_calculator import ChargesCalculator
from core.pnl_engine.trade_book import TradeBook, TradeRecord
from core.pnl_engine.pnl_calculator import PnLCalculator, PnLEntry


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def nifty_equity():
    return Instrument(
        symbol="NIFTY",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        instrument_type=InstrumentType.INDEX,
        lot_size=25,
        tick_size=Decimal("0.05"),
    )


@pytest.fixture
def reliance_stock():
    return Instrument(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        instrument_type=InstrumentType.STOCK,
        lot_size=1,
        tick_size=Decimal("0.05"),
    )


@pytest.fixture
def nifty_future():
    return Instrument(
        symbol="NIFTY-FUT",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.FUTURE,
        lot_size=25,
        tick_size=Decimal("0.05"),
        expiry=datetime(2026, 4, 30).date(),
    )


@pytest.fixture
def nifty_call_option():
    from core.models import OptionType
    return Instrument(
        symbol="NIFTY-24200CE",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.CALL_OPTION,
        lot_size=25,
        tick_size=Decimal("0.05"),
        expiry=datetime(2026, 4, 30).date(),
        strike=Decimal("24200"),
        option_type=OptionType.CE,
    )


@pytest.fixture
def nifty_put_option():
    from core.models import OptionType
    return Instrument(
        symbol="NIFTY-24000PE",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.PUT_OPTION,
        lot_size=25,
        tick_size=Decimal("0.05"),
        expiry=datetime(2026, 4, 30).date(),
        strike=Decimal("24000"),
        option_type=OptionType.PE,
    )


def _make_trade(
    instrument: Instrument,
    side: OrderSide,
    qty: int,
    price: Decimal,
    strategy_id: str = "strat_1",
    trade_id: str | None = None,
    timestamp: datetime | None = None,
) -> Trade:
    return Trade(
        trade_id=trade_id or f"TRD-{side.value}-{qty}",
        order_id="ORD-001",
        strategy_id=strategy_id,
        instrument=instrument,
        side=side,
        quantity=qty,
        price=price,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


# =====================================================================
# ChargesCalculator Tests
# =====================================================================


class TestChargesCalculator:
    """Tests for ChargesCalculator wrapping TransactionCharges.calculate()."""

    def test_calculate_returns_transaction_charges(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        result = calc.calculate(trade)
        assert isinstance(result, TransactionCharges)

    def test_equity_buy_stt_nonzero(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        result = calc.calculate(trade)
        # Delivery equity: 0.1% on both legs
        assert result.stt > 0

    def test_equity_sell_stt_nonzero(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2500"))
        result = calc.calculate(trade)
        assert result.stt > 0

    def test_futures_buy_stt_zero(self, nifty_future):
        calc = ChargesCalculator()
        trade = _make_trade(nifty_future, OrderSide.BUY, 25, Decimal("24000"))
        result = calc.calculate(trade)
        # Futures STT only on sell side
        assert result.stt == Decimal("0")

    def test_futures_sell_stt_nonzero(self, nifty_future):
        calc = ChargesCalculator()
        trade = _make_trade(nifty_future, OrderSide.SELL, 25, Decimal("24000"))
        result = calc.calculate(trade)
        assert result.stt > 0

    def test_option_buy_stt_zero(self, nifty_call_option):
        calc = ChargesCalculator()
        trade = _make_trade(nifty_call_option, OrderSide.BUY, 25, Decimal("150"))
        result = calc.calculate(trade)
        # Options STT only on sell side
        assert result.stt == Decimal("0")

    def test_option_sell_stt_nonzero(self, nifty_call_option):
        calc = ChargesCalculator()
        trade = _make_trade(nifty_call_option, OrderSide.SELL, 25, Decimal("150"))
        result = calc.calculate(trade)
        assert result.stt > 0

    def test_put_option_sell_stt_nonzero(self, nifty_put_option):
        calc = ChargesCalculator()
        trade = _make_trade(nifty_put_option, OrderSide.SELL, 25, Decimal("100"))
        result = calc.calculate(trade)
        assert result.stt > 0

    def test_exchange_fee_nonzero_equity(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        result = calc.calculate(trade)
        assert result.exchange_txn_fee > 0

    def test_exchange_fee_nonzero_futures(self, nifty_future):
        calc = ChargesCalculator()
        trade = _make_trade(nifty_future, OrderSide.BUY, 25, Decimal("24000"))
        result = calc.calculate(trade)
        assert result.exchange_txn_fee > 0

    def test_exchange_fee_nonzero_options(self, nifty_call_option):
        calc = ChargesCalculator()
        trade = _make_trade(nifty_call_option, OrderSide.BUY, 25, Decimal("150"))
        result = calc.calculate(trade)
        assert result.exchange_txn_fee > 0

    def test_gst_calculated_on_brokerage_and_exchange_fee(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        result = calc.calculate(trade)
        expected_gst = (result.brokerage + result.exchange_txn_fee) * Decimal("0.18")
        assert abs(result.gst - expected_gst.quantize(Decimal("0.01"))) <= Decimal("0.01")

    def test_stamp_duty_buy_side_only_equity(self, reliance_stock):
        calc = ChargesCalculator()
        buy_trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        sell_trade = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2500"))
        buy_result = calc.calculate(buy_trade)
        sell_result = calc.calculate(sell_trade)
        assert buy_result.stamp_duty > 0
        assert sell_result.stamp_duty == Decimal("0")

    def test_stamp_duty_buy_side_only_options(self, nifty_call_option):
        calc = ChargesCalculator()
        buy_trade = _make_trade(nifty_call_option, OrderSide.BUY, 25, Decimal("150"))
        sell_trade = _make_trade(nifty_call_option, OrderSide.SELL, 25, Decimal("150"))
        buy_result = calc.calculate(buy_trade)
        sell_result = calc.calculate(sell_trade)
        assert buy_result.stamp_duty > 0
        assert sell_result.stamp_duty == Decimal("0")

    def test_total_equals_sum_of_components(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        result = calc.calculate(trade)
        expected_total = (
            result.brokerage
            + result.stt
            + result.exchange_txn_fee
            + result.gst
            + result.sebi_fee
            + result.stamp_duty
        )
        # Allow ±0.02 for rounding differences in Decimal arithmetic
        assert abs(result.total - expected_total) <= Decimal("0.02")

    def test_running_total_accumulates(self, reliance_stock):
        calc = ChargesCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        t2 = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        r1 = calc.calculate(t1)
        r2 = calc.calculate(t2)
        assert calc.get_total_charges() == round(float(r1.total) + float(r2.total), 2)

    def test_strategy_charges_tracking(self, reliance_stock):
        calc = ChargesCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"), strategy_id="s1")
        t2 = _make_trade(reliance_stock, OrderSide.BUY, 50, Decimal("2500"), strategy_id="s2")
        r1 = calc.calculate(t1)
        r2 = calc.calculate(t2)
        assert calc.get_strategy_charges("s1") == round(float(r1.total), 2)
        assert calc.get_strategy_charges("s2") == round(float(r2.total), 2)

    def test_breakdown_keys(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.calculate(trade)
        breakdown = calc.get_breakdown()
        expected_keys = {"stt", "exchange_txn", "gst", "sebi_fee", "stamp_duty", "brokerage"}
        assert set(breakdown.keys()) == expected_keys

    def test_trade_count_increments(self, reliance_stock):
        calc = ChargesCalculator()
        assert calc.get_trade_count() == 0
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.calculate(trade)
        assert calc.get_trade_count() == 1
        calc.calculate(trade)
        assert calc.get_trade_count() == 2

    def test_day_totals_reset(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.calculate(trade)
        assert calc.get_day_total_charges() > 0
        calc.reset_day()
        assert calc.get_day_total_charges() == 0.0
        # Cumulative should still be non-zero
        assert calc.get_total_charges() > 0

    def test_day_breakdown_reset(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.calculate(trade)
        calc.reset_day()
        day_bd = calc.get_day_breakdown()
        assert all(v == 0.0 for v in day_bd.values())

    def test_no_strategy_returns_unattributed(self, reliance_stock):
        calc = ChargesCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"), strategy_id=None)
        result = calc.calculate(trade)
        assert calc.get_strategy_charges("__unattributed__") == round(float(result.total), 2)


# =====================================================================
# TradeBook Tests
# =====================================================================


class TestTradeBook:
    """Tests for TradeBook trade recording and querying."""

    def test_record_trade_returns_trade_record(self, reliance_stock):
        book = TradeBook()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        record = book.record_trade(trade, charges=50.0, net_pnl=0.0)
        assert isinstance(record, TradeRecord)

    def test_total_trades_increments(self, reliance_stock):
        book = TradeBook()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        assert book.total_trades == 0
        book.record_trade(trade)
        assert book.total_trades == 1

    def test_running_pnl_accumulates(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        t2 = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        book.record_trade(t1, net_pnl=0.0)
        book.record_trade(t2, net_pnl=500.0)
        assert book.total_pnl == 500.0

    def test_running_pnl_on_record(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        t2 = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        r1 = book.record_trade(t1, net_pnl=100.0)
        r2 = book.record_trade(t2, net_pnl=200.0)
        assert r1.running_pnl == 100.0
        assert r2.running_pnl == 300.0

    def test_winning_losing_breakeven_counts(self, reliance_stock):
        book = TradeBook()
        t_win = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        t_lose = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2400"))
        t_even = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        book.record_trade(t_win, net_pnl=500.0)
        book.record_trade(t_lose, net_pnl=-200.0)
        book.record_trade(t_even, net_pnl=0.0)
        summary = book.get_summary()
        assert summary["winning_trades"] == 1
        assert summary["losing_trades"] == 1
        assert summary["breakeven_trades"] == 1

    def test_get_trades_all(self, reliance_stock):
        book = TradeBook()
        for i in range(5):
            t = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"), trade_id=f"T{i}")
            book.record_trade(t)
        trades = book.get_trades()
        assert len(trades) == 5

    def test_get_trades_newest_first(self, reliance_stock):
        book = TradeBook()
        ts_old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ts_new = datetime(2026, 3, 1, tzinfo=timezone.utc)
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"), trade_id="OLD", timestamp=ts_old)
        t2 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2600"), trade_id="NEW", timestamp=ts_new)
        book.record_trade(t1)
        book.record_trade(t2)
        trades = book.get_trades()
        assert trades[0].trade.trade_id == "NEW"
        assert trades[1].trade.trade_id == "OLD"

    def test_get_trades_filter_by_strategy(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"), strategy_id="s1")
        t2 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"), strategy_id="s2")
        book.record_trade(t1)
        book.record_trade(t2)
        trades = book.get_trades(strategy_id="s1")
        assert len(trades) == 1
        assert trades[0].trade.strategy_id == "s1"

    def test_get_trades_filter_by_symbol(self, reliance_stock, nifty_future):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"))
        t2 = _make_trade(nifty_future, OrderSide.BUY, 25, Decimal("24000"))
        book.record_trade(t1)
        book.record_trade(t2)
        trades = book.get_trades(symbol="RELIANCE")
        assert len(trades) == 1
        assert trades[0].trade.instrument.symbol == "RELIANCE"

    def test_get_trades_filter_by_since(self, reliance_stock):
        book = TradeBook()
        ts_old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ts_new = datetime(2026, 3, 1, tzinfo=timezone.utc)
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"), trade_id="OLD", timestamp=ts_old)
        t2 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2600"), trade_id="NEW", timestamp=ts_new)
        book.record_trade(t1)
        book.record_trade(t2)
        cutoff = datetime(2026, 2, 1, tzinfo=timezone.utc)
        trades = book.get_trades(since=cutoff)
        assert len(trades) == 1
        assert trades[0].trade.trade_id == "NEW"

    def test_get_trades_limit(self, reliance_stock):
        book = TradeBook()
        for i in range(10):
            t = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"), trade_id=f"T{i}")
            book.record_trade(t)
        trades = book.get_trades(limit=3)
        assert len(trades) == 3

    def test_strategies_property(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"), strategy_id="s1")
        t2 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"), strategy_id="s2")
        book.record_trade(t1)
        book.record_trade(t2)
        assert set(book.strategies) == {"s1", "s2"}

    def test_symbols_property(self, reliance_stock, nifty_future):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 10, Decimal("2500"))
        t2 = _make_trade(nifty_future, OrderSide.BUY, 25, Decimal("24000"))
        book.record_trade(t1)
        book.record_trade(t2)
        assert set(book.symbols) == {"RELIANCE", "NIFTY-FUT"}

    def test_get_summary_empty_book(self):
        book = TradeBook()
        summary = book.get_summary()
        assert summary["total_trades"] == 0
        assert summary["total_pnl"] == 0.0
        assert summary["win_rate"] == 0.0

    def test_get_summary_with_trades(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        t2 = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        book.record_trade(t1, charges=50.0, net_pnl=1000.0)
        book.record_trade(t2, charges=50.0, net_pnl=-200.0)
        summary = book.get_summary()
        assert summary["total_trades"] == 2
        assert summary["total_pnl"] == 800.0
        assert summary["winning_trades"] == 1
        assert summary["losing_trades"] == 1

    def test_get_strategy_summary(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"), strategy_id="s1")
        book.record_trade(t1, net_pnl=500.0)
        summary = book.get_strategy_summary("s1")
        assert summary["total_trades"] == 1
        assert summary["total_pnl"] == 500.0

    def test_get_symbol_summary(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        book.record_trade(t1, net_pnl=300.0)
        summary = book.get_symbol_summary("RELIANCE")
        assert summary["total_trades"] == 1

    def test_profit_factor_calculation(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        t2 = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        book.record_trade(t1, net_pnl=1000.0)
        book.record_trade(t2, net_pnl=-500.0)
        summary = book.get_summary()
        assert summary["profit_factor"] == round(1000.0 / 500.0, 4)

    def test_profit_factor_no_losses(self, reliance_stock):
        book = TradeBook()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        book.record_trade(t1, net_pnl=1000.0)
        summary = book.get_summary()
        assert summary["profit_factor"] == float("inf")


# =====================================================================
# PnLCalculator Tests
# =====================================================================


class TestPnLCalculator:
    """Tests for PnLCalculator including realised P&L, unrealised P&L, and MTM."""

    def test_on_trade_creates_position(self, reliance_stock):
        calc = PnLCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        entry = calc.on_trade(trade)
        assert isinstance(entry, PnLEntry)
        assert entry.symbol == "RELIANCE"
        assert entry.quantity == 100

    def test_buy_trade_long_position(self, reliance_stock):
        calc = PnLCalculator()
        trade = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        entry = calc.on_trade(trade)
        assert entry.side == "BUY"
        assert entry._signed_qty == 100

    def test_sell_trade_short_position(self, reliance_stock):
        calc = PnLCalculator()
        trade = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2500"))
        entry = calc.on_trade(trade)
        assert entry.side == "SELL"
        assert entry._signed_qty == -100
        assert entry.quantity == 100

    def test_realised_pnl_on_close_long(self, reliance_stock):
        calc = PnLCalculator()
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(buy)
        sell = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        entry = calc.on_trade(sell)
        # Realised P&L = (2600 - 2500) * 100 = 10000
        assert entry.realised_pnl == 10000.0
        assert entry.quantity == 0  # flat

    def test_realised_pnl_on_close_short(self, reliance_stock):
        calc = PnLCalculator()
        sell = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        calc.on_trade(sell)
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        entry = calc.on_trade(buy)
        # Realised P&L = (2600 - 2500) * 100 = 10000
        assert entry.realised_pnl == 10000.0
        assert entry.quantity == 0

    def test_partial_close_realised_pnl(self, reliance_stock):
        calc = PnLCalculator()
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(buy)
        sell = _make_trade(reliance_stock, OrderSide.SELL, 50, Decimal("2600"))
        entry = calc.on_trade(sell)
        # Realised on partial: (2600 - 2500) * 50 = 5000
        assert entry.realised_pnl == 5000.0
        assert entry.quantity == 50
        assert entry.side == "BUY"

    def test_unrealised_pnl_with_price_update(self, reliance_stock):
        calc = PnLCalculator()
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(buy)
        calc.update_price("RELIANCE", 2600.0)
        entry = calc.get_position_pnl("RELIANCE", "strat_1")
        # Unrealised = (2600 - 2500) * 100 = 10000
        assert entry.unrealised_pnl == 10000.0

    def test_unrealised_pnl_short_position(self, reliance_stock):
        calc = PnLCalculator()
        sell = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        calc.on_trade(sell)
        calc.update_price("RELIANCE", 2500.0)
        entry = calc.get_position_pnl("RELIANCE", "strat_1")
        # Short unrealised: (2500 - 2600) * (-100) = 10000
        assert entry.unrealised_pnl == 10000.0

    def test_mtm_update_changes_unrealised(self, reliance_stock):
        calc = PnLCalculator()
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(buy)
        calc.update_price("RELIANCE", 2600.0)
        entry1 = calc.get_position_pnl("RELIANCE", "strat_1")
        assert entry1.unrealised_pnl == 10000.0
        calc.update_price("RELIANCE", 2400.0)
        entry2 = calc.get_position_pnl("RELIANCE", "strat_1")
        assert entry2.unrealised_pnl == -10000.0

    def test_total_pnl_is_sum(self, reliance_stock):
        calc = PnLCalculator()
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(buy)
        sell = _make_trade(reliance_stock, OrderSide.SELL, 50, Decimal("2600"))
        entry = calc.on_trade(sell)
        # total_pnl = realised + unrealised
        assert entry.total_pnl == round(entry.realised_pnl + entry.unrealised_pnl, 2)

    def test_net_pnl_subtracts_charges(self, reliance_stock):
        calc = PnLCalculator()
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        entry = calc.on_trade(buy)
        # net_pnl = total_pnl - charges
        assert entry.net_pnl == round(entry.total_pnl - entry.charges, 2)

    def test_get_position_pnl_returns_none_for_unknown(self):
        calc = PnLCalculator()
        assert calc.get_position_pnl("UNKNOWN", "s1") is None

    def test_get_all_positions(self, reliance_stock, nifty_future):
        calc = PnLCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        t2 = _make_trade(nifty_future, OrderSide.BUY, 25, Decimal("24000"))
        calc.on_trade(t1)
        calc.on_trade(t2)
        positions = calc.get_all_positions()
        assert len(positions) == 2

    def test_get_open_positions_excludes_flat(self, reliance_stock):
        calc = PnLCalculator()
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(buy)
        sell = _make_trade(reliance_stock, OrderSide.SELL, 100, Decimal("2600"))
        calc.on_trade(sell)
        open_positions = calc.get_open_positions()
        assert len(open_positions) == 0

    def test_strategy_pnl_aggregation(self, reliance_stock, nifty_future):
        calc = PnLCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"), strategy_id="s1")
        t2 = _make_trade(nifty_future, OrderSide.BUY, 25, Decimal("24000"), strategy_id="s1")
        calc.on_trade(t1)
        calc.on_trade(t2)
        strat_pnl = calc.get_strategy_pnl("s1")
        assert strat_pnl["strategy_id"] == "s1"
        assert strat_pnl["open_positions"] == 2
        assert len(strat_pnl["symbols"]) == 2

    def test_portfolio_pnl(self, reliance_stock):
        calc = PnLCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"), strategy_id="s1")
        calc.on_trade(t1)
        portfolio = calc.get_portfolio_pnl()
        assert "unrealised_pnl" in portfolio
        assert "realised_pnl" in portfolio
        assert "net_pnl" in portfolio
        assert portfolio["open_positions"] == 1
        assert portfolio["strategy_count"] == 1

    def test_portfolio_snapshot(self, reliance_stock):
        calc = PnLCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(t1)
        snapshot = calc.get_portfolio_snapshot()
        assert isinstance(snapshot, PnLSnapshot)
        assert snapshot.strategy_id == "__portfolio__"

    def test_take_and_get_snapshots(self, reliance_stock):
        calc = PnLCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(t1)
        s1 = calc.take_snapshot()
        s2 = calc.take_snapshot()
        snaps = calc.get_snapshots()
        assert len(snaps) == 2
        # Newest first
        assert snaps[0].timestamp >= snaps[1].timestamp

    def test_get_total_charges(self, reliance_stock):
        calc = PnLCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(t1)
        assert calc.get_total_charges() > 0

    def test_day_pnl_reset(self, reliance_stock):
        calc = PnLCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(t1)
        calc.update_price("RELIANCE", 2600.0)
        calc.reset_day()
        # After reset, day P&L should be 0 (no new changes)
        assert calc.get_day_pnl() == 0.0
        # New price change should contribute to day P&L
        calc.update_price("RELIANCE", 2700.0)
        day_pnl = calc.get_day_pnl()
        assert day_pnl != 0.0

    def test_trade_book_access(self, reliance_stock):
        calc = PnLCalculator()
        t1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(t1)
        assert calc.trade_book.total_trades == 1

    def test_charges_calculator_access(self):
        calc = PnLCalculator()
        assert isinstance(calc.charges_calculator, ChargesCalculator)

    def test_add_to_long_position_averages(self, reliance_stock):
        calc = PnLCalculator()
        buy1 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(buy1)
        buy2 = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2600"))
        entry = calc.on_trade(buy2)
        assert entry.quantity == 200
        assert entry.avg_entry_price == pytest.approx(2550.0, rel=1e-2)

    def test_position_flip_long_to_short(self, reliance_stock):
        calc = PnLCalculator()
        buy = _make_trade(reliance_stock, OrderSide.BUY, 100, Decimal("2500"))
        calc.on_trade(buy)
        sell = _make_trade(reliance_stock, OrderSide.SELL, 150, Decimal("2600"))
        entry = calc.on_trade(sell)
        assert entry.side == "SELL"
        assert entry.quantity == 50
        # Realised on the 100 closed: (2600 - 2500) * 100 = 10000
        assert entry.realised_pnl == 10000.0
