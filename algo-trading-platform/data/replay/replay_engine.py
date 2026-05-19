"""
Replay Engine — replays historical market data through the live data pipeline.

Strategies do not know whether they are consuming live or replayed data;
the interface is identical.  This makes the replay engine ideal for
backtesting, strategy development, and regression testing.

Features
--------
- Load ticks from CSV files or in-memory ``Tick`` lists.
- Auto-detect CSV format (standard OHLCV *or* tick-level LTP/bid/ask).
- Configurable replay speed: real-time (1x), accelerated (2x, 10x, 100x),
  or max speed (0) for fast-forward backtesting.
- Pause / resume on the fly.
- Time-range filtering when loading from CSV.
- Progress tracking (fraction complete, simulated time, wall-clock elapsed).

Usage::

    from core.market_data.manager import MarketDataManager
    from data.replay.replay_engine import ReplayEngine

    mdm = MarketDataManager(event_bus=bus)

    async def on_tick(tick):
        await mdm.process_raw_tick("replay", {"tick": tick})

    engine = ReplayEngine(on_tick=on_tick, speed=10.0)
    loaded = await engine.load_csv("data/nifty50_daily.csv", symbol="NIFTY")
    await engine.start()
"""

from __future__ import annotations

import asyncio
import csv
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Awaitable, Callable

from core.constants import IST
from core.models import Exchange, Tick

logger = logging.getLogger(__name__)

# Timestamp formats to try when parsing CSV timestamps.
_TIMESTAMP_FORMATS: list[str] = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(value: str) -> datetime:
    """Parse a timestamp string into a timezone-aware datetime.

    Tries multiple common formats.  Naive datetimes are assumed IST.

    Args:
        value: A date or datetime string.

    Returns:
        A timezone-aware ``datetime``.

    Raises:
        ValueError: If *value* cannot be parsed by any known format.
    """
    value = value.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Unable to parse timestamp: {value!r}")


def _to_decimal(value: str, default: Decimal = Decimal("0")) -> Decimal:
    """Safely convert a string to ``Decimal``."""
    value = value.strip()
    if not value:
        return default
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return default


def _to_int(value: str, default: int = 0) -> int:
    """Safely convert a string to ``int``."""
    value = value.strip()
    if not value:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _detect_csv_format(headers: list[str]) -> str:
    """Detect CSV format from header names.

    Returns:
        ``"ohlcv"`` for standard OHLCV data, ``"tick"`` for tick-level data.
    """
    normalised = [h.strip().lower() for h in headers]

    # Tick format: has ltp or last_price column
    tick_indicators = {"ltp", "last_price", "last_traded_price"}
    if tick_indicators & set(normalised):
        return "tick"

    # OHLCV format: has open/high/low/close
    ohlcv_indicators = {"open", "high", "low", "close"}
    if ohlcv_indicators.issubset(set(normalised)):
        return "ohlcv"

    # Default to OHLCV (most common for historical CSVs)
    return "ohlcv"


def _normalise_header(header: str) -> str:
    """Normalise a CSV header to a canonical lowercase form."""
    return header.strip().lower().replace(" ", "_")


class ReplayEngine:
    """Replays historical market data through the same pipeline as live data.

    Strategies don't know if they're running on live or replayed data --
    the interface is identical.

    Args:
        on_tick: Async callback invoked for each replayed tick.  Typically
            this is ``MarketDataManager.process_raw_tick`` wrapped in a
            lambda, or a custom handler.
        speed: Replay speed multiplier.  ``1.0`` = real-time, ``10.0`` =
            10x faster, ``0`` = max speed (no sleep between ticks).
    """

    def __init__(
        self,
        on_tick: Callable[[Tick], Awaitable[None]],
        speed: float = 1.0,
    ) -> None:
        self._on_tick = on_tick
        self._speed = max(speed, 0.0)
        self._running = False
        self._paused = False
        self._ticks: list[Tick] = []
        self._ticks_replayed: int = 0
        self._total_ticks: int = 0
        self._start_time: datetime | None = None
        self._current_time: datetime | None = None  # simulated market time
        self._wall_start: float | None = None  # monotonic clock at replay start

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    async def load_csv(
        self,
        file_path: str | Path,
        symbol: str = "",
        instrument_id: str = "",
        exchange: str = "NSE",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> int:
        """Load ticks from a CSV file.

        Supports two CSV layouts:

        1. **Standard OHLCV**: ``Date, Open, High, Low, Close, Volume``
           Each row is converted to a synthetic tick with LTP = Close.
        2. **Tick format**: ``Timestamp, LTP, Bid, Ask, Volume, OI``

        The format is auto-detected from the CSV header row.

        Args:
            file_path: Path to the CSV file.
            symbol: Trading symbol to assign (e.g. ``"NIFTY"``).  Defaults
                to the file stem if empty.
            instrument_id: Explicit instrument_id.  If empty, one is
                generated from ``replay:<symbol>``.
            exchange: Exchange name (default ``"NSE"``).
            start_time: If set, only ticks at or after this time are loaded.
            end_time: If set, only ticks before this time are loaded.

        Returns:
            The number of ticks loaded.

        Raises:
            FileNotFoundError: If *file_path* does not exist.
            ValueError: If the CSV has no parseable rows.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        if not symbol:
            symbol = path.stem

        if not instrument_id:
            instrument_id = f"replay:{symbol}"

        try:
            exchange_enum = Exchange(exchange.upper())
        except ValueError:
            exchange_enum = Exchange.NSE

        loaded_ticks: list[Tick] = []

        with open(path, "r", newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            raw_headers = next(reader, None)
            if raw_headers is None:
                raise ValueError(f"CSV file is empty: {path}")

            headers = [_normalise_header(h) for h in raw_headers]
            fmt = _detect_csv_format(headers)

            # Build a column index lookup
            col = {h: i for i, h in enumerate(headers)}

            for row_num, row in enumerate(reader, start=2):
                if not row or all(c.strip() == "" for c in row):
                    continue  # skip blank rows

                try:
                    tick = self._parse_row(
                        row=row,
                        col=col,
                        fmt=fmt,
                        symbol=symbol,
                        instrument_id=instrument_id,
                        exchange=exchange_enum,
                    )
                except Exception as exc:
                    logger.debug("Skipping row %d: %s", row_num, exc)
                    continue

                if tick is None:
                    continue

                # Time-range filter
                if start_time is not None and tick.timestamp < start_time:
                    continue
                if end_time is not None and tick.timestamp >= end_time:
                    continue

                loaded_ticks.append(tick)

        # Sort by timestamp
        loaded_ticks.sort(key=lambda t: t.timestamp)

        self._ticks.extend(loaded_ticks)
        self._total_ticks = len(self._ticks)

        logger.info(
            "Loaded %d ticks from %s (format=%s, total=%d)",
            len(loaded_ticks),
            path.name,
            fmt,
            self._total_ticks,
        )
        return len(loaded_ticks)

    async def load_ticks(self, ticks: list[Tick]) -> int:
        """Load pre-constructed Tick objects.

        Ticks are sorted by timestamp after loading.

        Args:
            ticks: A list of ``Tick`` instances.

        Returns:
            The number of ticks loaded.
        """
        self._ticks.extend(ticks)
        self._ticks.sort(key=lambda t: t.timestamp)
        self._total_ticks = len(self._ticks)
        logger.info("Loaded %d pre-constructed ticks (total=%d)", len(ticks), self._total_ticks)
        return len(ticks)

    # ------------------------------------------------------------------
    # Replay control
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start replaying ticks via the ``on_tick`` callback.

        Ticks are emitted in chronological order.  The delay between
        consecutive ticks is computed from their timestamp difference,
        divided by the speed multiplier.

        This method blocks until all ticks have been replayed, the engine
        is stopped, or an error occurs.
        """
        if self._running:
            logger.warning("ReplayEngine is already running")
            return

        if not self._ticks:
            logger.warning("No ticks loaded; nothing to replay")
            return

        self._running = True
        self._paused = False
        self._ticks_replayed = 0
        self._start_time = self._ticks[0].timestamp
        self._wall_start = time.monotonic()

        logger.info(
            "Replay started: %d ticks, speed=%.1fx, range=[%s .. %s]",
            self._total_ticks,
            self._speed,
            self._ticks[0].timestamp.isoformat(),
            self._ticks[-1].timestamp.isoformat(),
        )

        try:
            prev_ts: datetime | None = None

            for tick in self._ticks:
                if not self._running:
                    break

                # Handle pause
                while self._paused and self._running:
                    await asyncio.sleep(0.05)

                if not self._running:
                    break

                # Compute inter-tick delay
                if prev_ts is not None and self._speed > 0:
                    delta = (tick.timestamp - prev_ts).total_seconds()
                    if delta > 0:
                        sleep_time = delta / self._speed
                        # Cap maximum sleep to avoid extremely long waits
                        sleep_time = min(sleep_time, 60.0)
                        if sleep_time > 0.0001:
                            await asyncio.sleep(sleep_time)

                self._current_time = tick.timestamp
                prev_ts = tick.timestamp

                try:
                    await self._on_tick(tick)
                except Exception:
                    logger.exception(
                        "on_tick callback failed for tick at %s",
                        tick.timestamp.isoformat(),
                    )

                self._ticks_replayed += 1

        finally:
            self._running = False
            elapsed = time.monotonic() - (self._wall_start or time.monotonic())
            logger.info(
                "Replay finished: %d / %d ticks in %.1f seconds",
                self._ticks_replayed,
                self._total_ticks,
                elapsed,
            )

    async def stop(self) -> None:
        """Stop the replay.

        Any in-progress ``start()`` coroutine will exit after the current
        tick is processed.
        """
        self._running = False
        self._paused = False
        logger.info("Replay stop requested")

    async def pause(self) -> None:
        """Pause the replay.  Call :meth:`resume` to continue."""
        if self._running and not self._paused:
            self._paused = True
            logger.info("Replay paused at tick %d / %d", self._ticks_replayed, self._total_ticks)

    async def resume(self) -> None:
        """Resume a paused replay."""
        if self._paused:
            self._paused = False
            logger.info("Replay resumed at tick %d / %d", self._ticks_replayed, self._total_ticks)

    def set_speed(self, speed: float) -> None:
        """Change the replay speed on the fly.

        Args:
            speed: New speed multiplier.  ``1.0`` = real-time, ``0`` = max speed.
        """
        self._speed = max(speed, 0.0)
        logger.info("Replay speed changed to %.1fx", self._speed)

    # ------------------------------------------------------------------
    # Progress & metrics
    # ------------------------------------------------------------------

    @property
    def progress(self) -> float:
        """Replay progress as a fraction (0.0 to 1.0)."""
        if self._total_ticks == 0:
            return 0.0
        return self._ticks_replayed / self._total_ticks

    @property
    def current_time(self) -> datetime | None:
        """Current simulated market time (timestamp of the last emitted tick)."""
        return self._current_time

    @property
    def metrics(self) -> dict:
        """Replay metrics for monitoring and diagnostics.

        Returns:
            A dictionary with keys: ``ticks_replayed``, ``total_ticks``,
            ``progress_pct``, ``speed``, ``elapsed_seconds``,
            ``simulated_time``, ``running``, ``paused``.
        """
        elapsed = 0.0
        if self._wall_start is not None:
            elapsed = time.monotonic() - self._wall_start

        return {
            "ticks_replayed": self._ticks_replayed,
            "total_ticks": self._total_ticks,
            "progress_pct": round(self.progress * 100, 2),
            "speed": self._speed,
            "elapsed_seconds": round(elapsed, 2),
            "simulated_time": (
                self._current_time.isoformat() if self._current_time else None
            ),
            "running": self._running,
            "paused": self._paused,
        }

    # ------------------------------------------------------------------
    # Internal CSV row parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_row(
        row: list[str],
        col: dict[str, int],
        fmt: str,
        symbol: str,
        instrument_id: str,
        exchange: Exchange,
    ) -> Tick | None:
        """Parse a single CSV row into a ``Tick``.

        Args:
            row: Raw CSV row (list of strings).
            col: Column name -> index mapping.
            fmt: CSV format (``"ohlcv"`` or ``"tick"``).
            symbol: Symbol to assign.
            instrument_id: Instrument ID to assign.
            exchange: Exchange enum to assign.

        Returns:
            A ``Tick`` or ``None`` if the row cannot be parsed.
        """

        def _get(name: str, default: str = "") -> str:
            """Look up a column value by name, trying common aliases."""
            idx = col.get(name)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return default

        # -- Timestamp --
        ts_str = (
            _get("timestamp")
            or _get("date")
            or _get("datetime")
            or _get("time")
        )
        if not ts_str:
            return None

        timestamp = _parse_timestamp(ts_str)

        if fmt == "ohlcv":
            # Standard OHLCV: use Close as LTP
            open_p = _to_decimal(_get("open"))
            high_p = _to_decimal(_get("high"))
            low_p = _to_decimal(_get("low"))
            close_p = _to_decimal(_get("close"))
            volume = _to_int(_get("volume"))
            oi = _to_int(_get("oi") or _get("open_interest"))

            if close_p <= 0:
                return None

            return Tick(
                instrument_id=instrument_id,
                symbol=symbol,
                ltp=close_p,
                bid=close_p,
                ask=close_p,
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=volume,
                oi=oi,
                timestamp=timestamp,
                exchange=exchange,
            )

        elif fmt == "tick":
            # Tick format: LTP, Bid, Ask, Volume, OI
            ltp = _to_decimal(
                _get("ltp") or _get("last_price") or _get("last_traded_price")
            )
            bid = _to_decimal(_get("bid") or _get("bid_price"))
            ask = _to_decimal(_get("ask") or _get("ask_price"))
            volume = _to_int(_get("volume"))
            oi = _to_int(_get("oi") or _get("open_interest"))

            if ltp <= 0:
                return None

            return Tick(
                instrument_id=instrument_id,
                symbol=symbol,
                ltp=ltp,
                bid=bid if bid > 0 else ltp,
                ask=ask if ask > 0 else ltp,
                volume=volume,
                oi=oi,
                timestamp=timestamp,
                exchange=exchange,
            )

        return None
