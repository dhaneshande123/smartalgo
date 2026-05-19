"""
Trade Book — maintains a complete record of all executed trades with P&L
attribution, filtering, and summary statistics.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from core.models import Trade

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TradeRecord:
    """A single trade entry in the trade book with P&L metadata."""

    trade: Trade
    charges: float = 0.0
    net_pnl: float = 0.0
    running_pnl: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TradeBook:
    """Complete trade record with P&L attribution.

    Stores every trade that flows through the P&L engine and provides
    query/filter capabilities plus summary statistics per strategy,
    per symbol, and across the entire book.

    Usage::

        book = TradeBook()
        book.record_trade(trade, charges=45.50, net_pnl=1200.0)
        trades = book.get_trades(strategy_id="strat_1")
        summary = book.get_summary()
    """

    def __init__(self) -> None:
        self._trades: list[TradeRecord] = []
        self._by_strategy: dict[str, list[TradeRecord]] = defaultdict(list)
        self._by_symbol: dict[str, list[TradeRecord]] = defaultdict(list)
        self._running_pnl: float = 0.0
        self._winning_trades: int = 0
        self._losing_trades: int = 0
        self._breakeven_trades: int = 0
        self._total_pnl_value: float = 0.0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_trade(
        self,
        trade: Trade,
        charges: float = 0.0,
        net_pnl: float = 0.0,
    ) -> TradeRecord:
        """Record a trade in the book.

        Args:
            trade: The executed trade.
            charges: Transaction charges attributed to this trade.
            net_pnl: Net P&L realised from this trade (after charges).

        Returns:
            The created ``TradeRecord``.
        """
        self._running_pnl += net_pnl
        self._total_pnl_value += net_pnl

        record = TradeRecord(
            trade=trade,
            charges=charges,
            net_pnl=net_pnl,
            running_pnl=self._running_pnl,
            timestamp=trade.timestamp,
        )

        self._trades.append(record)

        strategy_id = trade.strategy_id or "__unattributed__"
        self._by_strategy[strategy_id].append(record)
        self._by_symbol[trade.instrument.symbol].append(record)

        # Track win/loss
        if net_pnl > 0:
            self._winning_trades += 1
        elif net_pnl < 0:
            self._losing_trades += 1
        else:
            self._breakeven_trades += 1

        logger.debug(
            "Recorded trade %s: net_pnl=%.2f running=%.2f",
            trade.trade_id,
            net_pnl,
            self._running_pnl,
        )

        return record

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_trades(
        self,
        strategy_id: str | None = None,
        symbol: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[TradeRecord]:
        """Retrieve trades with optional filters.

        Args:
            strategy_id: Filter by strategy. ``None`` returns all.
            symbol: Filter by instrument symbol. ``None`` returns all.
            since: Only return trades on or after this timestamp.
            limit: Maximum number of records to return (most recent first).

        Returns:
            List of matching ``TradeRecord`` instances, newest first.
        """
        if strategy_id is not None:
            source: Sequence[TradeRecord] = self._by_strategy.get(strategy_id, [])
        elif symbol is not None:
            source = self._by_symbol.get(symbol, [])
        else:
            source = self._trades

        results: list[TradeRecord] = []
        # Iterate in reverse for newest-first ordering
        for record in reversed(source):
            if since is not None and record.timestamp < since:
                continue
            # Apply the other filter if both strategy_id and symbol are given
            if strategy_id is not None and symbol is not None:
                if record.trade.instrument.symbol != symbol:
                    continue
            results.append(record)
            if len(results) >= limit:
                break

        return results

    def get_today_trades(self) -> list[TradeRecord]:
        """Return all trades from the current UTC day, newest first."""
        today = datetime.now(timezone.utc).date()
        results: list[TradeRecord] = []
        for record in reversed(self._trades):
            if record.timestamp.date() == today:
                results.append(record)
            elif record.timestamp.date() < today:
                break  # trades are chronological, so we can stop
        return results

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def get_summary(self) -> dict:
        """Return overall trade-book summary statistics.

        Returns:
            Dictionary with keys: total_trades, winning_trades, losing_trades,
            breakeven_trades, total_pnl, avg_pnl, win_rate, max_win, max_loss,
            total_charges, profit_factor.
        """
        return self._build_summary(self._trades)

    def get_strategy_summary(self, strategy_id: str) -> dict:
        """Return summary statistics for a specific strategy.

        Args:
            strategy_id: The strategy to summarise.

        Returns:
            Dictionary with the same keys as ``get_summary()``.
        """
        records = self._by_strategy.get(strategy_id, [])
        return self._build_summary(records)

    def get_symbol_summary(self, symbol: str) -> dict:
        """Return summary statistics for a specific symbol.

        Args:
            symbol: The instrument symbol to summarise.

        Returns:
            Dictionary with the same keys as ``get_summary()``.
        """
        records = self._by_symbol.get(symbol, [])
        return self._build_summary(records)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def total_trades(self) -> int:
        """Total number of recorded trades."""
        return len(self._trades)

    @property
    def total_pnl(self) -> float:
        """Cumulative net P&L across all recorded trades."""
        return round(self._total_pnl_value, 2)

    @property
    def strategies(self) -> list[str]:
        """List of strategy IDs that have recorded trades."""
        return list(self._by_strategy.keys())

    @property
    def symbols(self) -> list[str]:
        """List of symbols that have recorded trades."""
        return list(self._by_symbol.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(records: Sequence[TradeRecord]) -> dict:
        """Build a summary dict from a sequence of trade records."""
        if not records:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "breakeven_trades": 0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "win_rate": 0.0,
                "max_win": 0.0,
                "max_loss": 0.0,
                "total_charges": 0.0,
                "profit_factor": 0.0,
            }

        total = len(records)
        wins = 0
        losses = 0
        breakevens = 0
        total_pnl = 0.0
        total_charges = 0.0
        max_win = 0.0
        max_loss = 0.0
        gross_profit = 0.0
        gross_loss = 0.0

        for rec in records:
            pnl = rec.net_pnl
            total_pnl += pnl
            total_charges += rec.charges

            if pnl > 0:
                wins += 1
                gross_profit += pnl
                max_win = max(max_win, pnl)
            elif pnl < 0:
                losses += 1
                gross_loss += abs(pnl)
                max_loss = min(max_loss, pnl)
            else:
                breakevens += 1

        win_rate = (wins / total * 100.0) if total > 0 else 0.0
        avg_pnl = (total_pnl / total) if total > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        return {
            "total_trades": total,
            "winning_trades": wins,
            "losing_trades": losses,
            "breakeven_trades": breakevens,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "win_rate": round(win_rate, 2),
            "max_win": round(max_win, 2),
            "max_loss": round(max_loss, 2),
            "total_charges": round(total_charges, 2),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
        }
