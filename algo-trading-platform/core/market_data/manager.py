"""
Market Data Manager — central orchestrator for all market data operations.

Strategies interact with market data ONLY through this manager.  It wires
together the tick normalizer, candle builder, in-memory order book, and
option chain builder, and fans out processed data to subscribers via the
platform event bus.

Typical usage::

    from core.event_bus.bus import create_event_bus
    from core.market_data.manager import MarketDataManager

    bus = create_event_bus("memory")
    mdm = MarketDataManager(event_bus=bus, candle_timeframes=["M1", "M5"])

    await bus.start()
    await mdm.start()

    # On each raw broker tick:
    await mdm.process_raw_tick("zerodha", raw_tick_dict)

    # Query:
    ltp = mdm.get_ltp("zerodha:12345")
    candles = mdm.get_candles("zerodha:12345", "M5", count=50)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from core.event_bus.bus import BaseEventBus, EventPriority, Topics
from core.models import Candle, OptionChain, Tick

from .candle_builder import CandleBuilder
from .option_chain_builder import OptionChainBuilder
from .order_book import InMemoryOrderBook
from .tick_normalizer import TickNormalizer

logger = logging.getLogger(__name__)

# How often the feed health monitor checks for stale feeds (seconds).
_HEALTH_CHECK_INTERVAL: float = 2.0


class MarketDataManager:
    """Central manager for all market data operations.

    Orchestrates:
    - Tick subscription and feed management
    - Tick normalization from broker feeds
    - Candle building across all timeframes
    - Option chain construction and updates
    - In-memory order book maintenance
    - Data fan-out to strategies via event bus
    - Feed health monitoring and staleness detection

    Strategies interact with market data ONLY through this manager.

    Args:
        event_bus: The platform event bus used to publish tick, candle,
            and option-chain events.
        candle_timeframes: List of timeframe strings to build candles for
            (e.g. ``["M1", "M5", "M15"]``).  Defaults to M1, M5, M15.
        stale_feed_timeout: Number of seconds after which a feed is
            considered stale.  Defaults to 5.0 s.
    """

    def __init__(
        self,
        event_bus: BaseEventBus,
        candle_timeframes: list[str] | None = None,
        stale_feed_timeout: float = 5.0,
    ) -> None:
        self._event_bus = event_bus
        self._tick_normalizer = TickNormalizer(
            stale_threshold_ms=stale_feed_timeout * 1000,
        )
        self._candle_builder = CandleBuilder(timeframes=candle_timeframes)
        self._order_book = InMemoryOrderBook()
        self._option_chain_builder = OptionChainBuilder()

        # strategy_id -> set of instrument_ids the strategy is subscribed to
        self._subscriptions: dict[str, set[str]] = {}

        self._running = False
        self._feed_health_task: asyncio.Task | None = None
        self._ticks_processed: int = 0
        self._ticks_published: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the market data manager and feed health monitoring.

        Idempotent — calling ``start()`` on an already-running manager is
        a no-op.
        """
        if self._running:
            return

        self._running = True
        self._feed_health_task = asyncio.create_task(
            self._health_monitor_loop(),
            name="mdm-health-monitor",
        )
        logger.info("MarketDataManager started")

    async def stop(self) -> None:
        """Stop the market data manager and cancel health monitoring.

        Idempotent — calling ``stop()`` on an already-stopped manager is
        a no-op.
        """
        if not self._running:
            return

        self._running = False

        if self._feed_health_task is not None:
            self._feed_health_task.cancel()
            try:
                await self._feed_health_task
            except asyncio.CancelledError:
                pass
            self._feed_health_task = None

        logger.info("MarketDataManager stopped")

    # ------------------------------------------------------------------
    # Tick processing pipeline
    # ------------------------------------------------------------------

    async def process_raw_tick(self, broker: str, raw_tick: dict) -> None:
        """Process a raw tick from a broker feed.

        Pipeline::

            raw_tick  -->  normalize  -->  update order book
                                      -->  build candles
                                      -->  update option chain
                                      -->  publish tick event
                                      -->  publish candle events (if any completed)

        Args:
            broker: Broker name (e.g. ``"zerodha"``, ``"angelone"``).
            raw_tick: The raw tick dictionary from the broker WebSocket.
        """
        if not self._running:
            logger.warning("MarketDataManager is not running; ignoring tick")
            return

        # 1. Normalize
        tick = self._tick_normalizer.normalize(broker, raw_tick)
        if tick is None:
            return

        self._ticks_processed += 1

        # 2. Update order book
        self._order_book.update(tick)

        # 3. Build candles — may return completed candles
        completed_candles = self._candle_builder.process_tick(tick)

        # 4. Update option chain
        self._option_chain_builder.update_from_tick(tick)

        # 5. Publish tick event to all interested subscribers
        if self._has_subscribers(tick.instrument_id):
            await self._publish_tick(tick)

        # 6. Publish completed candle events
        for candle in completed_candles:
            await self._publish_candle(candle)

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        strategy_id: str,
        instrument_ids: list[str],
    ) -> None:
        """Subscribe a strategy to receive ticks for specific instruments.

        Args:
            strategy_id: Unique identifier for the subscribing strategy.
            instrument_ids: List of instrument identifiers to subscribe to.
        """
        if strategy_id not in self._subscriptions:
            self._subscriptions[strategy_id] = set()

        self._subscriptions[strategy_id].update(instrument_ids)
        logger.info(
            "Strategy %s subscribed to %d instruments (total: %d)",
            strategy_id,
            len(instrument_ids),
            len(self._subscriptions[strategy_id]),
        )

    async def unsubscribe(
        self,
        strategy_id: str,
        instrument_ids: list[str] | None = None,
    ) -> None:
        """Unsubscribe a strategy from instruments.

        Args:
            strategy_id: Unique identifier for the strategy.
            instrument_ids: Specific instruments to unsubscribe from.
                If ``None``, unsubscribe from all instruments.
        """
        if strategy_id not in self._subscriptions:
            return

        if instrument_ids is None:
            removed = len(self._subscriptions[strategy_id])
            del self._subscriptions[strategy_id]
            logger.info(
                "Strategy %s unsubscribed from all %d instruments",
                strategy_id,
                removed,
            )
        else:
            self._subscriptions[strategy_id].difference_update(instrument_ids)
            # Clean up if no subscriptions remain
            if not self._subscriptions[strategy_id]:
                del self._subscriptions[strategy_id]
            logger.info(
                "Strategy %s unsubscribed from %d instruments",
                strategy_id,
                len(instrument_ids),
            )

    # ------------------------------------------------------------------
    # Convenience getters (delegated to sub-components)
    # ------------------------------------------------------------------

    def get_ltp(self, instrument_id: str) -> Decimal | None:
        """Return the last traded price for *instrument_id*, or ``None``.

        Delegated to the in-memory order book.

        Args:
            instrument_id: Unique instrument identifier.
        """
        return self._order_book.get_ltp(instrument_id)

    def get_all_ltps(self) -> dict[str, Decimal]:
        """Return a mapping of all tracked instruments to their LTP.

        Delegated to the in-memory order book.
        """
        return self._order_book.get_all_ltps()

    def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        count: int = 100,
    ) -> list[Candle]:
        """Return the last *count* completed candles.

        Delegated to the candle builder.

        Args:
            instrument_id: Instrument identifier.
            timeframe: Timeframe string (e.g. ``"M1"``, ``"M5"``).
            count: Maximum number of candles to return.

        Returns:
            A list of ``Candle`` objects, oldest first.
        """
        return self._candle_builder.get_candles(instrument_id, timeframe, count)

    def get_option_chain(
        self,
        underlying: str,
        expiry: date,
    ) -> OptionChain | None:
        """Return the option chain for the given underlying and expiry.

        Delegated to the option chain builder.

        Args:
            underlying: Underlying symbol (e.g. ``"NIFTY"``).
            expiry: Contract expiry date.

        Returns:
            An ``OptionChain`` model or ``None``.
        """
        return self._option_chain_builder.get_chain(underlying, expiry)

    def get_max_pain(
        self,
        underlying: str,
        expiry: date,
    ) -> Decimal | None:
        """Return the max pain strike for the given underlying and expiry.

        Delegated to the option chain builder.

        Args:
            underlying: Underlying symbol.
            expiry: Contract expiry date.

        Returns:
            The max pain strike as a ``Decimal``, or ``None``.
        """
        return self._option_chain_builder.compute_max_pain(underlying, expiry)

    def get_pcr(
        self,
        underlying: str,
        expiry: date,
    ) -> float | None:
        """Return the OI-based put-call ratio for the given chain.

        Delegated to the option chain builder.

        Args:
            underlying: Underlying symbol.
            expiry: Contract expiry date.

        Returns:
            PCR as a float, or ``None``.
        """
        return self._option_chain_builder.get_pcr(underlying, expiry)

    # ------------------------------------------------------------------
    # Feed health monitoring
    # ------------------------------------------------------------------

    async def _health_monitor_loop(self) -> None:
        """Periodically check for stale feeds and publish alerts."""
        while self._running:
            try:
                await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
                await self._check_feed_health()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in feed health monitor")

    async def _check_feed_health(self) -> None:
        """Detect stale feeds and publish health alerts.

        Iterates over all tracked instruments in the order book and checks
        whether the tick normalizer considers each one stale.  Publishes a
        ``SYSTEM_HEALTH`` event for any stale instruments detected.
        """
        stale_instruments: list[str] = []

        for instrument_id in self._order_book.instruments:
            if self._tick_normalizer.is_stale(instrument_id):
                stale_instruments.append(instrument_id)

        if stale_instruments:
            logger.warning(
                "Stale feeds detected for %d instruments: %s",
                len(stale_instruments),
                stale_instruments[:10],  # log at most 10
            )
            await self._event_bus.publish(
                topic=Topics.SYSTEM_HEALTH,
                event_type="STALE_FEED",
                payload={
                    "stale_instruments": stale_instruments,
                    "count": len(stale_instruments),
                },
                priority=EventPriority.HIGH,
                source="MarketDataManager",
            )

    # ------------------------------------------------------------------
    # Internal publishing helpers
    # ------------------------------------------------------------------

    def _has_subscribers(self, instrument_id: str) -> bool:
        """Check if any strategy is subscribed to *instrument_id*."""
        for instruments in self._subscriptions.values():
            if instrument_id in instruments:
                return True
        return False

    async def _publish_tick(self, tick: Tick) -> None:
        """Publish a normalized tick to the event bus."""
        self._ticks_published += 1
        await self._event_bus.publish(
            topic=Topics.TICKS,
            event_type="TICK",
            payload={"tick": tick.model_dump(mode="json")},
            priority=EventPriority.NORMAL,
            source="MarketDataManager",
        )

    async def _publish_candle(self, candle: Candle) -> None:
        """Publish a completed candle to the event bus."""
        await self._event_bus.publish(
            topic=Topics.CANDLES,
            event_type="CANDLE",
            payload={"candle": candle.model_dump(mode="json")},
            priority=EventPriority.NORMAL,
            source="MarketDataManager",
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the manager is currently running."""
        return self._running

    @property
    def metrics(self) -> dict:
        """Aggregate metrics from all sub-components.

        Returns:
            A dictionary containing metrics from the tick normalizer,
            candle builder, order book, option chain builder, and the
            manager itself.
        """
        return {
            "manager": {
                "running": self._running,
                "ticks_processed": self._ticks_processed,
                "ticks_published": self._ticks_published,
                "active_subscriptions": {
                    sid: len(instruments)
                    for sid, instruments in self._subscriptions.items()
                },
                "total_subscribed_strategies": len(self._subscriptions),
            },
            "tick_normalizer": self._tick_normalizer.metrics,
            "candle_builder": self._candle_builder.metrics,
            "order_book": self._order_book.metrics,
            "option_chain_builder": self._option_chain_builder.metrics,
        }
