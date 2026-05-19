"""
Session and state caching layer for the SmartAlgo trading platform.

Caches positions, strategy state, risk metrics, and aggregated dashboard
snapshots in Redis for fast retrieval by the web UI and monitoring systems.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.cache.redis_client import RedisClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL defaults (seconds)
# ---------------------------------------------------------------------------

POSITION_TTL = 60
STRATEGY_STATE_TTL = 60
RISK_METRICS_TTL = 5
DASHBOARD_TTL = 10


class SessionCache:
    """Caches runtime session data (positions, strategy state, risk metrics)
    in Redis for low-latency reads by the dashboard and monitoring layer.

    All methods are async and fail gracefully when Redis is unreachable.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    # ------------------------------------------------------------------
    # Key builders
    # ------------------------------------------------------------------

    @staticmethod
    def _position_key(symbol: str, strategy_id: str) -> str:
        return f"session:position:{strategy_id}:{symbol.upper()}"

    @staticmethod
    def _positions_index_key(strategy_id: str) -> str:
        """Set key tracking all position keys for a strategy."""
        return f"session:positions_idx:{strategy_id}"

    @staticmethod
    def _strategy_state_key(strategy_id: str) -> str:
        return f"session:strategy:{strategy_id}"

    @staticmethod
    def _strategies_index_key() -> str:
        """Set key tracking all active strategy IDs."""
        return "session:strategies_idx"

    @staticmethod
    def _risk_metrics_key() -> str:
        return "session:risk_metrics"

    @staticmethod
    def _dashboard_key() -> str:
        return "session:dashboard"

    # ------------------------------------------------------------------
    # Position caching
    # ------------------------------------------------------------------

    async def cache_position(
        self,
        symbol: str,
        strategy_id: str,
        position_data: dict[str, Any],
    ) -> bool:
        """Cache position state for a symbol within a strategy.

        The position is stored under a composite key and also registered
        in an index set so that all positions for a strategy can be
        enumerated efficiently.

        Returns:
            ``True`` on success.
        """
        payload = {
            "symbol": symbol.upper(),
            "strategy_id": strategy_id,
            **position_data,
            "cached_at": time.time(),
        }
        key = self._position_key(symbol, strategy_id)
        success = await self._redis.set(key, payload, ttl=POSITION_TTL)

        # Track this key in the strategy's position index
        if success:
            try:
                idx_key = self._positions_index_key(strategy_id)
                await self._redis.client.sadd(
                    self._redis._key(idx_key), key
                )
                await self._redis.expire(idx_key, POSITION_TTL)
            except Exception:
                logger.warning(
                    "Failed to update position index for strategy=%s",
                    strategy_id,
                    exc_info=True,
                )
        return success

    async def get_position(
        self,
        symbol: str,
        strategy_id: str,
    ) -> dict[str, Any] | None:
        """Return cached position data, or ``None``."""
        return await self._redis.get(self._position_key(symbol, strategy_id))

    async def get_all_positions(
        self,
        strategy_id: str,
    ) -> list[dict[str, Any]]:
        """Return all cached positions for a strategy."""
        positions: list[dict[str, Any]] = []
        try:
            idx_key = self._positions_index_key(strategy_id)
            members: set[bytes] = await self._redis.client.smembers(
                self._redis._key(idx_key)
            )
            for raw_key in members:
                key_str = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                # Strip the global prefix to use our get() helper
                if key_str.startswith(self._redis._key_prefix):
                    key_str = key_str[len(self._redis._key_prefix) :]
                data = await self._redis.get(key_str)
                if data is not None:
                    positions.append(data)
        except Exception:
            logger.warning(
                "Failed to fetch positions for strategy=%s",
                strategy_id,
                exc_info=True,
            )
        return positions

    # ------------------------------------------------------------------
    # Strategy state caching
    # ------------------------------------------------------------------

    async def cache_strategy_state(
        self,
        strategy_id: str,
        state_data: dict[str, Any],
    ) -> bool:
        """Cache the runtime state of a strategy.

        Returns:
            ``True`` on success.
        """
        payload = {
            "strategy_id": strategy_id,
            **state_data,
            "cached_at": time.time(),
        }
        key = self._strategy_state_key(strategy_id)
        success = await self._redis.set(key, payload, ttl=STRATEGY_STATE_TTL)

        # Track strategy ID in the global index
        if success:
            try:
                idx_key = self._strategies_index_key()
                await self._redis.client.sadd(
                    self._redis._key(idx_key), strategy_id
                )
                await self._redis.expire(idx_key, STRATEGY_STATE_TTL)
            except Exception:
                logger.warning(
                    "Failed to update strategy index for %s",
                    strategy_id,
                    exc_info=True,
                )
        return success

    async def get_strategy_state(
        self,
        strategy_id: str,
    ) -> dict[str, Any] | None:
        """Return cached strategy state, or ``None``."""
        return await self._redis.get(self._strategy_state_key(strategy_id))

    async def get_all_strategy_states(self) -> list[dict[str, Any]]:
        """Return cached state for all tracked strategies."""
        states: list[dict[str, Any]] = []
        try:
            idx_key = self._strategies_index_key()
            members: set[bytes] = await self._redis.client.smembers(
                self._redis._key(idx_key)
            )
            for raw_id in members:
                sid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
                data = await self._redis.get(self._strategy_state_key(sid))
                if data is not None:
                    states.append(data)
        except Exception:
            logger.warning("Failed to fetch all strategy states", exc_info=True)
        return states

    # ------------------------------------------------------------------
    # Risk metrics caching
    # ------------------------------------------------------------------

    async def cache_risk_metrics(
        self,
        metrics_data: dict[str, Any],
    ) -> bool:
        """Cache a portfolio-level risk metrics snapshot (5-second TTL).

        Returns:
            ``True`` on success.
        """
        payload = {
            **metrics_data,
            "cached_at": time.time(),
        }
        return await self._redis.set(
            self._risk_metrics_key(),
            payload,
            ttl=RISK_METRICS_TTL,
        )

    async def get_risk_metrics(self) -> dict[str, Any] | None:
        """Return the latest cached risk metrics, or ``None``."""
        return await self._redis.get(self._risk_metrics_key())

    # ------------------------------------------------------------------
    # Dashboard snapshot
    # ------------------------------------------------------------------

    async def get_dashboard_snapshot(self) -> dict[str, Any]:
        """Build and return an aggregated dashboard snapshot from cache.

        Combines strategy states, risk metrics, and position summaries
        into a single dict suitable for the web dashboard.  If any
        component is unavailable, the corresponding key will be ``None``
        or an empty list.
        """
        # Try the pre-built snapshot first
        cached = await self._redis.get(self._dashboard_key())
        if cached is not None:
            return cached

        # Build from individual caches
        strategies = await self.get_all_strategy_states()
        risk = await self.get_risk_metrics()

        snapshot: dict[str, Any] = {
            "strategies": strategies,
            "risk_metrics": risk,
            "strategy_count": len(strategies),
            "generated_at": time.time(),
        }

        # Cache the assembled snapshot briefly
        await self._redis.set(self._dashboard_key(), snapshot, ttl=DASHBOARD_TTL)
        return snapshot

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    async def invalidate_strategy(self, strategy_id: str) -> None:
        """Clear all caches related to a strategy.

        Removes the strategy state and all its cached positions.
        """
        # Remove strategy state
        await self._redis.delete(self._strategy_state_key(strategy_id))

        # Remove all positions tracked in the index
        try:
            idx_key = self._positions_index_key(strategy_id)
            members: set[bytes] = await self._redis.client.smembers(
                self._redis._key(idx_key)
            )
            for raw_key in members:
                key_str = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                if key_str.startswith(self._redis._key_prefix):
                    key_str = key_str[len(self._redis._key_prefix) :]
                await self._redis.delete(key_str)
            await self._redis.delete(idx_key)
        except Exception:
            logger.warning(
                "Error during position invalidation for strategy=%s",
                strategy_id,
                exc_info=True,
            )

        # Remove strategy from global index
        try:
            global_idx = self._strategies_index_key()
            await self._redis.client.srem(
                self._redis._key(global_idx), strategy_id
            )
        except Exception:
            logger.warning(
                "Failed to remove strategy %s from global index",
                strategy_id,
                exc_info=True,
            )

        # Invalidate dashboard cache so next read rebuilds
        await self._redis.delete(self._dashboard_key())

        logger.info("Invalidated all caches for strategy=%s", strategy_id)
