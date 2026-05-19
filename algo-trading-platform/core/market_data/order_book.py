"""In-memory top-of-book tracker for all subscribed instruments.

Maintains a ``BookEntry`` per instrument, updated from incoming ``Tick``
objects.  Provides O(1) lookups for LTP, bid/ask spread, volume, OI,
and change-from-previous-close.

Typical usage::

    book = InMemoryOrderBook()
    book.update(tick)           # called on every incoming tick
    ltp = book.get_ltp("NSE:NIFTY24MARFUT")
    spread = book.get_spread("NSE:NIFTY24MARFUT")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from core.models import Tick


# ---------------------------------------------------------------------------
# BookEntry dataclass
# ---------------------------------------------------------------------------


@dataclass
class BookEntry:
    """Point-in-time top-of-book state for a single instrument.

    Attributes:
        instrument_id: Unique identifier for the instrument.
        symbol: Human-readable trading symbol (e.g. ``NIFTY``, ``RELIANCE``).
        ltp: Last traded price.
        bid: Best bid price.
        ask: Best ask (offer) price.
        bid_qty: Quantity available at the best bid.
        ask_qty: Quantity available at the best ask.
        open: Today's opening price.
        high: Today's high price.
        low: Today's low price.
        close: Previous day's closing price (used for change calculation).
        volume: Cumulative traded volume for the day.
        oi: Current open interest.
        oi_change: Change in open interest from previous session.
        last_update: Timestamp of the most recent tick that updated this entry.
        tick_count: Number of ticks processed for this instrument.
    """

    instrument_id: str
    symbol: str
    ltp: Decimal
    bid: Decimal
    ask: Decimal
    bid_qty: int
    ask_qty: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal  # previous close
    volume: int
    oi: int
    oi_change: int
    last_update: datetime
    tick_count: int = 0


# ---------------------------------------------------------------------------
# InMemoryOrderBook
# ---------------------------------------------------------------------------


class InMemoryOrderBook:
    """Maintains top-of-book (best bid/ask) for all subscribed instruments.

    Updated from incoming ticks.  Provides fast O(1) lookups for:

    - **LTP** (last traded price) for any instrument
    - **Bid/ask spread**
    - **Volume and OI snapshots**
    - **Change from previous close**

    Thread-safety note:
        This class is *not* thread-safe.  If ticks arrive from multiple
        threads, external synchronisation (e.g. a ``threading.Lock``) is
        required.  In the typical single-threaded async design of this
        platform, no lock is needed.

    Example::

        order_book = InMemoryOrderBook()

        # On each incoming tick from the market-data feed:
        order_book.update(tick)

        # Query current state:
        ltp = order_book.get_ltp("NSE:NIFTY24MARFUT")
        spread = order_book.get_spread("NSE:NIFTY24MARFUT")
        abs_chg, pct_chg = order_book.get_change("NSE:NIFTY24MARFUT")
    """

    def __init__(self) -> None:
        self._books: dict[str, BookEntry] = {}  # instrument_id -> BookEntry
        self._total_ticks: int = 0

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def update(self, tick: Tick) -> None:
        """Update the book from an incoming tick.

        If the instrument has not been seen before, a new ``BookEntry`` is
        created.  Otherwise the existing entry is updated in-place so that
        all references remain valid.

        Args:
            tick: A ``core.models.Tick`` instance carrying the latest
                market-data snapshot for one instrument.
        """
        self._total_ticks += 1

        existing = self._books.get(tick.instrument_id)
        if existing is None:
            self._books[tick.instrument_id] = BookEntry(
                instrument_id=tick.instrument_id,
                symbol=tick.symbol,
                ltp=tick.ltp,
                bid=tick.bid,
                ask=tick.ask,
                bid_qty=tick.bid_qty,
                ask_qty=tick.ask_qty,
                open=tick.open,
                high=tick.high,
                low=tick.low,
                close=tick.close,
                volume=tick.volume,
                oi=tick.oi,
                oi_change=tick.oi_change,
                last_update=tick.timestamp,
                tick_count=1,
            )
        else:
            existing.ltp = tick.ltp
            existing.bid = tick.bid
            existing.ask = tick.ask
            existing.bid_qty = tick.bid_qty
            existing.ask_qty = tick.ask_qty
            existing.open = tick.open
            existing.high = tick.high
            existing.low = tick.low
            existing.close = tick.close
            existing.volume = tick.volume
            existing.oi = tick.oi
            existing.oi_change = tick.oi_change
            existing.last_update = tick.timestamp
            existing.tick_count += 1

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_ltp(self, instrument_id: str) -> Decimal | None:
        """Return the last traded price for *instrument_id*, or ``None``.

        Args:
            instrument_id: Unique instrument identifier.

        Returns:
            The LTP as a ``Decimal``, or ``None`` if the instrument is
            not tracked.
        """
        entry = self._books.get(instrument_id)
        return entry.ltp if entry is not None else None

    def get_book(self, instrument_id: str) -> BookEntry | None:
        """Return the full ``BookEntry`` for *instrument_id*, or ``None``.

        Args:
            instrument_id: Unique instrument identifier.

        Returns:
            The ``BookEntry`` dataclass instance, or ``None`` if the
            instrument is not tracked.
        """
        return self._books.get(instrument_id)

    def get_spread(self, instrument_id: str) -> Decimal | None:
        """Return the bid-ask spread for *instrument_id*, or ``None``.

        The spread is computed as ``ask - bid``.  If either bid or ask is
        zero (i.e. not yet populated), ``None`` is returned.

        Args:
            instrument_id: Unique instrument identifier.

        Returns:
            The spread as a ``Decimal``, or ``None`` if the instrument is
            not tracked or bid/ask data is unavailable.
        """
        entry = self._books.get(instrument_id)
        if entry is None:
            return None
        if entry.bid == Decimal("0") or entry.ask == Decimal("0"):
            return None
        return entry.ask - entry.bid

    def get_all_ltps(self) -> dict[str, Decimal]:
        """Return a dictionary mapping every tracked instrument to its LTP.

        Returns:
            A ``dict`` of ``{instrument_id: ltp}`` for all instruments
            currently in the book.
        """
        return {iid: entry.ltp for iid, entry in self._books.items()}

    def get_change(self, instrument_id: str) -> tuple[Decimal, float] | None:
        """Return (absolute_change, pct_change) from previous close.

        The percentage change is expressed as a regular float (e.g. 1.5
        means +1.5 %).  If the previous close is zero or the instrument
        is not tracked, ``None`` is returned.

        Args:
            instrument_id: Unique instrument identifier.

        Returns:
            A tuple ``(absolute_change, percentage_change)`` or ``None``.
        """
        entry = self._books.get(instrument_id)
        if entry is None:
            return None
        if entry.close == Decimal("0"):
            return None
        abs_change = entry.ltp - entry.close
        pct_change = float(abs_change / entry.close * 100)
        return abs_change, pct_change

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def instruments(self) -> list[str]:
        """List of tracked instrument IDs.

        Returns:
            A list of all instrument identifiers currently in the book.
        """
        return list(self._books.keys())

    @property
    def metrics(self) -> dict:
        """Operational metrics for monitoring and diagnostics.

        Returns:
            A dictionary containing:

            - ``instruments_tracked``: number of unique instruments.
            - ``total_ticks_processed``: cumulative tick count across all
              instruments.
            - ``per_instrument_ticks``: mapping of instrument_id to its
              individual tick count.
        """
        return {
            "instruments_tracked": len(self._books),
            "total_ticks_processed": self._total_ticks,
            "per_instrument_ticks": {
                iid: entry.tick_count for iid, entry in self._books.items()
            },
        }
