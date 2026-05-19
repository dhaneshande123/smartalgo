"""
Async token-bucket rate limiter for broker API calls.

Brokers enforce strict rate limits (e.g., Zerodha: 3 req/sec, Angel One:
5 req/sec).  This module provides an asyncio-native limiter that prevents
the platform from exceeding those limits, avoiding HTTP 429 responses and
potential IP bans.

Algorithm
---------
A classic *token bucket*: the bucket starts full (up to ``burst`` tokens)
and refills at ``rate`` tokens per second.  Each API call consumes one (or
more) tokens.  If the bucket is empty the caller is suspended until enough
tokens have accumulated.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class AsyncRateLimiter:
    """Token bucket rate limiter for broker API calls.

    Brokers have strict rate limits (e.g., Zerodha: 3 req/sec, Angel One:
    5 req/sec).  This ensures we never exceed them.

    The limiter is safe to share across concurrent coroutines -- all
    state mutations are protected by an ``asyncio.Lock``.

    Example::

        limiter = AsyncRateLimiter(rate=3.0, burst=5)

        async def call_broker():
            await limiter.acquire()
            return await broker.get_positions()
    """

    __slots__ = (
        "_rate",
        "_burst",
        "_tokens",
        "_last_refill",
        "_lock",
        # metrics
        "total_requests",
        "total_waits",
        "total_wait_time_ms",
    )

    def __init__(self, rate: float, burst: int = 1) -> None:
        """Initialise the rate limiter.

        Args:
            rate: Maximum sustained requests per second.  Must be > 0.
            burst: Maximum burst size -- the most tokens the bucket can
                hold at once.  Must be >= 1.

        Raises:
            ValueError: If *rate* or *burst* are non-positive.
        """
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate}")
        if burst < 1:
            raise ValueError(f"burst must be >= 1, got {burst}")

        self._rate: float = rate
        self._burst: int = burst
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock: asyncio.Lock = asyncio.Lock()

        # --- Metrics ---
        self.total_requests: int = 0
        self.total_waits: int = 0
        self.total_wait_time_ms: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def acquire(self, tokens: int = 1) -> None:
        """Wait until *tokens* are available, then consume them.

        If the bucket does not have enough tokens the coroutine will
        ``asyncio.sleep`` in a loop, refilling as time passes, until the
        required tokens are available.

        Args:
            tokens: Number of tokens to consume.  Defaults to 1.

        Raises:
            ValueError: If *tokens* exceeds the burst size.
        """
        if tokens > self._burst:
            raise ValueError(
                f"Cannot acquire {tokens} tokens; burst limit is {self._burst}"
            )
        if tokens < 1:
            raise ValueError(f"tokens must be >= 1, got {tokens}")

        waited = False
        wait_start: float = 0.0

        async with self._lock:
            self._refill()

            while self._tokens < tokens:
                if not waited:
                    waited = True
                    wait_start = time.monotonic()
                    self.total_waits += 1

                deficit = tokens - self._tokens
                sleep_seconds = deficit / self._rate
                # Release the lock while sleeping so other coroutines
                # can check / acquire tokens that may become available.
                self._lock.release()
                try:
                    await asyncio.sleep(sleep_seconds)
                finally:
                    await self._lock.acquire()
                self._refill()

            self._tokens -= tokens
            self.total_requests += 1

        if waited:
            elapsed_ms = (time.monotonic() - wait_start) * 1000.0
            self.total_wait_time_ms += elapsed_ms
            logger.debug(
                "Rate limiter waited %.1f ms before acquiring %d token(s)",
                elapsed_ms,
                tokens,
            )

    async def try_acquire(self, tokens: int = 1) -> bool:
        """Try to acquire *tokens* without waiting.

        Returns ``True`` if the tokens were successfully consumed,
        ``False`` otherwise.  This never blocks.

        Args:
            tokens: Number of tokens to consume.  Defaults to 1.
        """
        if tokens < 1:
            raise ValueError(f"tokens must be >= 1, got {tokens}")

        async with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                self.total_requests += 1
                return True
            return False

    @property
    def available_tokens(self) -> float:
        """Current number of available tokens (approximate, no lock)."""
        self._refill()
        return self._tokens

    def reset(self) -> None:
        """Reset the limiter to full capacity.

        Useful when the broker session is re-established and the
        server-side rate-limit window has elapsed.
        """
        self._tokens = float(self._burst)
        self._last_refill = time.monotonic()
        logger.debug("Rate limiter reset to full capacity (%d tokens)", self._burst)

    @property
    def metrics(self) -> dict[str, float | int]:
        """Return a snapshot of rate-limiter metrics.

        Returns:
            A dict with keys ``total_requests``, ``total_waits``,
            ``total_wait_time_ms``, ``available_tokens``, ``rate``,
            and ``burst``.
        """
        return {
            "total_requests": self.total_requests,
            "total_waits": self.total_waits,
            "total_wait_time_ms": round(self.total_wait_time_ms, 2),
            "available_tokens": round(self.available_tokens, 2),
            "rate": self._rate,
            "burst": self._burst,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        """Add tokens that have accumulated since the last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"AsyncRateLimiter(rate={self._rate}, burst={self._burst}, "
            f"tokens={self._tokens:.2f})"
        )
