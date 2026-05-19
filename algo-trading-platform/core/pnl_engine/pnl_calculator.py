"""
P&L Calculator — real-time mark-to-market P&L engine.

Tracks per-position, per-strategy, and portfolio-level profit and loss
including unrealised MTM, realised P&L, transaction charges, and day
vs overall accounting.  Designed for tight integration with the event bus
and order management system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.models import (
    OrderSide,
    PnLSnapshot,
    Trade,
    TransactionCharges,
)
from core.pnl_engine.charges_calculator import ChargesCalculator
from core.pnl_engine.trade_book import TradeBook

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PnLEntry dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PnLEntry:
    """P&L state for a single position (symbol + strategy combination)."""

    symbol: str
    strategy_id: str
    side: str  # "BUY" (long) or "SELL" (short)
    quantity: int  # absolute qty held; 0 when flat
    avg_entry_price: float
    current_price: float
    unrealised_pnl: float = 0.0
    realised_pnl: float = 0.0
    total_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    pnl_pct: float = 0.0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Internal bookkeeping — not part of the public interface
    _signed_qty: int = field(default=0, repr=False)
    _total_cost: float = field(default=0.0, repr=False)  # cumulative cost basis

    def recalc(self) -> None:
        """Recalculate derived fields from core state."""
        if self._signed_qty > 0:
            self.side = "BUY"
            self.quantity = self._signed_qty
        elif self._signed_qty < 0:
            self.side = "SELL"
            self.quantity = abs(self._signed_qty)
        else:
            self.quantity = 0

        if self.quantity != 0:
            self.avg_entry_price = abs(self._total_cost) / self.quantity
        # else: keep last avg_entry_price for reference

        # Unrealised P&L
        if self._signed_qty != 0:
            self.unrealised_pnl = round(
                (self.current_price - self.avg_entry_price) * self._signed_qty, 2
            )
        else:
            self.unrealised_pnl = 0.0

        self.total_pnl = round(self.realised_pnl + self.unrealised_pnl, 2)
        self.net_pnl = round(self.total_pnl - self.charges, 2)

        entry_value = abs(self.avg_entry_price * self.quantity) if self.quantity else 0.0
        self.pnl_pct = round((self.net_pnl / entry_value) * 100.0, 4) if entry_value else 0.0
        self.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# PnLCalculator
# ---------------------------------------------------------------------------


class PnLCalculator:
    """Real-time P&L calculation engine.

    Features:
        - Per-position P&L (unrealised + realised)
        - Per-strategy P&L aggregation
        - Portfolio-level P&L with charges
        - Mark-to-market using latest prices
        - Transaction charges calculation (STT, exchange, GST, stamp duty)
        - Day P&L vs overall P&L
        - P&L snapshots for historical tracking

    Usage::

        calc = PnLCalculator()
        calc.on_trade(trade)
        calc.update_price("NIFTY", 24300.0)
        snapshot = calc.get_portfolio_snapshot()
    """

    def __init__(self) -> None:
        # key = "symbol:strategy_id"
        self._positions: dict[str, PnLEntry] = {}
        # For day P&L: stores net_pnl at start of day per position key
        self._day_start_values: dict[str, float] = {}
        self._total_charges: float = 0.0
        self._snapshots: list[PnLSnapshot] = []

        # Sub-engines
        self._charges_calc = ChargesCalculator()
        self._trade_book = TradeBook()

        # Strategy tracking
        self._strategy_ids: set[str] = set()

        # Latest prices cache (symbol -> price)
        self._latest_prices: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(symbol: str, strategy_id: str) -> str:
        return f"{symbol}:{strategy_id}"

    def _get_or_create_entry(self, symbol: str, strategy_id: str, price: float) -> PnLEntry:
        key = self._make_key(symbol, strategy_id)
        entry = self._positions.get(key)
        if entry is None:
            entry = PnLEntry(
                symbol=symbol,
                strategy_id=strategy_id,
                side="BUY",
                quantity=0,
                avg_entry_price=price,
                current_price=price,
            )
            self._positions[key] = entry
        return entry

    # ------------------------------------------------------------------
    # Trade processing
    # ------------------------------------------------------------------

    def on_trade(self, trade: Trade) -> PnLEntry:
        """Process a new trade and update position P&L.

        Handles position averaging on adds and realised P&L on reduces/closes.

        Args:
            trade: The executed trade.

        Returns:
            Updated ``PnLEntry`` for the position.
        """
        symbol = trade.instrument.symbol
        strategy_id = trade.strategy_id or ""
        price = float(trade.price)
        qty = trade.quantity

        self._strategy_ids.add(strategy_id)
        self._latest_prices[symbol] = price

        entry = self._get_or_create_entry(symbol, strategy_id, price)

        # Calculate charges for this trade
        charges_obj = self._charges_calc.calculate(trade)
        trade_charges = float(charges_obj.total)
        self._total_charges += trade_charges
        entry.charges += trade_charges

        # Determine signed quantity delta
        signed_delta = qty if trade.side == OrderSide.BUY else -qty

        old_signed = entry._signed_qty
        new_signed = old_signed + signed_delta

        # Calculate realised P&L if reducing position
        realised_on_trade = 0.0
        if old_signed != 0 and _is_reducing(old_signed, signed_delta):
            # Number of units being closed
            closed_qty = min(abs(signed_delta), abs(old_signed))
            if old_signed > 0:
                # Was long, now selling
                realised_on_trade = (price - entry.avg_entry_price) * closed_qty
            else:
                # Was short, now buying
                realised_on_trade = (entry.avg_entry_price - price) * closed_qty

            entry.realised_pnl = round(entry.realised_pnl + realised_on_trade, 2)

        # Update cost basis
        if old_signed == 0:
            # Fresh position
            entry._total_cost = price * signed_delta
        elif _same_direction(old_signed, signed_delta):
            # Adding to position — average in
            entry._total_cost += price * signed_delta
        else:
            # Reducing / flipping
            closed_qty = min(abs(signed_delta), abs(old_signed))
            remaining_old = abs(old_signed) - closed_qty
            if remaining_old > 0:
                # Partial close — scale down cost basis proportionally
                entry._total_cost = (
                    entry._total_cost * remaining_old / abs(old_signed)
                )
            else:
                # Full close or flip
                overshoot = abs(signed_delta) - abs(old_signed)
                if overshoot > 0:
                    # Flip: new direction position
                    sign = 1 if signed_delta > 0 else -1
                    entry._total_cost = price * overshoot * sign
                else:
                    entry._total_cost = 0.0

        entry._signed_qty = new_signed
        entry.current_price = price
        entry.recalc()

        # Record in trade book
        net_pnl_on_trade = round(realised_on_trade - trade_charges, 2)
        self._trade_book.record_trade(
            trade=trade,
            charges=trade_charges,
            net_pnl=net_pnl_on_trade,
        )

        logger.debug(
            "Trade processed: %s %s %d @ %.2f | pos=%d avg=%.2f unreal=%.2f real=%.2f",
            trade.side.value,
            symbol,
            qty,
            price,
            entry._signed_qty,
            entry.avg_entry_price,
            entry.unrealised_pnl,
            entry.realised_pnl,
        )

        return entry

    # ------------------------------------------------------------------
    # Price updates (MTM)
    # ------------------------------------------------------------------

    def update_price(self, symbol: str, price: float) -> None:
        """Update mark-to-market price for all positions in a symbol.

        Args:
            symbol: The instrument symbol.
            price: Latest traded price.
        """
        self._latest_prices[symbol] = price

        for key, entry in self._positions.items():
            if entry.symbol == symbol and entry.quantity > 0:
                entry.current_price = price
                entry.recalc()

    # ------------------------------------------------------------------
    # Position-level queries
    # ------------------------------------------------------------------

    def get_position_pnl(
        self, symbol: str, strategy_id: str = ""
    ) -> PnLEntry | None:
        """Get P&L entry for a specific position.

        Args:
            symbol: Instrument symbol.
            strategy_id: Strategy identifier (empty string for unattributed).

        Returns:
            The ``PnLEntry`` or ``None`` if not found.
        """
        key = self._make_key(symbol, strategy_id)
        return self._positions.get(key)

    def get_all_positions(self) -> list[PnLEntry]:
        """Return all position P&L entries (including flat/closed)."""
        return list(self._positions.values())

    def get_open_positions(self) -> list[PnLEntry]:
        """Return only positions with non-zero quantity."""
        return [e for e in self._positions.values() if e.quantity > 0]

    # ------------------------------------------------------------------
    # Strategy-level aggregation
    # ------------------------------------------------------------------

    def get_strategy_pnl(self, strategy_id: str) -> dict[str, Any]:
        """Aggregate P&L across all positions for a strategy.

        Args:
            strategy_id: The strategy to aggregate.

        Returns:
            Dict with keys: strategy_id, unrealised_pnl, realised_pnl,
            total_pnl, charges, net_pnl, open_positions, symbols.
        """
        unrealised = 0.0
        realised = 0.0
        charges = 0.0
        open_count = 0
        symbols: list[str] = []

        for entry in self._positions.values():
            if entry.strategy_id != strategy_id:
                continue
            unrealised += entry.unrealised_pnl
            realised += entry.realised_pnl
            charges += entry.charges
            if entry.quantity > 0:
                open_count += 1
                symbols.append(entry.symbol)

        total = round(unrealised + realised, 2)
        net = round(total - charges, 2)

        return {
            "strategy_id": strategy_id,
            "unrealised_pnl": round(unrealised, 2),
            "realised_pnl": round(realised, 2),
            "total_pnl": total,
            "charges": round(charges, 2),
            "net_pnl": net,
            "open_positions": open_count,
            "symbols": symbols,
        }

    # ------------------------------------------------------------------
    # Portfolio-level aggregation
    # ------------------------------------------------------------------

    def get_portfolio_pnl(self) -> dict[str, Any]:
        """Calculate total portfolio-level P&L.

        Returns:
            Dict with keys: unrealised_pnl, realised_pnl, total_pnl,
            charges, net_pnl, open_positions, total_positions,
            strategy_count, day_pnl.
        """
        unrealised = 0.0
        realised = 0.0
        charges = 0.0
        open_count = 0

        for entry in self._positions.values():
            unrealised += entry.unrealised_pnl
            realised += entry.realised_pnl
            charges += entry.charges
            if entry.quantity > 0:
                open_count += 1

        total = round(unrealised + realised, 2)
        net = round(total - charges, 2)

        return {
            "unrealised_pnl": round(unrealised, 2),
            "realised_pnl": round(realised, 2),
            "total_pnl": total,
            "charges": round(charges, 2),
            "net_pnl": net,
            "open_positions": open_count,
            "total_positions": len(self._positions),
            "strategy_count": len(self._strategy_ids),
            "day_pnl": self.get_day_pnl(),
        }

    def get_portfolio_snapshot(self) -> PnLSnapshot:
        """Build a ``PnLSnapshot`` from current portfolio state.

        Uses strategy_id ``"__portfolio__"`` for the aggregate snapshot.
        """
        portfolio = self.get_portfolio_pnl()

        charges_breakdown = self._charges_calc.get_breakdown()
        charges_obj = TransactionCharges(
            brokerage=Decimal(str(charges_breakdown.get("brokerage", 0))),
            stt=Decimal(str(charges_breakdown.get("stt", 0))),
            exchange_txn_fee=Decimal(str(charges_breakdown.get("exchange_txn", 0))),
            gst=Decimal(str(charges_breakdown.get("gst", 0))),
            sebi_fee=Decimal(str(charges_breakdown.get("sebi_fee", 0))),
            stamp_duty=Decimal(str(charges_breakdown.get("stamp_duty", 0))),
            total=Decimal(str(portfolio["charges"])),
        )

        return PnLSnapshot(
            strategy_id="__portfolio__",
            timestamp=datetime.now(timezone.utc),
            realized_pnl=Decimal(str(portfolio["realised_pnl"])),
            unrealized_pnl=Decimal(str(portfolio["unrealised_pnl"])),
            total_pnl=Decimal(str(portfolio["total_pnl"])),
            charges=charges_obj,
            net_pnl=Decimal(str(portfolio["net_pnl"])),
        )

    # ------------------------------------------------------------------
    # Day P&L
    # ------------------------------------------------------------------

    def get_day_pnl(self) -> float:
        """Calculate P&L accrued since the last ``reset_day()`` call.

        Compares each position's current net P&L against the snapshot
        taken at start of day.
        """
        day_pnl = 0.0
        for key, entry in self._positions.items():
            current_net = entry.net_pnl
            start_net = self._day_start_values.get(key, 0.0)
            day_pnl += current_net - start_net
        return round(day_pnl, 2)

    # ------------------------------------------------------------------
    # Charges
    # ------------------------------------------------------------------

    def get_total_charges(self) -> float:
        """Return cumulative transaction charges across all trades."""
        return round(self._total_charges, 2)

    def _calculate_charges(self, trade: Trade) -> float:
        """Calculate transaction charges using TransactionCharges.calculate().

        This is a convenience wrapper; the main path goes through
        ``ChargesCalculator`` which also updates running totals.
        """
        charges = TransactionCharges.calculate(
            side=trade.side,
            instrument_type=trade.instrument.instrument_type,
            quantity=trade.quantity,
            price=trade.price,
            is_intraday=False,
        )
        return float(charges.total)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def take_snapshot(self) -> PnLSnapshot:
        """Take and store a portfolio P&L snapshot.

        Returns:
            The new ``PnLSnapshot``.
        """
        snapshot = self.get_portfolio_snapshot()
        self._snapshots.append(snapshot)
        logger.debug(
            "Snapshot taken: total_pnl=%s net_pnl=%s",
            snapshot.total_pnl,
            snapshot.net_pnl,
        )
        return snapshot

    def get_snapshots(self, limit: int = 100) -> list[PnLSnapshot]:
        """Return most recent P&L snapshots.

        Args:
            limit: Maximum number of snapshots to return.

        Returns:
            List of snapshots, newest first.
        """
        return list(reversed(self._snapshots[-limit:]))

    # ------------------------------------------------------------------
    # Day reset
    # ------------------------------------------------------------------

    def reset_day(self) -> None:
        """Reset day-level accounting.

        Captures current net P&L per position as the day start baseline,
        and resets the charges calculator day totals.
        """
        self._day_start_values.clear()
        for key, entry in self._positions.items():
            self._day_start_values[key] = entry.net_pnl

        self._charges_calc.reset_day()
        logger.info("PnLCalculator day reset complete")

    # ------------------------------------------------------------------
    # Access to sub-engines
    # ------------------------------------------------------------------

    @property
    def trade_book(self) -> TradeBook:
        """Access the underlying trade book."""
        return self._trade_book

    @property
    def charges_calculator(self) -> ChargesCalculator:
        """Access the underlying charges calculator."""
        return self._charges_calc


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _is_reducing(old_signed: int, delta: int) -> bool:
    """Return True if delta reduces (or flips) the position."""
    if old_signed > 0 and delta < 0:
        return True
    if old_signed < 0 and delta > 0:
        return True
    return False


def _same_direction(old_signed: int, delta: int) -> bool:
    """Return True if delta adds to the same direction as old position."""
    return (old_signed > 0 and delta > 0) or (old_signed < 0 and delta < 0)
