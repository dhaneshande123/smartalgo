"""
Candle Builder — real-time OHLCV candle aggregation from incoming ticks.

Maintains multiple timeframe candles simultaneously for each instrument.
When a tick's timestamp crosses a candle's time-period boundary, the candle
is marked *completed*, emitted via the ``on_candle`` callback, and a new
candle begins.

Supported timeframes: 1s, 5s, 1m, 5m, 15m, 30m, 1h, 1D.

Design decisions
~~~~~~~~~~~~~~~~
* **IST-aligned boundaries**: All period boundaries use Indian Standard Time
  (``Asia/Kolkata``).  The daily candle spans the NSE session 09:15 -- 15:30 IST.
* **Late ticks**: A tick that falls within an already-active candle's period
  updates that candle normally.  A tick that arrives *after* the period has
  ended triggers candle completion and a new candle.
* **Asyncio-friendly**: ``process_tick`` is an ``async`` method so the
  ``on_candle`` callback may be a coroutine (e.g. publishing to the event bus).
* **History**: Completed candles are kept in an internal ring buffer
  (default 500 per instrument per timeframe).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from core.constants import IST, MARKET_OPEN, MARKET_CLOSE
from core.models import Candle, Tick, TimeFrame

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeframe string -> TimeFrame enum mapping
# ---------------------------------------------------------------------------

_TIMEFRAME_MAP: dict[str, TimeFrame] = {
    "1s": TimeFrame.S1,
    "5s": TimeFrame.S5,
    "1m": TimeFrame.M1,
    "5m": TimeFrame.M5,
    "15m": TimeFrame.M15,
    "30m": TimeFrame.M30,
    "1h": TimeFrame.H1,
    "1D": TimeFrame.D1,
}

# Reverse map for display / logging
_TIMEFRAME_LABEL: dict[TimeFrame, str] = {v: k for k, v in _TIMEFRAME_MAP.items()}

# Timeframe durations for sub-daily candles
_TIMEFRAME_DELTA: dict[str, timedelta] = {
    "1s": timedelta(seconds=1),
    "5s": timedelta(seconds=5),
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
}

# Default timeframes to build if none specified
_DEFAULT_TIMEFRAMES: list[str] = ["1s", "5s", "1m", "5m", "15m", "1h"]


# ---------------------------------------------------------------------------
# Period boundary computation
# ---------------------------------------------------------------------------


def _floor_to_interval(dt: datetime, interval: timedelta) -> datetime:
    """Floor *dt* to the nearest interval boundary relative to midnight.

    For example, with a 5-minute interval and dt = 09:17:32, the result is
    09:15:00.

    Args:
        dt: A timezone-aware datetime (IST expected).
        interval: The candle interval duration.

    Returns:
        The floored datetime with the same tzinfo as *dt*.
    """
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = dt - midnight
    total_seconds = int(interval.total_seconds())
    elapsed_seconds = int(elapsed.total_seconds())
    floored_seconds = (elapsed_seconds // total_seconds) * total_seconds
    return midnight + timedelta(seconds=floored_seconds)


def _get_period_boundaries(ts: datetime, timeframe: str) -> tuple[datetime, datetime]:
    """Compute the (start, end) of the candle period that *ts* falls into.

    All boundaries are in IST.

    Rules:
    - ``"1m"``: Floor to the minute start, end = start + 1 minute.
    - ``"5m"``: Floor to the nearest 5-minute mark (09:15, 09:20, 09:25 ...).
    - ``"15m"``: Floor to the nearest 15-minute mark (09:15, 09:30, 09:45 ...).
    - ``"30m"``: Floor to the nearest 30-minute mark.
    - ``"1h"``: Floor to the hour start.
    - ``"1D"``: 09:15 AM to 15:30 PM IST (full NSE session).

    Args:
        ts: A timezone-aware tick timestamp.
        timeframe: One of the supported timeframe strings (``"1s"``, ``"5s"``,
            ``"1m"``, ``"5m"``, ``"15m"``, ``"30m"``, ``"1h"``, ``"1D"``).

    Returns:
        A ``(period_start, period_end)`` tuple.  ``period_end`` is the
        exclusive upper bound (the start of the *next* candle).

    Raises:
        ValueError: If *timeframe* is not supported.
    """
    # Ensure we are working in IST
    ts_ist = ts.astimezone(IST)

    if timeframe == "1D":
        # Daily candle spans full NSE session: 09:15 to 15:30 IST
        day = ts_ist.date()
        period_start = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
        period_end = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
        return period_start, period_end

    delta = _TIMEFRAME_DELTA.get(timeframe)
    if delta is None:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")

    period_start = _floor_to_interval(ts_ist, delta)
    period_end = period_start + delta
    return period_start, period_end


# ---------------------------------------------------------------------------
# Internal candle state
# ---------------------------------------------------------------------------


@dataclass
class _CandleState:
    """Internal mutable state for a candle currently being built.

    This is *not* a Pydantic model — it is a lightweight dataclass used only
    inside :class:`CandleBuilder`.  Once the candle period ends, the state is
    converted to a validated :class:`core.models.Candle`.

    Attributes:
        instrument_id: Unified instrument ID (e.g. ``"zerodha:12345"``).
        symbol: Human-readable trading symbol.
        timeframe: Timeframe string (e.g. ``"5m"``).
        open: Opening price of the candle.
        high: Highest price seen so far.
        low: Lowest price seen so far.
        close: Most recent price (becomes the closing price on completion).
        volume: Cumulative volume within the candle period.
        oi: Latest open interest value.
        num_ticks: Number of ticks aggregated into this candle.
        period_start: Start of this candle's time window (inclusive).
        period_end: End of this candle's time window (exclusive / next candle start).
    """

    instrument_id: str
    symbol: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    oi: int
    num_ticks: int
    period_start: datetime
    period_end: datetime

    def update(self, tick: Tick) -> None:
        """Update the candle state with a new tick.

        Adjusts high, low, close, volume, OI, and tick count.
        """
        price = tick.ltp
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        self.volume += tick.volume
        self.oi = tick.oi
        self.num_ticks += 1

    def to_candle(self) -> Candle:
        """Convert this state to a validated ``Candle`` model.

        The candle's ``timestamp`` is set to ``period_start`` (the beginning
        of the candle window), following the convention used by most charting
        libraries.
        """
        tf_enum = _TIMEFRAME_MAP.get(self.timeframe, TimeFrame.M1)
        return Candle(
            instrument_id=self.instrument_id,
            symbol=self.symbol,
            timeframe=tf_enum,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            oi=self.oi,
            timestamp=self.period_start,
            num_ticks=self.num_ticks,
        )


# ---------------------------------------------------------------------------
# CandleBuilder
# ---------------------------------------------------------------------------


class CandleBuilder:
    """Builds OHLCV candles in real-time from incoming ticks.

    Maintains multiple timeframe candles simultaneously for each instrument.
    When a candle completes (time boundary crossed), it is emitted via the
    optional ``on_candle`` callback and stored in history.

    Supported timeframes: ``1s``, ``5s``, ``1m``, ``5m``, ``15m``, ``30m``,
    ``1h``, ``1D``.

    Usage::

        builder = CandleBuilder(
            timeframes=["1m", "5m", "15m"],
            on_candle=my_async_handler,
        )
        completed = await builder.process_tick(tick)

    Args:
        timeframes: List of timeframe strings to build.  Defaults to
            ``["1s", "5s", "1m", "5m", "15m", "1h"]``.
        on_candle: Optional async callback invoked with each completed
            :class:`core.models.Candle`.
    """

    def __init__(
        self,
        timeframes: list[str] | None = None,
        on_candle: Callable[[Candle], Awaitable[None]] | None = None,
    ) -> None:
        self._timeframes: list[str] = timeframes or list(_DEFAULT_TIMEFRAMES)
        self._on_candle = on_candle

        # Validate requested timeframes
        for tf in self._timeframes:
            if tf not in _TIMEFRAME_MAP:
                raise ValueError(
                    f"Unsupported timeframe {tf!r}. "
                    f"Supported: {sorted(_TIMEFRAME_MAP.keys())}"
                )

        # Key: (instrument_id, timeframe) -> _CandleState
        self._active_candles: dict[tuple[str, str], _CandleState] = {}

        # "instrument_id:timeframe" -> list of completed Candle (most recent last)
        self._completed_candles: dict[str, list[Candle]] = defaultdict(list)

        self._max_history: int = 500  # candles to keep per instrument per timeframe
        self._ticks_processed: int = 0
        self._candles_completed: int = 0

        # Lock for asyncio-safe access (protects state during concurrent process_tick calls)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Tick processing
    # ------------------------------------------------------------------

    async def process_tick(self, tick: Tick) -> list[Candle]:
        """Process a tick and update all active candles for its instrument.

        For each configured timeframe:

        1. If no active candle exists, a new one is created.
        2. If the tick falls within the active candle's period, the candle is
           updated.
        3. If the tick's timestamp is at or beyond the period end, the active
           candle is completed, the ``on_candle`` callback is called, and a
           new candle starts.

        Args:
            tick: A validated :class:`core.models.Tick`.

        Returns:
            A list of :class:`core.models.Candle` instances that completed
            during this call (may be empty).
        """
        completed: list[Candle] = []

        async with self._lock:
            self._ticks_processed += 1

            for tf in self._timeframes:
                key = (tick.instrument_id, tf)
                period_start, period_end = _get_period_boundaries(tick.timestamp, tf)

                active = self._active_candles.get(key)

                if active is None:
                    # First tick for this instrument+timeframe — start a new candle
                    self._active_candles[key] = _CandleState(
                        instrument_id=tick.instrument_id,
                        symbol=tick.symbol,
                        timeframe=tf,
                        open=tick.ltp,
                        high=tick.ltp,
                        low=tick.ltp,
                        close=tick.ltp,
                        volume=tick.volume,
                        oi=tick.oi,
                        num_ticks=1,
                        period_start=period_start,
                        period_end=period_end,
                    )
                    continue

                # Check if the tick belongs to a new period
                tick_ist = tick.timestamp.astimezone(IST)
                if tick_ist >= active.period_end:
                    # Complete the current candle
                    candle = active.to_candle()
                    completed.append(candle)
                    self._candles_completed += 1

                    # Store in history
                    history_key = f"{tick.instrument_id}:{tf}"
                    self._completed_candles[history_key].append(candle)
                    # Trim history
                    if len(self._completed_candles[history_key]) > self._max_history:
                        self._completed_candles[history_key] = self._completed_candles[
                            history_key
                        ][-self._max_history :]

                    # Start a new candle for the tick's period
                    self._active_candles[key] = _CandleState(
                        instrument_id=tick.instrument_id,
                        symbol=tick.symbol,
                        timeframe=tf,
                        open=tick.ltp,
                        high=tick.ltp,
                        low=tick.ltp,
                        close=tick.ltp,
                        volume=tick.volume,
                        oi=tick.oi,
                        num_ticks=1,
                        period_start=period_start,
                        period_end=period_end,
                    )
                else:
                    # Tick is within the active candle's period — update in place
                    active.update(tick)

        # Fire callbacks outside the lock to avoid holding it during I/O
        if self._on_candle is not None:
            for candle in completed:
                try:
                    await self._on_candle(candle)
                except Exception as exc:
                    logger.error(
                        "on_candle callback failed for %s/%s: %s",
                        candle.instrument_id,
                        _TIMEFRAME_LABEL.get(candle.timeframe, candle.timeframe),
                        exc,
                    )

        return completed

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_candles(
        self, instrument_id: str, timeframe: str, count: int = 100
    ) -> list[Candle]:
        """Get recent completed candles for an instrument and timeframe.

        Args:
            instrument_id: The unified instrument identifier.
            timeframe: Timeframe string (e.g. ``"5m"``).
            count: Maximum number of candles to return.  Most recent candles
                are returned last (chronological order).

        Returns:
            A list of completed ``Candle`` instances, up to *count* items.
        """
        history_key = f"{instrument_id}:{timeframe}"
        candles = self._completed_candles.get(history_key, [])
        return candles[-count:]

    def get_active_candle(self, instrument_id: str, timeframe: str) -> Candle | None:
        """Get the currently-forming (incomplete) candle.

        The returned ``Candle`` reflects the state as of the last tick
        processed.  Its OHLCV values will continue to change as new ticks
        arrive.

        Args:
            instrument_id: The unified instrument identifier.
            timeframe: Timeframe string (e.g. ``"1m"``).

        Returns:
            A ``Candle`` snapshot of the active candle, or ``None`` if no
            candle is being built for the given instrument/timeframe pair.
        """
        key = (instrument_id, timeframe)
        state = self._active_candles.get(key)
        if state is None:
            return None
        return state.to_candle()

    def flush(self, instrument_id: str | None = None) -> list[Candle]:
        """Flush in-progress candles (e.g. at market close).

        Completes all active candles immediately, storing them in history and
        returning them.  Useful at end-of-day to capture the final
        partially-formed candles.

        Args:
            instrument_id: If provided, flush only candles for this instrument.
                Otherwise flush all active candles.

        Returns:
            A list of flushed ``Candle`` objects.
        """
        flushed: list[Candle] = []
        keys = list(self._active_candles.keys())
        for key in keys:
            if instrument_id is not None and key[0] != instrument_id:
                continue
            state = self._active_candles.pop(key)
            candle = state.to_candle()
            inst_id, tf = key
            history_key = f"{inst_id}:{tf}"
            self._completed_candles[history_key].append(candle)
            self._candles_completed += 1
            flushed.append(candle)
        return flushed

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @property
    def metrics(self) -> dict:
        """Return builder metrics for monitoring dashboards.

        Returns:
            A dict with keys ``ticks_processed``, ``candles_completed``,
            ``active_candles_count``, and ``instruments_count``.
        """
        instrument_ids = {key[0] for key in self._active_candles}
        return {
            "ticks_processed": self._ticks_processed,
            "candles_completed": self._candles_completed,
            "active_candles_count": len(self._active_candles),
            "instruments_count": len(instrument_ids),
        }
