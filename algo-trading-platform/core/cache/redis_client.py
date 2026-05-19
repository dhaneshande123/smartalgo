"""
Redis connection manager for the SmartAlgo trading platform.

Wraps ``redis.asyncio`` with connection pooling, key-prefix namespacing,
orjson serialization, and pub/sub helpers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import orjson
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379
DEFAULT_DB = 0
DEFAULT_KEY_PREFIX = "smartalgo:"
DEFAULT_POOL_SIZE = 20
DEFAULT_SOCKET_TIMEOUT = 5.0
DEFAULT_CONNECT_TIMEOUT = 5.0


class RedisClient:
    """Async Redis connection manager with pooling, serialization, and pub/sub.

    Configuration is read from environment variables:

    * ``REDIS_HOST`` (default ``localhost``)
    * ``REDIS_PORT`` (default ``6379``)
    * ``REDIS_PASSWORD`` (default empty)
    * ``REDIS_DB`` (default ``0``)

    All keys are automatically prefixed with ``smartalgo:`` to avoid collisions
    with other applications sharing the same Redis instance.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        password: str | None = None,
        db: int | None = None,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        pool_size: int = DEFAULT_POOL_SIZE,
    ) -> None:
        self._host = host or os.environ.get("REDIS_HOST", DEFAULT_HOST)
        self._port = port or int(os.environ.get("REDIS_PORT", str(DEFAULT_PORT)))
        self._password = password or os.environ.get("REDIS_PASSWORD", "") or None
        self._db = db if db is not None else int(os.environ.get("REDIS_DB", str(DEFAULT_DB)))
        self._key_prefix = key_prefix
        self._pool_size = pool_size

        self._pool: aioredis.ConnectionPool | None = None
        self._client: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Create the connection pool and verify connectivity."""
        if self._client is not None:
            return

        self._pool = aioredis.ConnectionPool(
            host=self._host,
            port=self._port,
            password=self._password,
            db=self._db,
            max_connections=self._pool_size,
            socket_timeout=DEFAULT_SOCKET_TIMEOUT,
            socket_connect_timeout=DEFAULT_CONNECT_TIMEOUT,
            decode_responses=False,  # we handle bytes via orjson
        )
        self._client = aioredis.Redis(connection_pool=self._pool)

        # Verify connection
        try:
            await self._client.ping()
            logger.info(
                "Redis connected: %s:%s db=%s (pool_size=%s)",
                self._host,
                self._port,
                self._db,
                self._pool_size,
            )
        except Exception:
            logger.exception("Failed to connect to Redis at %s:%s", self._host, self._port)
            raise

    async def disconnect(self) -> None:
        """Gracefully close connections and release the pool."""
        if self._pubsub is not None:
            try:
                await self._pubsub.close()
            except Exception:
                logger.warning("Error closing pub/sub connection", exc_info=True)
            self._pubsub = None

        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.warning("Error closing Redis client", exc_info=True)
            self._client = None

        if self._pool is not None:
            try:
                await self._pool.disconnect()
            except Exception:
                logger.warning("Error disconnecting Redis pool", exc_info=True)
            self._pool = None

        logger.info("Redis disconnected")

    async def health_check(self) -> bool:
        """Return ``True`` if Redis is reachable, ``False`` otherwise."""
        try:
            if self._client is None:
                return False
            await self._client.ping()
            return True
        except Exception:
            logger.warning("Redis health check failed", exc_info=True)
            return False

    @property
    def client(self) -> aioredis.Redis:
        """Return the underlying ``redis.asyncio.Redis`` instance.

        Raises:
            RuntimeError: If :meth:`connect` has not been called.
        """
        if self._client is None:
            raise RuntimeError("RedisClient is not connected. Call connect() first.")
        return self._client

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _key(self, key: str) -> str:
        """Return the fully-qualified key with namespace prefix."""
        return f"{self._key_prefix}{key}"

    # ------------------------------------------------------------------
    # Basic operations
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        """Get a value by key, deserializing with orjson.

        Returns ``None`` when the key does not exist or Redis is unreachable.
        """
        try:
            raw: bytes | None = await self.client.get(self._key(key))
            if raw is None:
                return None
            return orjson.loads(raw)
        except Exception:
            logger.warning("Redis GET failed for key=%s", key, exc_info=True)
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
    ) -> bool:
        """Set a value, serializing with orjson.

        Args:
            key: Cache key (prefix is added automatically).
            value: Any JSON-serializable Python object.
            ttl: Time-to-live in seconds.  ``None`` means no expiry.

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        try:
            raw = orjson.dumps(value)
            if ttl is not None:
                await self.client.setex(self._key(key), ttl, raw)
            else:
                await self.client.set(self._key(key), raw)
            return True
        except Exception:
            logger.warning("Redis SET failed for key=%s", key, exc_info=True)
            return False

    async def delete(self, key: str) -> bool:
        """Delete a key. Returns ``True`` if the key existed."""
        try:
            result = await self.client.delete(self._key(key))
            return result > 0
        except Exception:
            logger.warning("Redis DELETE failed for key=%s", key, exc_info=True)
            return False

    async def exists(self, key: str) -> bool:
        """Check whether a key exists."""
        try:
            return bool(await self.client.exists(self._key(key)))
        except Exception:
            logger.warning("Redis EXISTS failed for key=%s", key, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Sorted-set helpers (used by candle cache)
    # ------------------------------------------------------------------

    async def zadd(self, key: str, score: float, value: Any) -> bool:
        """Add a member to a sorted set with the given score."""
        try:
            raw = orjson.dumps(value)
            await self.client.zadd(self._key(key), {raw: score})
            return True
        except Exception:
            logger.warning("Redis ZADD failed for key=%s", key, exc_info=True)
            return False

    async def zrange(
        self,
        key: str,
        start: int,
        stop: int,
        reverse: bool = False,
    ) -> list[Any]:
        """Return members of a sorted set in the given index range.

        When *reverse* is ``True``, results are ordered from highest to lowest
        score (equivalent to ``ZREVRANGE``).
        """
        try:
            if reverse:
                raw_items: list[bytes] = await self.client.zrevrange(
                    self._key(key), start, stop
                )
            else:
                raw_items = await self.client.zrange(self._key(key), start, stop)
            return [orjson.loads(item) for item in raw_items]
        except Exception:
            logger.warning("Redis ZRANGE failed for key=%s", key, exc_info=True)
            return []

    async def zcard(self, key: str) -> int:
        """Return the cardinality (number of members) of a sorted set."""
        try:
            return await self.client.zcard(self._key(key))
        except Exception:
            logger.warning("Redis ZCARD failed for key=%s", key, exc_info=True)
            return 0

    async def expire(self, key: str, ttl: int) -> bool:
        """Set a TTL on an existing key."""
        try:
            return bool(await self.client.expire(self._key(key), ttl))
        except Exception:
            logger.warning("Redis EXPIRE failed for key=%s", key, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Pub / Sub
    # ------------------------------------------------------------------

    async def publish(self, channel: str, message: Any) -> int:
        """Publish a message to a channel.

        The message is serialized with orjson before publishing.

        Returns:
            Number of subscribers that received the message.
        """
        try:
            raw = orjson.dumps(message)
            return await self.client.publish(self._key(channel), raw)
        except Exception:
            logger.warning("Redis PUBLISH failed for channel=%s", channel, exc_info=True)
            return 0

    async def subscribe(
        self,
        channel: str,
        callback: Any | None = None,
    ) -> aioredis.client.PubSub:
        """Subscribe to a channel and optionally register a message handler.

        Args:
            channel: Channel name (prefix is added automatically).
            callback: An async callable ``(message: dict) -> None`` invoked for
                each received message.  If ``None``, messages must be consumed
                manually via the returned ``PubSub`` object.

        Returns:
            The ``PubSub`` instance for the subscription.
        """
        if self._pubsub is None:
            self._pubsub = self.client.pubsub()

        prefixed_channel = self._key(channel)

        if callback is not None:

            async def _wrapper(message: dict[str, Any]) -> None:
                if message["type"] == "message":
                    data = orjson.loads(message["data"])
                    await callback(data)

            await self._pubsub.subscribe(**{prefixed_channel: _wrapper})
        else:
            await self._pubsub.subscribe(prefixed_channel)

        logger.info("Subscribed to channel: %s", prefixed_channel)
        return self._pubsub

    async def start_listening(self) -> None:
        """Begin consuming pub/sub messages in the background.

        This is a blocking coroutine; run it as an ``asyncio.Task``.
        """
        if self._pubsub is None:
            raise RuntimeError("No active subscriptions. Call subscribe() first.")
        try:
            async for message in self._pubsub.listen():
                pass  # handlers registered via subscribe() are invoked automatically
        except asyncio.CancelledError:
            logger.info("Pub/sub listener cancelled")
        except Exception:
            logger.exception("Pub/sub listener error")
