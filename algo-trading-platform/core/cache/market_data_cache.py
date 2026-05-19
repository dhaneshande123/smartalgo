"""
Market data caching layer for the SmartAlgo trading platform.

Provides fast async access to ticks, option chains, candles, and greeks
backed by Redis with appropriate TTLs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import orjson

from core.cache.redis_client import RedisClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL defaults (seconds)
# ---------------------------------------------------------------------------

TICK_TTL = 10
OPTION_CHAIN_TTL = 60
GREEKS_TTL = 30
CANDLE_SET_TTL = 3600  # 1 hour for sorted-set candle windows
MAX_CANDLES_PER_SET = 500  # cap sorted-set cardinality


class MarketDataCache:
    """High-performance market-data cache backed by Redis.

    All values are serialized with orjson for minimal latency.  Each method
    gracefully returns ``None`` / empty when Redis is unavailable so that
    the platform can continue operating in a degraded mode.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    # ------------------------------------------------------------------
    # Key builders
    # ------------------------------------------------------------------

    @staticmethod
    def _tick_key(symbol: str) -> str:
        return f"market:tick:{symbol.upper()}"

    @staticmethod
    def _option_chain_key(symbol: str) -> str:
        return f"market:optchain:{symbol.upper()}"

    @staticmethod
    def _candle_key(symbol: str, timeframe: str) -> str:
        return f"market:candle:{symbol.upper()}:{timeframe}"

    @staticmethod
    def _greeks_key(symbol: str, strike: str, expiry: str) -> str:
        return f"market:greeks:{symbol.upper()}:{strike}:{expiry}"

    # ------------------------------------------------------------------
    # Tick caching
    # ------------------------------------------------------------------

    async def cache_tick(
        self,
        symbol: str,
        price: float,
        volume: int,
        timestamp: datetime | float,
    ) -> bool:
        """Cache the latest tick for *symbol*.

        Args:
            symbol: Trading symbol (e.g. ``"NIFTY"``).
            price: Last traded price.
            volume: Cumulative volume at the time of the tick.
            timestamp: Tick timestamp as a ``datetime`` or Unix epoch float.

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        ts_value: float
        if isinstance(timestamp, datetime):
            ts_value = timestamp.timestamp()
        else:
            ts_value = float(timestamp)

        tick_data = {
            "symbol": symbol.upper(),
            "price": price,
            "volume": volume,
            "timestamp": ts_value,
            "cached_at": time.time(),
        }
        return await self._redis.set(self._tick_key(symbol), tick_data, ttl=TICK_TTL)

    async def get_tick(self, symbol: str) -> dict[str, Any] | None:
        """Return the latest cached tick for *symbol*, or ``None``."""
        return await self._redis.get(self._tick_key(symbol))

    # ------------------------------------------------------------------
    # Option chain caching
    # ------------------------------------------------------------------

    async def cache_option_chain(
        self,
        symbol: str,
        chain_data: dict[str, Any] | list[dict[str, Any]],
    ) -> bool:
        """Cache a full option chain snapshot for *symbol*.

        The *chain_data* is expected to be either a serialized
        :class:`~core.models.OptionChain` dict or a list of contract dicts.
        A 60-second TTL is applied automatically.

        Returns:
            ``True`` on success.
        """
        payload = {
            "symbol": symbol.upper(),
            "data": chain_data,
            "cached_at": time.time(),
        }
        return await self._redis.set(
            self._option_chain_key(symbol),
            payload,
            ttl=OPTION_CHAIN_TTL,
        )

    async def get_option_chain(
        self,
        symbol: str,
    ) -> dict[str, Any] | None:
        """Return the cached option chain for *symbol*, or ``None``.

        If the cached entry exists, only the inner ``data`` payload is
        returned (the wrapper metadata is stripped).
        """
        result = await self._redis.get(self._option_chain_key(symbol))
        if result is None:
            return None
        return result.get("data") if isinstance(result, dict) else result

    # ------------------------------------------------------------------
    # Candle caching (sorted sets)
    # ------------------------------------------------------------------

    async def cache_candle(
        self,
        symbol: str,
        timeframe: str,
        candle: dict[str, Any],
    ) -> bool:
        """Append a candle to the sorted set for *symbol* / *timeframe*.

        The candle's ``timestamp`` field (Unix epoch) is used as the sort
        score so that candles are naturally ordered chronologically.

        The sorted set is capped at :data:`MAX_CANDLES_PER_SET` entries and
        given a rolling :data:`CANDLE_SET_TTL` to prevent unbounded growth.

        Returns:
            ``True`` on success.
        """
        # Derive score from the candle's timestamp
        ts = candle.get("timestamp", time.time())
        if isinstance(ts, datetime):
            score = ts.timestamp()
        elif isinstance(ts, str):
            try:
                score = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                score = time.time()
        else:
            score = float(ts)

        key = self._candle_key(symbol, timeframe)
        success = await self._redis.zadd(key, score, candle)
        if not success:
            return False

        # Trim excess entries (keep the most recent MAX_CANDLES_PER_SET)
        try:
            count = await self._redis.zcard(key)
            if count > MAX_CANDLES_PER_SET:
                excess = count - MAX_CANDLES_PER_SET
                # Remove the *oldest* entries (lowest scores)
                await self._redis.client.zremrangebyrank(
                    self._redis._key(key), 0, excess - 1
                )
        except Exception:
            logger.warning(
                "Failed to trim candle set for %s:%s", symbol, timeframe, exc_info=True
            )

        # Refresh TTL
        await self._redis.expire(key, CANDLE_SET_TTL)
        return True

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 100,
    ) -> list[dict[str, Any]]:
        """Return the most recent *count* candles for *symbol* / *timeframe*.

        Candles are returned in chronological order (oldest first).
        """
        key = self._candle_key(symbol, timeframe)
        # Fetch from the *end* of the sorted set (highest scores = newest)
        items = await self._redis.zrange(key, 0, count - 1, reverse=True)
        # Reverse so the caller gets oldest-first ordering
        items.reverse()
        return items

    # ------------------------------------------------------------------
    # Greeks caching
    # ------------------------------------------------------------------

    async def cache_greeks(
        self,
        symbol: str,
        strike: str | float,
        expiry: str,
        greeks_data: dict[str, Any],
    ) -> bool:
        """Cache computed greeks for a specific option contract.

        Args:
            symbol: Underlying symbol.
            strike: Strike price (converted to string for key building).
            expiry: Expiry date string (e.g. ``"2026-04-03"``).
            greeks_data: Dict with greek values (delta, gamma, theta, ...).

        Returns:
            ``True`` on success.
        """
        payload = {
            "symbol": symbol.upper(),
            "strike": str(strike),
            "expiry": expiry,
            "greeks": greeks_data,
            "cached_at": time.time(),
        }
        return await self._redis.set(
            self._greeks_key(symbol, str(strike), expiry),
            payload,
            ttl=GREEKS_TTL,
        )

    async def get_greeks(
        self,
        symbol: str,
        strike: str | float,
        expiry: str,
    ) -> dict[str, Any] | None:
        """Return cached greeks for a specific contract, or ``None``.

        Only the inner ``greeks`` dict is returned when the entry exists.
        """
        result = await self._redis.get(
            self._greeks_key(symbol, str(strike), expiry)
        )
        if result is None:
            return None
        return result.get("greeks") if isinstance(result, dict) else result

    # ------------------------------------------------------------------
    # Bulk helpers
    # ------------------------------------------------------------------

    async def invalidate_symbol(self, symbol: str) -> None:
        """Remove all cached market data for *symbol*.

        Deletes the tick and option-chain keys.  Candle and greeks keys
        are left to expire naturally (their key patterns are not easily
        enumerable without ``SCAN``).
        """
        await self._redis.delete(self._tick_key(symbol))
        await self._redis.delete(self._option_chain_key(symbol))
        logger.info("Invalidated market data cache for %s", symbol.upper())
