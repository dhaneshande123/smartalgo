"""
Tick Normalizer — converts raw broker tick data into the unified ``Tick`` model.

Each Indian broker (Zerodha, Angel One, Shoonya/Finvasia, Dhan) delivers
real-time WebSocket ticks in a proprietary JSON format.  This module provides
a single entry point, :pymethod:`TickNormalizer.normalize`, that routes the raw
dict to the correct broker-specific parser and returns a validated
:pyclass:`core.models.Tick` instance (or ``None`` if the tick is invalid,
stale, or a duplicate).

Thread-safety: the normalizer is designed for single-threaded asyncio use.
All mutable state (_last_ticks, counters) is accessed from one event-loop
thread.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from core.constants import IST
from core.models import Exchange, Tick

logger = logging.getLogger(__name__)

# Broker name constants used for routing
BROKER_ZERODHA = "zerodha"
BROKER_ANGELONE = "angelone"
BROKER_SHOONYA = "shoonya"
BROKER_DHAN = "dhan"

# Default staleness threshold in milliseconds
_DEFAULT_STALE_THRESHOLD_MS: float = 5000.0

# Timestamp formats commonly used by Indian brokers
_TIMESTAMP_FORMATS: list[str] = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
]


def _parse_timestamp(value: Any) -> datetime:
    """Parse a timestamp from various string formats or pass through datetime objects.

    If the parsed datetime is naive (no tzinfo), IST is assumed.

    Args:
        value: A ``datetime`` instance, a numeric Unix epoch (int/float), or a
            date-time string in one of the common Indian broker formats.

    Returns:
        A timezone-aware ``datetime``.

    Raises:
        ValueError: If *value* cannot be parsed.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=IST)
        return value

    if isinstance(value, (int, float)):
        # Treat as Unix timestamp (seconds).  Values > 1e12 are milliseconds.
        epoch = float(value)
        if epoch > 1e12:
            epoch /= 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc)

    if isinstance(value, str):
        # Try numeric string first (Unix timestamp as string)
        try:
            epoch = float(value)
            if epoch > 1e12:
                epoch /= 1000.0
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass

        for fmt in _TIMESTAMP_FORMATS:
            try:
                dt = datetime.strptime(value, fmt)
                # Assume IST for naive parsed strings
                return dt.replace(tzinfo=IST)
            except ValueError:
                continue
        raise ValueError(f"Unable to parse timestamp string: {value!r}")

    raise ValueError(f"Unsupported timestamp type: {type(value)}")


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Safely convert *value* to ``Decimal``, returning *default* on failure."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    """Safely convert *value* to ``int``, returning *default* on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class TickNormalizer:
    """Normalizes raw tick data from different brokers into a unified Tick schema.

    Each broker sends ticks in different formats.  This normalizer:

    1. Maps broker-specific fields to the unified :class:`core.models.Tick` model.
    2. Validates tick data (price > 0, timestamp present, etc.).
    3. Filters out stale / duplicate ticks.
    4. Tracks per-instrument last tick for staleness detection.
    5. Provides metrics for monitoring (tick count, error count, duplicate count).

    Usage::

        normalizer = TickNormalizer(stale_threshold_ms=5000)
        tick = normalizer.normalize("zerodha", raw_dict)
        if tick is not None:
            await event_bus.publish(Topics.TICKS, "TICK", {"tick": tick.model_dump()})

    Args:
        stale_threshold_ms: Number of milliseconds after which a tick is
            considered stale.  Used by :meth:`is_stale`.
    """

    def __init__(self, stale_threshold_ms: float = _DEFAULT_STALE_THRESHOLD_MS) -> None:
        self._stale_threshold_ms = stale_threshold_ms
        self._last_ticks: dict[str, Tick] = {}  # instrument_id -> last tick
        self._tick_count: int = 0
        self._error_count: int = 0
        self._duplicate_count: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_duplicate(self, tick: Tick) -> bool:
        """Return ``True`` if *tick* has the same price and timestamp as the
        last tick recorded for its instrument."""
        last = self._last_ticks.get(tick.instrument_id)
        if last is None:
            return False
        return last.ltp == tick.ltp and last.timestamp == tick.timestamp

    def _validate_tick(self, tick: Tick) -> bool:
        """Basic sanity checks on a normalized tick.

        Returns ``True`` if the tick passes validation.
        """
        if tick.ltp <= 0:
            logger.warning("Tick rejected: ltp <= 0 for %s", tick.instrument_id)
            return False
        if tick.timestamp is None:
            logger.warning("Tick rejected: missing timestamp for %s", tick.instrument_id)
            return False
        return True

    def _record_tick(self, tick: Tick) -> None:
        """Update internal bookkeeping after a valid, non-duplicate tick."""
        self._last_ticks[tick.instrument_id] = tick
        self._tick_count += 1

    # ------------------------------------------------------------------
    # Broker-specific normalizers
    # ------------------------------------------------------------------

    def normalize_zerodha(self, raw_tick: dict) -> Tick | None:
        """Normalize a Zerodha KiteTicker tick.

        Expected Zerodha format::

            {
                "instrument_token": 12345,
                "last_price": 24150.50,
                "ohlc": {"open": 24100, "high": 24200, "low": 24050, "close": 24175},
                "depth": {
                    "buy": [{"price": 24150, "quantity": 100, ...}, ...],
                    "sell": [{"price": 24151, "quantity": 50, ...}, ...]
                },
                "volume_traded": 1234567,
                "oi": 9876543,
                "oi_day_high": 10000000,
                "oi_day_low": 9500000,
                "exchange_timestamp": "2025-04-01 09:30:00",
                "tradable": true
            }

        Args:
            raw_tick: Raw tick dictionary from Zerodha KiteTicker WebSocket.

        Returns:
            A validated ``Tick`` or ``None`` if the data is invalid.
        """
        try:
            instrument_token = str(raw_tick.get("instrument_token", ""))
            if not instrument_token:
                logger.warning("Zerodha tick missing instrument_token")
                return None

            instrument_id = f"zerodha:{instrument_token}"
            symbol = raw_tick.get("trading_symbol", raw_tick.get("symbol", instrument_token))

            ohlc = raw_tick.get("ohlc", {})
            depth = raw_tick.get("depth", {})
            buy_depth = depth.get("buy", [])
            sell_depth = depth.get("sell", [])

            # Best bid/ask from depth
            bid = _to_decimal(buy_depth[0].get("price") if buy_depth else None)
            ask = _to_decimal(sell_depth[0].get("price") if sell_depth else None)
            bid_qty = _to_int(buy_depth[0].get("quantity") if buy_depth else None)
            ask_qty = _to_int(sell_depth[0].get("quantity") if sell_depth else None)

            # Parse timestamp — Zerodha provides exchange_timestamp or last_trade_time
            ts_raw = raw_tick.get("exchange_timestamp") or raw_tick.get("last_trade_time")
            if ts_raw is None:
                timestamp = datetime.now(tz=IST)
            else:
                timestamp = _parse_timestamp(ts_raw)

            # Determine exchange
            exchange_str = raw_tick.get("exchange", "NSE")
            try:
                exchange = Exchange(exchange_str)
            except ValueError:
                exchange = Exchange.NSE

            # Previous close for OI change calculation
            prev_oi = _to_int(raw_tick.get("oi_day_low", 0))
            current_oi = _to_int(raw_tick.get("oi", 0))
            oi_change = current_oi - prev_oi if prev_oi else 0

            tick = Tick(
                instrument_id=instrument_id,
                symbol=str(symbol),
                ltp=_to_decimal(raw_tick.get("last_price")),
                bid=bid,
                ask=ask,
                bid_qty=bid_qty,
                ask_qty=ask_qty,
                open=_to_decimal(ohlc.get("open")),
                high=_to_decimal(ohlc.get("high")),
                low=_to_decimal(ohlc.get("low")),
                close=_to_decimal(ohlc.get("close")),
                volume=_to_int(raw_tick.get("volume_traded", raw_tick.get("volume", 0))),
                oi=current_oi,
                oi_change=oi_change,
                timestamp=timestamp,
                exchange=exchange,
            )
            return tick

        except Exception as exc:
            logger.error("Failed to normalize Zerodha tick: %s", exc, exc_info=True)
            self._error_count += 1
            return None

    def normalize_angelone(self, raw_tick: dict) -> Tick | None:
        """Normalize an Angel One SmartAPI WebSocket tick.

        Expected Angel One format::

            {
                "token": "26009",
                "symbol": "NIFTY",
                "ltp": 24150.50,
                "open": 24100.0,
                "high": 24200.0,
                "low": 24050.0,
                "close": 24175.0,
                "best_bid_price": 24150.0,
                "best_ask_price": 24151.0,
                "best_bid_qty": 100,
                "best_ask_qty": 50,
                "total_traded_volume": 1234567,
                "open_interest": 9876543,
                "exchange_type": "nse_cm",
                "exchange_timestamp": 1711955400000
            }

        Args:
            raw_tick: Raw tick dictionary from Angel One SmartAPI WebSocket.

        Returns:
            A validated ``Tick`` or ``None`` if the data is invalid.
        """
        try:
            token = str(raw_tick.get("token", ""))
            if not token:
                logger.warning("Angel One tick missing token")
                return None

            instrument_id = f"angelone:{token}"
            symbol = raw_tick.get("symbol", raw_tick.get("trading_symbol", token))

            # Parse timestamp
            ts_raw = raw_tick.get("exchange_timestamp") or raw_tick.get("last_traded_timestamp")
            if ts_raw is None:
                timestamp = datetime.now(tz=IST)
            else:
                timestamp = _parse_timestamp(ts_raw)

            # Map exchange type string to Exchange enum
            exchange_map = {
                "nse_cm": Exchange.NSE,
                "bse_cm": Exchange.BSE,
                "nse_fo": Exchange.NFO,
                "bse_fo": Exchange.BFO,
                "mcx_fo": Exchange.MCX,
                "cde_fo": Exchange.CDS,
            }
            exchange_type = str(raw_tick.get("exchange_type", "nse_cm")).lower()
            exchange = exchange_map.get(exchange_type, Exchange.NSE)

            tick = Tick(
                instrument_id=instrument_id,
                symbol=str(symbol),
                ltp=_to_decimal(raw_tick.get("ltp")),
                bid=_to_decimal(raw_tick.get("best_bid_price", raw_tick.get("bid"))),
                ask=_to_decimal(raw_tick.get("best_ask_price", raw_tick.get("ask"))),
                bid_qty=_to_int(raw_tick.get("best_bid_qty", raw_tick.get("bid_qty"))),
                ask_qty=_to_int(raw_tick.get("best_ask_qty", raw_tick.get("ask_qty"))),
                open=_to_decimal(raw_tick.get("open")),
                high=_to_decimal(raw_tick.get("high")),
                low=_to_decimal(raw_tick.get("low")),
                close=_to_decimal(raw_tick.get("close")),
                volume=_to_int(raw_tick.get("total_traded_volume", raw_tick.get("volume", 0))),
                oi=_to_int(raw_tick.get("open_interest", raw_tick.get("oi", 0))),
                oi_change=_to_int(raw_tick.get("oi_change", 0)),
                timestamp=timestamp,
                exchange=exchange,
            )
            return tick

        except Exception as exc:
            logger.error("Failed to normalize Angel One tick: %s", exc, exc_info=True)
            self._error_count += 1
            return None

    def normalize_shoonya(self, raw_tick: dict) -> Tick | None:
        """Normalize a Shoonya / Finvasia tick.

        Expected Shoonya format::

            {
                "tk": "26009",
                "ts": "NIFTY",
                "e": "NSE",
                "lp": "24150.50",
                "pc": "0.12",
                "o": "24100.00",
                "h": "24200.00",
                "l": "24050.00",
                "c": "24175.00",
                "bp1": "24150.00",
                "sp1": "24151.00",
                "bq1": "100",
                "sq1": "50",
                "v": "1234567",
                "oi": "9876543",
                "ft": "1711955400",
                "ti": "0.05"
            }

        Args:
            raw_tick: Raw tick dictionary from Shoonya/Finvasia WebSocket.

        Returns:
            A validated ``Tick`` or ``None`` if the data is invalid.
        """
        try:
            token = str(raw_tick.get("tk", ""))
            if not token:
                logger.warning("Shoonya tick missing tk (token)")
                return None

            instrument_id = f"shoonya:{token}"
            symbol = raw_tick.get("ts", raw_tick.get("trading_symbol", token))

            # Parse timestamp — Shoonya uses epoch seconds in "ft" field
            ts_raw = raw_tick.get("ft")
            if ts_raw is None:
                timestamp = datetime.now(tz=IST)
            else:
                timestamp = _parse_timestamp(ts_raw)

            # Exchange mapping
            exchange_str = str(raw_tick.get("e", "NSE")).upper()
            try:
                exchange = Exchange(exchange_str)
            except ValueError:
                exchange = Exchange.NSE

            tick = Tick(
                instrument_id=instrument_id,
                symbol=str(symbol),
                ltp=_to_decimal(raw_tick.get("lp")),
                bid=_to_decimal(raw_tick.get("bp1")),
                ask=_to_decimal(raw_tick.get("sp1")),
                bid_qty=_to_int(raw_tick.get("bq1")),
                ask_qty=_to_int(raw_tick.get("sq1")),
                open=_to_decimal(raw_tick.get("o")),
                high=_to_decimal(raw_tick.get("h")),
                low=_to_decimal(raw_tick.get("l")),
                close=_to_decimal(raw_tick.get("c")),
                volume=_to_int(raw_tick.get("v", 0)),
                oi=_to_int(raw_tick.get("oi", 0)),
                oi_change=0,  # Shoonya does not provide OI change directly
                timestamp=timestamp,
                exchange=exchange,
            )
            return tick

        except Exception as exc:
            logger.error("Failed to normalize Shoonya tick: %s", exc, exc_info=True)
            self._error_count += 1
            return None

    def normalize_dhan(self, raw_tick: dict) -> Tick | None:
        """Normalize a Dhan HQ WebSocket tick.

        Expected Dhan format::

            {
                "security_id": "13",
                "trading_symbol": "NIFTY",
                "LTP": 24150.50,
                "open": 24100.0,
                "high": 24200.0,
                "low": 24050.0,
                "close": 24175.0,
                "bid_price": 24150.0,
                "ask_price": 24151.0,
                "bid_qty": 100,
                "ask_qty": 50,
                "volume": 1234567,
                "OI": 9876543,
                "exchange_segment": "NSE_EQ",
                "timestamp": "2025-04-01T09:30:00.000"
            }

        Args:
            raw_tick: Raw tick dictionary from Dhan HQ WebSocket.

        Returns:
            A validated ``Tick`` or ``None`` if the data is invalid.
        """
        try:
            security_id = str(raw_tick.get("security_id", ""))
            if not security_id:
                logger.warning("Dhan tick missing security_id")
                return None

            instrument_id = f"dhan:{security_id}"
            symbol = raw_tick.get("trading_symbol", raw_tick.get("symbol", security_id))

            # Parse timestamp
            ts_raw = raw_tick.get("timestamp") or raw_tick.get("exchange_timestamp")
            if ts_raw is None:
                timestamp = datetime.now(tz=IST)
            else:
                timestamp = _parse_timestamp(ts_raw)

            # Exchange segment mapping
            segment_map = {
                "NSE_EQ": Exchange.NSE,
                "BSE_EQ": Exchange.BSE,
                "NSE_FNO": Exchange.NFO,
                "BSE_FNO": Exchange.BFO,
                "MCX_COMM": Exchange.MCX,
                "NSE_CURRENCY": Exchange.CDS,
            }
            segment_str = str(raw_tick.get("exchange_segment", "NSE_EQ")).upper()
            exchange = segment_map.get(segment_str, Exchange.NSE)

            tick = Tick(
                instrument_id=instrument_id,
                symbol=str(symbol),
                ltp=_to_decimal(raw_tick.get("LTP", raw_tick.get("ltp"))),
                bid=_to_decimal(raw_tick.get("bid_price", raw_tick.get("bid"))),
                ask=_to_decimal(raw_tick.get("ask_price", raw_tick.get("ask"))),
                bid_qty=_to_int(raw_tick.get("bid_qty")),
                ask_qty=_to_int(raw_tick.get("ask_qty")),
                open=_to_decimal(raw_tick.get("open")),
                high=_to_decimal(raw_tick.get("high")),
                low=_to_decimal(raw_tick.get("low")),
                close=_to_decimal(raw_tick.get("close")),
                volume=_to_int(raw_tick.get("volume", 0)),
                oi=_to_int(raw_tick.get("OI", raw_tick.get("oi", 0))),
                oi_change=_to_int(raw_tick.get("oi_change", 0)),
                timestamp=timestamp,
                exchange=exchange,
            )
            return tick

        except Exception as exc:
            logger.error("Failed to normalize Dhan tick: %s", exc, exc_info=True)
            self._error_count += 1
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(self, broker: str, raw_tick: dict) -> Tick | None:
        """Route to the appropriate normalizer based on *broker* name.

        After broker-specific parsing, the tick is validated and checked for
        duplicates.  Only ticks that pass all checks are recorded and returned.

        Args:
            broker: One of ``"zerodha"``, ``"angelone"``, ``"shoonya"``, ``"dhan"``
                (case-insensitive).
            raw_tick: The raw tick dictionary from the broker WebSocket.

        Returns:
            A validated ``Tick`` or ``None`` if the raw data is invalid, a
            duplicate, or from an unsupported broker.
        """
        normalizer_map: dict[str, Any] = {
            BROKER_ZERODHA: self.normalize_zerodha,
            BROKER_ANGELONE: self.normalize_angelone,
            BROKER_SHOONYA: self.normalize_shoonya,
            BROKER_DHAN: self.normalize_dhan,
        }

        broker_key = broker.strip().lower()
        normalizer_fn = normalizer_map.get(broker_key)
        if normalizer_fn is None:
            logger.error("Unsupported broker: %s", broker)
            self._error_count += 1
            return None

        tick = normalizer_fn(raw_tick)
        if tick is None:
            return None

        # Validate
        if not self._validate_tick(tick):
            self._error_count += 1
            return None

        # Duplicate check
        if self._is_duplicate(tick):
            self._duplicate_count += 1
            return None

        # Record and return
        self._record_tick(tick)
        return tick

    def is_stale(self, instrument_id: str) -> bool:
        """Check if the last tick for *instrument_id* is stale.

        A tick is considered stale if no tick has been received for the
        instrument, or if the elapsed time since the last tick exceeds
        ``stale_threshold_ms``.

        Args:
            instrument_id: The unified instrument identifier
                (e.g. ``"zerodha:12345"``).

        Returns:
            ``True`` if the instrument's data is stale or missing.
        """
        last = self._last_ticks.get(instrument_id)
        if last is None:
            return True
        now = datetime.now(tz=timezone.utc)
        last_utc = last.timestamp if last.timestamp.tzinfo else last.timestamp.replace(tzinfo=IST)
        if last_utc.tzinfo != timezone.utc:
            last_utc = last_utc.astimezone(timezone.utc)
        elapsed_ms = (now - last_utc).total_seconds() * 1000.0
        return elapsed_ms > self._stale_threshold_ms

    def get_last_tick(self, instrument_id: str) -> Tick | None:
        """Return the most recent tick for *instrument_id*, or ``None``.

        Args:
            instrument_id: The unified instrument identifier.
        """
        return self._last_ticks.get(instrument_id)

    @property
    def metrics(self) -> dict:
        """Return normalizer metrics for monitoring dashboards.

        Returns:
            A dict with keys ``tick_count``, ``error_count``,
            ``duplicate_count``, and ``instruments_tracked``.
        """
        return {
            "tick_count": self._tick_count,
            "error_count": self._error_count,
            "duplicate_count": self._duplicate_count,
            "instruments_tracked": len(self._last_ticks),
        }
