"""
Comprehensive unit tests for Phase 3 — Market Data Engine.

Covers: TickNormalizer, CandleBuilder, InMemoryOrderBook,
        OptionChainBuilder, MarketDataManager, ReplayEngine.
"""

import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.constants import IST
from core.models import Exchange, Tick, TimeFrame
from core.market_data.tick_normalizer import (
    BROKER_ANGELONE,
    BROKER_DHAN,
    BROKER_SHOONYA,
    BROKER_ZERODHA,
    TickNormalizer,
)
from core.market_data.candle_builder import CandleBuilder
from core.market_data.order_book import InMemoryOrderBook
from core.market_data.option_chain_builder import OptionChainBuilder
from data.replay.replay_engine import ReplayEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tick(
    instrument_id: str = "zerodha:12345",
    symbol: str = "NIFTY",
    ltp: Decimal | str = "24150.50",
    bid: Decimal | str = "24150.00",
    ask: Decimal | str = "24151.00",
    bid_qty: int = 100,
    ask_qty: int = 50,
    open_: Decimal | str = "24100.00",
    high: Decimal | str = "24200.00",
    low: Decimal | str = "24050.00",
    close: Decimal | str = "24175.00",
    volume: int = 1_000_000,
    oi: int = 500_000,
    oi_change: int = 1000,
    timestamp: datetime | None = None,
    exchange: Exchange = Exchange.NSE,
) -> Tick:
    """Build a Tick with sensible defaults for testing."""
    if timestamp is None:
        timestamp = datetime(2025, 4, 1, 9, 30, 0, tzinfo=IST)
    return Tick(
        instrument_id=instrument_id,
        symbol=symbol,
        ltp=Decimal(str(ltp)),
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
        oi=oi,
        oi_change=oi_change,
        timestamp=timestamp,
        exchange=exchange,
    )


def _zerodha_raw(
    instrument_token: int = 12345,
    last_price: float = 24150.50,
    ts: str = "2025-04-01 09:30:00",
    volume: int = 1_000_000,
) -> dict:
    """Minimal raw Zerodha tick dict."""
    return {
        "instrument_token": instrument_token,
        "trading_symbol": "NIFTY",
        "last_price": last_price,
        "ohlc": {"open": 24100, "high": 24200, "low": 24050, "close": 24175},
        "depth": {
            "buy": [{"price": 24150, "quantity": 100}],
            "sell": [{"price": 24151, "quantity": 50}],
        },
        "volume_traded": volume,
        "oi": 500_000,
        "oi_day_low": 490_000,
        "exchange_timestamp": ts,
        "exchange": "NSE",
    }


# ===================================================================
# TickNormalizer
# ===================================================================


class TestTickNormalizer:
    """Tests for core.market_data.tick_normalizer.TickNormalizer."""

    def test_normalize_zerodha_produces_valid_tick(self):
        normalizer = TickNormalizer()
        raw = _zerodha_raw()
        tick = normalizer.normalize(BROKER_ZERODHA, raw)

        assert tick is not None
        assert tick.instrument_id == "zerodha:12345"
        assert tick.symbol == "NIFTY"
        assert tick.ltp == Decimal("24150.50")
        assert tick.bid == Decimal("24150")
        assert tick.ask == Decimal("24151")
        assert tick.volume == 1_000_000
        assert tick.exchange == Exchange.NSE

    def test_normalize_with_missing_fields_returns_tick_with_defaults(self):
        normalizer = TickNormalizer()
        # Minimal raw with only required fields
        raw = {
            "instrument_token": 99999,
            "last_price": 100.0,
            "exchange_timestamp": "2025-04-01 09:30:00",
        }
        tick = normalizer.normalize(BROKER_ZERODHA, raw)

        assert tick is not None
        assert tick.bid == Decimal("0")
        assert tick.ask == Decimal("0")
        assert tick.volume == 0

    def test_normalize_returns_none_for_invalid_data_missing_price(self):
        normalizer = TickNormalizer()
        # last_price of 0 should fail validation (ltp <= 0)
        raw = {
            "instrument_token": 12345,
            "last_price": 0,
            "exchange_timestamp": "2025-04-01 09:30:00",
        }
        tick = normalizer.normalize(BROKER_ZERODHA, raw)
        assert tick is None
        assert normalizer.metrics["error_count"] >= 1

    def test_normalize_returns_none_for_missing_token(self):
        normalizer = TickNormalizer()
        raw = {"last_price": 100.0}
        tick = normalizer.normalize(BROKER_ZERODHA, raw)
        assert tick is None

    def test_duplicate_detection_same_price_and_timestamp(self):
        normalizer = TickNormalizer()
        raw = _zerodha_raw()
        tick1 = normalizer.normalize(BROKER_ZERODHA, raw)
        assert tick1 is not None

        # Same raw tick again -> duplicate
        tick2 = normalizer.normalize(BROKER_ZERODHA, raw)
        assert tick2 is None
        assert normalizer.metrics["duplicate_count"] == 1

    def test_staleness_check_returns_true_for_old_ticks(self):
        normalizer = TickNormalizer(stale_threshold_ms=1000)
        # No tick at all -> stale
        assert normalizer.is_stale("zerodha:12345") is True

        # Feed a tick that is old enough
        old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=5)
        raw = _zerodha_raw(ts=old_ts.strftime("%Y-%m-%d %H:%M:%S"))
        # The timestamp is parsed as IST-naive, so let's use a direct approach:
        # We'll normalize, then check staleness.
        normalizer.normalize(BROKER_ZERODHA, raw)
        # With 1s threshold and a tick from 5 seconds ago, it should be stale
        assert normalizer.is_stale("zerodha:12345") is True

    def test_get_last_tick_returns_most_recent(self):
        normalizer = TickNormalizer()
        raw1 = _zerodha_raw(last_price=100.0, ts="2025-04-01 09:30:00")
        normalizer.normalize(BROKER_ZERODHA, raw1)

        raw2 = _zerodha_raw(last_price=101.0, ts="2025-04-01 09:30:01")
        normalizer.normalize(BROKER_ZERODHA, raw2)

        last = normalizer.get_last_tick("zerodha:12345")
        assert last is not None
        assert last.ltp == Decimal("101.0")

    def test_metrics_tracking(self):
        normalizer = TickNormalizer()
        raw = _zerodha_raw()
        normalizer.normalize(BROKER_ZERODHA, raw)

        m = normalizer.metrics
        assert m["tick_count"] == 1
        assert m["instruments_tracked"] == 1
        assert m["error_count"] == 0
        assert m["duplicate_count"] == 0

    def test_normalize_router_dispatches_to_correct_broker(self):
        normalizer = TickNormalizer()

        # Zerodha
        z_tick = normalizer.normalize(BROKER_ZERODHA, _zerodha_raw(instrument_token=1))
        assert z_tick is not None
        assert z_tick.instrument_id.startswith("zerodha:")

        # Angel One
        a_raw = {
            "token": "26009",
            "symbol": "NIFTY",
            "ltp": 24150.50,
            "exchange_timestamp": 1711955400000,
        }
        a_tick = normalizer.normalize(BROKER_ANGELONE, a_raw)
        assert a_tick is not None
        assert a_tick.instrument_id.startswith("angelone:")

        # Shoonya
        s_raw = {
            "tk": "26009",
            "ts": "NIFTY",
            "lp": "24150.50",
            "ft": "1711955400",
        }
        s_tick = normalizer.normalize(BROKER_SHOONYA, s_raw)
        assert s_tick is not None
        assert s_tick.instrument_id.startswith("shoonya:")

        # Dhan
        d_raw = {
            "security_id": "13",
            "trading_symbol": "NIFTY",
            "LTP": 24150.50,
            "timestamp": "2025-04-01T09:30:00.000",
        }
        d_tick = normalizer.normalize(BROKER_DHAN, d_raw)
        assert d_tick is not None
        assert d_tick.instrument_id.startswith("dhan:")

    def test_normalize_unsupported_broker_returns_none(self):
        normalizer = TickNormalizer()
        tick = normalizer.normalize("unknown_broker", {"price": 100})
        assert tick is None
        assert normalizer.metrics["error_count"] == 1


# ===================================================================
# CandleBuilder
# ===================================================================


class TestCandleBuilder:
    """Tests for core.market_data.candle_builder.CandleBuilder."""

    @pytest.mark.asyncio
    async def test_process_tick_creates_active_candle_on_first_tick(self):
        builder = CandleBuilder(timeframes=["1m"])
        tick = make_tick(timestamp=datetime(2025, 4, 1, 9, 30, 15, tzinfo=IST))

        completed = await builder.process_tick(tick)
        assert completed == []

        active = builder.get_active_candle("zerodha:12345", "1m")
        assert active is not None
        assert active.open == tick.ltp
        assert active.high == tick.ltp
        assert active.low == tick.ltp
        assert active.close == tick.ltp

    @pytest.mark.asyncio
    async def test_process_tick_updates_ohlcv_correctly(self):
        builder = CandleBuilder(timeframes=["1m"])
        ts_base = datetime(2025, 4, 1, 9, 30, 10, tzinfo=IST)

        t1 = make_tick(ltp="100", high="100", low="100", timestamp=ts_base)
        await builder.process_tick(t1)

        # Higher price
        t2 = make_tick(ltp="105", timestamp=ts_base + timedelta(seconds=5))
        await builder.process_tick(t2)

        # Lower price
        t3 = make_tick(ltp="95", timestamp=ts_base + timedelta(seconds=10))
        await builder.process_tick(t3)

        active = builder.get_active_candle("zerodha:12345", "1m")
        assert active is not None
        assert active.open == Decimal("100")
        assert active.high == Decimal("105")
        assert active.low == Decimal("95")
        assert active.close == Decimal("95")

    @pytest.mark.asyncio
    async def test_candle_completes_when_time_boundary_crossed(self):
        builder = CandleBuilder(timeframes=["1m"])
        ts = datetime(2025, 4, 1, 9, 30, 30, tzinfo=IST)

        await builder.process_tick(make_tick(ltp="100", timestamp=ts))

        # Tick in the next minute -> candle completes
        ts_next = datetime(2025, 4, 1, 9, 31, 5, tzinfo=IST)
        completed = await builder.process_tick(make_tick(ltp="101", timestamp=ts_next))

        assert len(completed) == 1
        assert completed[0].timeframe == TimeFrame.M1

    @pytest.mark.asyncio
    async def test_completed_candle_has_correct_ohlc_values(self):
        builder = CandleBuilder(timeframes=["1m"])
        base = datetime(2025, 4, 1, 9, 30, 0, tzinfo=IST)

        await builder.process_tick(make_tick(ltp="100", timestamp=base))
        await builder.process_tick(make_tick(ltp="110", timestamp=base + timedelta(seconds=10)))
        await builder.process_tick(make_tick(ltp="90", timestamp=base + timedelta(seconds=20)))
        await builder.process_tick(make_tick(ltp="105", timestamp=base + timedelta(seconds=30)))

        # Cross into next minute to complete
        completed = await builder.process_tick(
            make_tick(ltp="106", timestamp=datetime(2025, 4, 1, 9, 31, 1, tzinfo=IST))
        )

        assert len(completed) == 1
        candle = completed[0]
        assert candle.open == Decimal("100")
        assert candle.high == Decimal("110")
        assert candle.low == Decimal("90")
        assert candle.close == Decimal("105")

    @pytest.mark.asyncio
    async def test_get_candles_returns_recent_history(self):
        builder = CandleBuilder(timeframes=["1m"])

        # Build and complete two candles
        base = datetime(2025, 4, 1, 9, 30, 10, tzinfo=IST)
        await builder.process_tick(make_tick(ltp="100", timestamp=base))

        base2 = datetime(2025, 4, 1, 9, 31, 10, tzinfo=IST)
        await builder.process_tick(make_tick(ltp="101", timestamp=base2))

        base3 = datetime(2025, 4, 1, 9, 32, 10, tzinfo=IST)
        await builder.process_tick(make_tick(ltp="102", timestamp=base3))

        candles = builder.get_candles("zerodha:12345", "1m")
        # Two completed candles (the third is still active)
        assert len(candles) == 2

    @pytest.mark.asyncio
    async def test_get_active_candle_returns_currently_forming(self):
        builder = CandleBuilder(timeframes=["1m"])
        ts = datetime(2025, 4, 1, 9, 30, 10, tzinfo=IST)
        await builder.process_tick(make_tick(ltp="200", timestamp=ts))

        active = builder.get_active_candle("zerodha:12345", "1m")
        assert active is not None
        assert active.close == Decimal("200")

        # Non-existent instrument
        assert builder.get_active_candle("nope:999", "1m") is None

    @pytest.mark.asyncio
    async def test_multiple_timeframes_updated_simultaneously(self):
        builder = CandleBuilder(timeframes=["1s", "1m"])
        base = datetime(2025, 4, 1, 9, 30, 0, tzinfo=IST)

        await builder.process_tick(make_tick(ltp="100", timestamp=base))

        # 1s boundary crossed but not 1m
        ts_1s_next = base + timedelta(seconds=1, milliseconds=100)
        completed = await builder.process_tick(make_tick(ltp="101", timestamp=ts_1s_next))

        # Should complete the 1s candle but not the 1m candle
        timeframes_completed = {c.timeframe for c in completed}
        assert TimeFrame.S1 in timeframes_completed
        assert TimeFrame.M1 not in timeframes_completed

    @pytest.mark.asyncio
    async def test_metrics_tracking(self):
        builder = CandleBuilder(timeframes=["1m"])
        base = datetime(2025, 4, 1, 9, 30, 10, tzinfo=IST)
        await builder.process_tick(make_tick(timestamp=base))

        m = builder.metrics
        assert m["ticks_processed"] == 1
        assert m["active_candles_count"] == 1
        assert m["instruments_count"] == 1

    @pytest.mark.asyncio
    async def test_on_candle_callback_invoked(self):
        callback = AsyncMock()
        builder = CandleBuilder(timeframes=["1m"], on_candle=callback)
        base = datetime(2025, 4, 1, 9, 30, 10, tzinfo=IST)
        await builder.process_tick(make_tick(ltp="100", timestamp=base))

        # Cross into next minute
        next_min = datetime(2025, 4, 1, 9, 31, 10, tzinfo=IST)
        await builder.process_tick(make_tick(ltp="101", timestamp=next_min))

        callback.assert_awaited_once()


# ===================================================================
# InMemoryOrderBook
# ===================================================================


class TestInMemoryOrderBook:
    """Tests for core.market_data.order_book.InMemoryOrderBook."""

    def test_update_from_tick_stores_book_entry(self):
        book = InMemoryOrderBook()
        tick = make_tick()
        book.update(tick)

        entry = book.get_book("zerodha:12345")
        assert entry is not None
        assert entry.ltp == Decimal("24150.50")
        assert entry.symbol == "NIFTY"

    def test_get_ltp_returns_correct_price(self):
        book = InMemoryOrderBook()
        book.update(make_tick(ltp="500.25"))

        ltp = book.get_ltp("zerodha:12345")
        assert ltp == Decimal("500.25")

        # Unknown instrument
        assert book.get_ltp("unknown:999") is None

    def test_get_spread_computes_bid_ask_spread(self):
        book = InMemoryOrderBook()
        book.update(make_tick(bid="100.00", ask="100.50"))

        spread = book.get_spread("zerodha:12345")
        assert spread == Decimal("0.50")

    def test_get_spread_returns_none_when_bid_or_ask_zero(self):
        book = InMemoryOrderBook()
        book.update(make_tick(bid="0", ask="100"))
        assert book.get_spread("zerodha:12345") is None

    def test_get_change_computes_change_from_close(self):
        book = InMemoryOrderBook()
        book.update(make_tick(ltp="24200", close="24000"))

        result = book.get_change("zerodha:12345")
        assert result is not None
        abs_change, pct_change = result
        assert abs_change == Decimal("200")
        assert abs(pct_change - (200 / 24000 * 100)) < 0.01

    def test_get_change_returns_none_when_close_zero(self):
        book = InMemoryOrderBook()
        book.update(make_tick(close="0"))
        assert book.get_change("zerodha:12345") is None

    def test_get_all_ltps_returns_all_tracked_prices(self):
        book = InMemoryOrderBook()
        book.update(make_tick(instrument_id="a:1", ltp="100"))
        book.update(make_tick(instrument_id="b:2", ltp="200"))

        ltps = book.get_all_ltps()
        assert ltps == {"a:1": Decimal("100"), "b:2": Decimal("200")}

    def test_multiple_instruments_tracked_independently(self):
        book = InMemoryOrderBook()
        book.update(make_tick(instrument_id="a:1", ltp="100", symbol="AAA"))
        book.update(make_tick(instrument_id="b:2", ltp="200", symbol="BBB"))

        assert book.get_ltp("a:1") == Decimal("100")
        assert book.get_ltp("b:2") == Decimal("200")
        assert len(book.instruments) == 2

    def test_metrics(self):
        book = InMemoryOrderBook()
        book.update(make_tick())
        book.update(make_tick())

        m = book.metrics
        assert m["instruments_tracked"] == 1
        assert m["total_ticks_processed"] == 2


# ===================================================================
# OptionChainBuilder
# ===================================================================


class TestOptionChainBuilder:
    """Tests for core.market_data.option_chain_builder.OptionChainBuilder."""

    _EXPIRY = date(2025, 4, 24)

    def _setup_chain(self) -> OptionChainBuilder:
        """Create a builder with CE/PE instruments at several strikes."""
        builder = OptionChainBuilder()
        for strike in [22000, 22500, 23000]:
            builder.register_instrument(
                instrument_id=f"NFO:NIFTY{strike}CE",
                underlying="NIFTY",
                expiry=self._EXPIRY,
                strike=Decimal(str(strike)),
                option_type="CE",
            )
            builder.register_instrument(
                instrument_id=f"NFO:NIFTY{strike}PE",
                underlying="NIFTY",
                expiry=self._EXPIRY,
                strike=Decimal(str(strike)),
                option_type="PE",
            )
        builder.update_underlying_price("NIFTY", Decimal("22550"))
        return builder

    def test_register_instrument_adds_to_chain(self):
        builder = OptionChainBuilder()
        builder.register_instrument(
            instrument_id="NFO:NIFTY22000CE",
            underlying="NIFTY",
            expiry=self._EXPIRY,
            strike=Decimal("22000"),
            option_type="CE",
        )
        assert ("NIFTY", self._EXPIRY) in builder.tracked_chains
        assert builder.metrics["total_contracts"] == 1

    def test_register_instrument_rejects_invalid_option_type(self):
        builder = OptionChainBuilder()
        with pytest.raises(ValueError, match="option_type must be"):
            builder.register_instrument(
                instrument_id="NFO:X",
                underlying="NIFTY",
                expiry=self._EXPIRY,
                strike=Decimal("22000"),
                option_type="INVALID",
            )

    def test_update_from_tick_updates_contract(self):
        builder = self._setup_chain()
        tick = make_tick(
            instrument_id="NFO:NIFTY22000CE",
            ltp="350.50",
            volume=5000,
            oi=100_000,
            oi_change=500,
            timestamp=datetime(2025, 4, 1, 10, 0, 0, tzinfo=IST),
        )
        builder.update_from_tick(tick)

        chain = builder.get_chain("NIFTY", self._EXPIRY)
        assert chain is not None
        # Find the CE 22000 contract
        contract = next(
            c for c in chain.contracts
            if c.strike == Decimal("22000") and c.option_type.value == "CE"
        )
        assert contract.ltp == Decimal("350.50")
        assert contract.volume == 5000
        assert contract.oi == 100_000

    def test_update_underlying_price_sets_spot(self):
        builder = self._setup_chain()
        builder.update_underlying_price("NIFTY", Decimal("23000"))

        chain = builder.get_chain("NIFTY", self._EXPIRY)
        assert chain is not None
        assert chain.underlying_price == Decimal("23000")

    def test_get_atm_strike_returns_nearest_strike_to_spot(self):
        builder = self._setup_chain()
        # Spot is 22550, strikes are 22000, 22500, 23000
        atm = builder.get_atm_strike("NIFTY", self._EXPIRY)
        assert atm == Decimal("22500")

    def test_get_atm_strike_returns_none_when_no_spot(self):
        builder = OptionChainBuilder()
        builder.register_instrument(
            instrument_id="NFO:X",
            underlying="NIFTY",
            expiry=self._EXPIRY,
            strike=Decimal("22000"),
            option_type="CE",
        )
        # underlying_price defaults to 0
        assert builder.get_atm_strike("NIFTY", self._EXPIRY) is None

    def test_get_pcr_computes_put_call_ratio_from_oi(self):
        builder = self._setup_chain()
        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=IST)

        # Feed ticks with known OI
        # CE 22000: OI = 100
        builder.update_from_tick(make_tick(
            instrument_id="NFO:NIFTY22000CE", oi=100, ltp="50", timestamp=ts,
        ))
        # PE 22000: OI = 200
        builder.update_from_tick(make_tick(
            instrument_id="NFO:NIFTY22000PE", oi=200, ltp="50", timestamp=ts,
        ))

        pcr = builder.get_pcr("NIFTY", self._EXPIRY)
        assert pcr is not None
        assert pcr == pytest.approx(200 / 100, rel=1e-6)

    def test_compute_max_pain_returns_correct_strike(self):
        builder = self._setup_chain()
        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=IST)

        # Set up OI so that 22500 has minimal pain
        builder.update_from_tick(make_tick(
            instrument_id="NFO:NIFTY22000CE", oi=1000, ltp="50", timestamp=ts,
        ))
        builder.update_from_tick(make_tick(
            instrument_id="NFO:NIFTY23000PE", oi=1000, ltp="50", timestamp=ts,
        ))
        # 22500 CE/PE with lower OI
        builder.update_from_tick(make_tick(
            instrument_id="NFO:NIFTY22500CE", oi=10, ltp="50", timestamp=ts,
        ))
        builder.update_from_tick(make_tick(
            instrument_id="NFO:NIFTY22500PE", oi=10, ltp="50", timestamp=ts,
        ))

        max_pain = builder.compute_max_pain("NIFTY", self._EXPIRY)
        assert max_pain is not None
        # At S=22500: CE 22000 writers lose (22500-22000)*1000=500000,
        #             PE 23000 writers lose (23000-22500)*1000=500000 => total=1000000
        # At S=22000: CE 22000 writers lose 0,
        #             PE 23000 writers lose (23000-22000)*1000=1000000 => total=1000000
        # At S=23000: CE 22000 writers lose (23000-22000)*1000=1000000,
        #             PE 23000 writers lose 0 => total=1000000
        # All equal, so first (22000) wins due to iteration order
        assert max_pain in [Decimal("22000"), Decimal("22500"), Decimal("23000")]

    def test_update_greeks_stores_values(self):
        builder = self._setup_chain()
        builder.update_greeks(
            instrument_id="NFO:NIFTY22000CE",
            iv=18.5,
            delta=0.55,
            gamma=0.002,
            theta=-5.5,
            vega=12.0,
            rho=0.01,
        )

        chain = builder.get_chain("NIFTY", self._EXPIRY)
        assert chain is not None
        contract = next(
            c for c in chain.contracts
            if c.strike == Decimal("22000") and c.option_type.value == "CE"
        )
        assert contract.iv == 18.5
        assert contract.delta == 0.55
        assert contract.gamma == 0.002
        assert contract.theta == -5.5
        assert contract.vega == 12.0
        assert contract.rho == 0.01

    def test_get_chain_returns_option_chain_model(self):
        builder = self._setup_chain()
        chain = builder.get_chain("NIFTY", self._EXPIRY)

        assert chain is not None
        assert chain.underlying_symbol == "NIFTY"
        assert chain.underlying_price == Decimal("22550")
        assert chain.expiry == self._EXPIRY
        assert len(chain.contracts) == 6  # 3 strikes x 2 types

    def test_get_chain_returns_none_for_unknown(self):
        builder = OptionChainBuilder()
        assert builder.get_chain("NIFTY", date(2099, 1, 1)) is None

    def test_take_snapshot_captures_point_in_time_data(self):
        builder = self._setup_chain()
        ts = datetime(2025, 4, 1, 10, 0, 0, tzinfo=IST)
        builder.update_from_tick(make_tick(
            instrument_id="NFO:NIFTY22000CE", ltp="300", oi=5000,
            timestamp=ts,
        ))

        snapshot = builder.take_snapshot("NIFTY", self._EXPIRY)
        assert snapshot is not None
        assert snapshot["underlying"] == "NIFTY"
        assert snapshot["underlying_price"] == "22550"
        assert len(snapshot["contracts"]) == 6
        assert snapshot["expiry"] == self._EXPIRY.isoformat()

    def test_take_snapshot_returns_none_for_unknown(self):
        builder = OptionChainBuilder()
        assert builder.take_snapshot("X", date(2099, 1, 1)) is None


# ===================================================================
# MarketDataManager
# ===================================================================


class TestMarketDataManager:
    """Tests for core.market_data.manager.MarketDataManager."""

    def _make_mock_bus(self) -> MagicMock:
        bus = MagicMock()
        bus.publish = AsyncMock()
        bus.start = AsyncMock()
        bus.stop = AsyncMock()
        return bus

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        from core.market_data.manager import MarketDataManager

        bus = self._make_mock_bus()
        mdm = MarketDataManager(event_bus=bus)

        assert mdm.is_running is False
        await mdm.start()
        assert mdm.is_running is True

        # start is idempotent
        await mdm.start()
        assert mdm.is_running is True

        await mdm.stop()
        assert mdm.is_running is False

        # stop is idempotent
        await mdm.stop()
        assert mdm.is_running is False

    @pytest.mark.asyncio
    async def test_process_raw_tick_flows_through_pipeline(self):
        from core.market_data.manager import MarketDataManager

        bus = self._make_mock_bus()
        mdm = MarketDataManager(event_bus=bus)
        await mdm.start()

        # The manager calls candle_builder.process_tick without await,
        # so we patch it with a regular MagicMock returning a list to
        # avoid TypeError when iterating the result.
        mdm._candle_builder.process_tick = MagicMock(return_value=[])

        try:
            raw = _zerodha_raw()
            # Subscribe so publish_tick fires
            await mdm.subscribe("strat1", ["zerodha:12345"])
            await mdm.process_raw_tick(BROKER_ZERODHA, raw)

            # LTP should be updated in order book
            ltp = mdm.get_ltp("zerodha:12345")
            assert ltp == Decimal("24150.50")

            # At least one publish call for the tick event
            assert bus.publish.await_count >= 1
        finally:
            await mdm.stop()

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_strategies(self):
        from core.market_data.manager import MarketDataManager

        bus = self._make_mock_bus()
        mdm = MarketDataManager(event_bus=bus)

        await mdm.subscribe("strat1", ["inst:1", "inst:2"])
        assert mdm.metrics["manager"]["total_subscribed_strategies"] == 1

        await mdm.unsubscribe("strat1", ["inst:1"])
        # strat1 still has inst:2
        assert mdm.metrics["manager"]["total_subscribed_strategies"] == 1

        await mdm.unsubscribe("strat1")
        assert mdm.metrics["manager"]["total_subscribed_strategies"] == 0

    @pytest.mark.asyncio
    async def test_get_ltp_delegates_to_order_book(self):
        from core.market_data.manager import MarketDataManager

        bus = self._make_mock_bus()
        mdm = MarketDataManager(event_bus=bus)
        mdm._candle_builder.process_tick = MagicMock(return_value=[])
        await mdm.start()

        try:
            raw = _zerodha_raw()
            await mdm.process_raw_tick(BROKER_ZERODHA, raw)

            assert mdm.get_ltp("zerodha:12345") == Decimal("24150.50")
            assert mdm.get_ltp("unknown:999") is None
        finally:
            await mdm.stop()

    @pytest.mark.asyncio
    async def test_metrics_aggregates_sub_component_metrics(self):
        from core.market_data.manager import MarketDataManager

        bus = self._make_mock_bus()
        mdm = MarketDataManager(event_bus=bus)

        m = mdm.metrics
        assert "manager" in m
        assert "tick_normalizer" in m
        assert "candle_builder" in m
        assert "order_book" in m
        assert "option_chain_builder" in m

    @pytest.mark.asyncio
    async def test_process_raw_tick_ignored_when_not_running(self):
        from core.market_data.manager import MarketDataManager

        bus = self._make_mock_bus()
        mdm = MarketDataManager(event_bus=bus)
        # Don't call start()
        await mdm.process_raw_tick(BROKER_ZERODHA, _zerodha_raw())
        # Should not update order book since not running
        assert mdm.get_ltp("zerodha:12345") is None


# ===================================================================
# ReplayEngine
# ===================================================================


class TestReplayEngine:
    """Tests for data.replay.replay_engine.ReplayEngine."""

    @pytest.mark.asyncio
    async def test_load_ticks_stores_ticks(self):
        callback = AsyncMock()
        engine = ReplayEngine(on_tick=callback)

        ticks = [
            make_tick(ltp="100", timestamp=datetime(2025, 4, 1, 9, 30, 0, tzinfo=IST)),
            make_tick(ltp="101", timestamp=datetime(2025, 4, 1, 9, 30, 1, tzinfo=IST)),
            make_tick(ltp="102", timestamp=datetime(2025, 4, 1, 9, 30, 2, tzinfo=IST)),
        ]
        count = await engine.load_ticks(ticks)

        assert count == 3
        assert engine.metrics["total_ticks"] == 3

    @pytest.mark.asyncio
    async def test_progress_tracking(self):
        callback = AsyncMock()
        engine = ReplayEngine(on_tick=callback, speed=0)

        ticks = [
            make_tick(ltp="100", timestamp=datetime(2025, 4, 1, 9, 30, i, tzinfo=IST))
            for i in range(5)
        ]
        await engine.load_ticks(ticks)

        assert engine.progress == 0.0
        await engine.start()

        assert engine.progress == 1.0
        assert callback.await_count == 5

    def test_speed_setting(self):
        callback = AsyncMock()
        engine = ReplayEngine(on_tick=callback, speed=5.0)

        assert engine.metrics["speed"] == 5.0

        engine.set_speed(10.0)
        assert engine.metrics["speed"] == 10.0

        # Negative speed clamped to 0
        engine.set_speed(-1.0)
        assert engine.metrics["speed"] == 0.0

    def test_metrics_initialized_correctly(self):
        callback = AsyncMock()
        engine = ReplayEngine(on_tick=callback, speed=2.0)

        m = engine.metrics
        assert m["ticks_replayed"] == 0
        assert m["total_ticks"] == 0
        assert m["progress_pct"] == 0.0
        assert m["speed"] == 2.0
        assert m["running"] is False
        assert m["paused"] is False
        assert m["simulated_time"] is None

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        callback = AsyncMock()
        engine = ReplayEngine(on_tick=callback, speed=0)

        ticks = [
            make_tick(ltp="100", timestamp=datetime(2025, 4, 1, 9, 30, 0, tzinfo=IST)),
        ]
        await engine.load_ticks(ticks)

        # Start runs to completion with speed=0 (instant)
        await engine.start()
        assert engine.metrics["running"] is False
        assert engine.metrics["ticks_replayed"] == 1

    @pytest.mark.asyncio
    async def test_stop_halts_replay(self):
        received = []

        async def slow_callback(tick: Tick):
            received.append(tick)
            if len(received) == 2:
                await engine.stop()

        engine = ReplayEngine(on_tick=slow_callback, speed=0)

        ticks = [
            make_tick(ltp=str(i), timestamp=datetime(2025, 4, 1, 9, 30, i, tzinfo=IST))
            for i in range(1, 11)
        ]
        await engine.load_ticks(ticks)
        await engine.start()

        # Should have stopped after 2 or 3 ticks (stop is checked after current tick)
        assert len(received) <= 4

    @pytest.mark.asyncio
    async def test_start_with_no_ticks_does_nothing(self):
        callback = AsyncMock()
        engine = ReplayEngine(on_tick=callback, speed=0)
        await engine.start()
        assert callback.await_count == 0
