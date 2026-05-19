"""
Real-time position tracking from trade fills.

Builds and maintains live position state from :class:`Trade` events,
computes unrealised / realised P&L, tracks per-position drawdown, and
provides strategy-level and instrument-level views of the portfolio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from core.models import (
    InstrumentType,
    Order,
    OrderSide,
    Position,
    Trade,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position state
# ---------------------------------------------------------------------------


@dataclass
class PositionState:
    """Extended position state with risk metadata."""

    position: Position
    entry_value: float = 0.0          # total entry cost (avg_price * abs(qty) * lot)
    current_value: float = 0.0        # current market value
    unrealised_pnl: float = 0.0
    realised_pnl: float = 0.0
    day_pnl: float = 0.0
    max_drawdown: float = 0.0         # worst unrealised PnL seen (most negative)
    peak_pnl: float = 0.0             # best unrealised PnL seen
    last_price: float = 0.0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # --- helpers ---

    @property
    def symbol(self) -> str:
        return self.position.instrument.symbol

    @property
    def strategy_id(self) -> str:
        return self.position.strategy_id or ""

    @property
    def quantity(self) -> int:
        return self.position.quantity

    @property
    def is_closed(self) -> bool:
        return self.position.quantity == 0

    @property
    def lot_size(self) -> int:
        return self.position.instrument.lot_size

    @property
    def key(self) -> str:
        """Composite key used to store the position: ``symbol:strategy_id``."""
        return _make_key(self.position.instrument.symbol, self.position.strategy_id or "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_key(symbol: str, strategy_id: str) -> str:
    """Build the canonical position-map key."""
    return f"{symbol}:{strategy_id}"


def _effective_qty(trade: Trade) -> int:
    """Return signed quantity: positive for BUY, negative for SELL."""
    return trade.quantity if trade.side == OrderSide.BUY else -trade.quantity


# ---------------------------------------------------------------------------
# Position Tracker
# ---------------------------------------------------------------------------


class PositionTracker:
    """Tracks positions from trade events and computes real-time P&L.

    Features
    --------
    - Builds positions from trade fills (handles buys, sells, partial fills).
    - Computes unrealised P&L using last traded price.
    - Tracks realised P&L when positions are reduced or closed.
    - Per-strategy and per-instrument position views.
    - Position-level drawdown tracking.

    Usage::

        tracker = PositionTracker()
        tracker.on_trade(trade)                    # from OMS
        tracker.update_price("NIFTY", 24300.0)     # from market data
        positions = tracker.get_all_positions()
    """

    def __init__(self) -> None:
        # key = "symbol:strategy_id"
        self._positions: dict[str, PositionState] = {}
        self._closed_positions: list[PositionState] = []

    # ------------------------------------------------------------------
    # Trade handling
    # ------------------------------------------------------------------

    def on_trade(self, trade: Trade) -> PositionState:
        """Process a trade fill and update (or create) the corresponding position.

        Handles:
        - Opening a new position.
        - Adding to an existing position (same side).
        - Partial or full closing of a position (opposite side).
        - Flipping a position through zero (e.g. long 10 -> short 5).

        Returns the updated :class:`PositionState`.
        """
        strategy_id = trade.strategy_id or ""
        key = _make_key(trade.instrument.symbol, strategy_id)
        trade_price = float(trade.price)
        signed_qty = _effective_qty(trade)
        lot_size = trade.instrument.lot_size

        state = self._positions.get(key)

        if state is None:
            # Brand-new position
            state = self._create_position(trade, signed_qty, trade_price, key)
            logger.info(
                "Opened position %s qty=%d @ %.2f",
                key, signed_qty, trade_price,
            )
            return state

        old_qty = state.position.quantity
        new_qty = old_qty + signed_qty

        if old_qty == 0:
            # Re-opening a previously closed position
            state = self._create_position(trade, signed_qty, trade_price, key)
            logger.info(
                "Re-opened position %s qty=%d @ %.2f",
                key, signed_qty, trade_price,
            )
            return state

        # Determine whether the trade is adding to or reducing the position.
        same_direction = (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0)

        if same_direction:
            # --- Adding to position: weighted-average entry price ---
            old_avg = float(state.position.average_price)
            total_cost = old_avg * abs(old_qty) + trade_price * abs(signed_qty)
            new_avg = total_cost / abs(new_qty)
            state.position.average_price = Decimal(str(round(new_avg, 4)))
            state.position.quantity = new_qty
            state.entry_value = new_avg * abs(new_qty) * lot_size
            logger.info(
                "Added to position %s qty=%d -> %d avg=%.2f",
                key, old_qty, new_qty, new_avg,
            )
        else:
            # --- Reducing / closing / flipping position ---
            close_qty = min(abs(signed_qty), abs(old_qty))
            old_avg = float(state.position.average_price)

            # Realised PnL: difference between trade price and average entry
            if old_qty > 0:
                # Was long, now selling
                realised = (trade_price - old_avg) * close_qty * lot_size
            else:
                # Was short, now buying
                realised = (old_avg - trade_price) * close_qty * lot_size

            state.realised_pnl += realised

            if new_qty == 0:
                # Fully closed
                state.position.quantity = 0
                state.entry_value = 0.0
                state.unrealised_pnl = 0.0
                state.current_value = 0.0
                state.position.pnl_realized = Decimal(str(round(state.realised_pnl, 2)))
                state.position.pnl_unrealized = Decimal("0")
                state.updated_at = datetime.now(timezone.utc)
                # Archive
                self._closed_positions.append(state)
                logger.info(
                    "Closed position %s realised=%.2f total_realised=%.2f",
                    key, realised, state.realised_pnl,
                )
                return state

            if abs(signed_qty) > abs(old_qty):
                # Flipped through zero — remaining quantity opens a new leg
                remaining = abs(signed_qty) - abs(old_qty)
                flip_signed = remaining if signed_qty > 0 else -remaining
                state.position.quantity = flip_signed
                state.position.average_price = Decimal(str(round(trade_price, 4)))
                state.entry_value = trade_price * abs(flip_signed) * lot_size
                logger.info(
                    "Flipped position %s from %d to %d realised=%.2f",
                    key, old_qty, flip_signed, realised,
                )
            else:
                # Partially reduced — average price stays the same
                state.position.quantity = new_qty
                state.entry_value = old_avg * abs(new_qty) * lot_size
                logger.info(
                    "Reduced position %s qty=%d -> %d realised=%.2f",
                    key, old_qty, new_qty, realised,
                )

        # Recompute unrealised PnL with the last known price
        self._recompute_unrealised(state)
        state.position.pnl_realized = Decimal(str(round(state.realised_pnl, 2)))
        state.updated_at = datetime.now(timezone.utc)
        return state

    # ------------------------------------------------------------------
    # Price updates
    # ------------------------------------------------------------------

    def update_price(self, symbol: str, price: float) -> None:
        """Update last traded price for all positions matching *symbol*.

        Recomputes unrealised P&L, current value, drawdown, and peak.
        """
        if price <= 0:
            logger.warning("Ignoring non-positive price %.4f for %s", price, symbol)
            return

        for key, state in self._positions.items():
            if state.position.instrument.symbol != symbol:
                continue
            if state.position.quantity == 0:
                continue

            state.last_price = price
            state.position.ltp = Decimal(str(round(price, 4)))
            self._recompute_unrealised(state)
            state.updated_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position(self, symbol: str, strategy_id: str = "") -> PositionState | None:
        """Return the position state for the given symbol and strategy, or ``None``."""
        return self._positions.get(_make_key(symbol, strategy_id))

    def get_all_positions(self) -> list[PositionState]:
        """Return all open (non-zero quantity) positions."""
        return [s for s in self._positions.values() if s.position.quantity != 0]

    def get_positions_by_strategy(self, strategy_id: str) -> list[PositionState]:
        """Return all open positions belonging to *strategy_id*."""
        return [
            s
            for s in self._positions.values()
            if s.strategy_id == strategy_id and s.position.quantity != 0
        ]

    def get_positions_by_symbol(self, symbol: str) -> list[PositionState]:
        """Return all open positions for *symbol* across all strategies."""
        return [
            s
            for s in self._positions.values()
            if s.position.instrument.symbol == symbol and s.position.quantity != 0
        ]

    def get_closed_positions(self) -> list[PositionState]:
        """Return all positions that have been fully closed."""
        return list(self._closed_positions)

    # ------------------------------------------------------------------
    # Aggregate metrics
    # ------------------------------------------------------------------

    def get_total_unrealised_pnl(self) -> float:
        """Sum of unrealised PnL across all open positions."""
        return sum(s.unrealised_pnl for s in self._positions.values() if s.position.quantity != 0)

    def get_total_realised_pnl(self) -> float:
        """Sum of realised PnL across all positions (open and closed)."""
        total = sum(s.realised_pnl for s in self._positions.values())
        total += sum(s.realised_pnl for s in self._closed_positions)
        return total

    def get_net_exposure(self) -> float:
        """Sum of absolute current values of all open positions (gross exposure)."""
        return sum(abs(s.current_value) for s in self._positions.values() if s.position.quantity != 0)

    def get_net_signed_exposure(self) -> float:
        """Net signed exposure: long positions positive, short negative."""
        return sum(s.current_value for s in self._positions.values() if s.position.quantity != 0)

    def get_position_count(self) -> int:
        """Number of open positions."""
        return sum(1 for s in self._positions.values() if s.position.quantity != 0)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all positions and history. Typically used at start of day."""
        self._positions.clear()
        self._closed_positions.clear()
        logger.info("PositionTracker reset — all positions cleared")

    # ------------------------------------------------------------------
    # Snapshot / serialisation
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serialisable summary of current position state."""
        positions = self.get_all_positions()
        return {
            "open_positions": len(positions),
            "total_unrealised_pnl": round(self.get_total_unrealised_pnl(), 2),
            "total_realised_pnl": round(self.get_total_realised_pnl(), 2),
            "net_exposure": round(self.get_net_exposure(), 2),
            "closed_count": len(self._closed_positions),
            "positions": [
                {
                    "symbol": s.symbol,
                    "strategy_id": s.strategy_id,
                    "quantity": s.quantity,
                    "avg_price": float(s.position.average_price),
                    "last_price": s.last_price,
                    "unrealised_pnl": round(s.unrealised_pnl, 2),
                    "realised_pnl": round(s.realised_pnl, 2),
                    "max_drawdown": round(s.max_drawdown, 2),
                    "peak_pnl": round(s.peak_pnl, 2),
                }
                for s in positions
            ],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_position(
        self,
        trade: Trade,
        signed_qty: int,
        trade_price: float,
        key: str,
    ) -> PositionState:
        """Create a fresh :class:`PositionState` from a trade and store it."""
        lot_size = trade.instrument.lot_size
        position = Position(
            instrument=trade.instrument,
            strategy_id=trade.strategy_id,
            quantity=signed_qty,
            average_price=Decimal(str(round(trade_price, 4))),
            ltp=Decimal(str(round(trade_price, 4))),
            product_type=trade.instrument.lot_size and "NRML" or "NRML",  # default
        )
        state = PositionState(
            position=position,
            entry_value=trade_price * abs(signed_qty) * lot_size,
            current_value=trade_price * abs(signed_qty) * lot_size,
            unrealised_pnl=0.0,
            realised_pnl=0.0,
            day_pnl=0.0,
            max_drawdown=0.0,
            peak_pnl=0.0,
            last_price=trade_price,
            updated_at=datetime.now(timezone.utc),
        )
        self._positions[key] = state
        return state

    def _recompute_unrealised(self, state: PositionState) -> None:
        """Recompute unrealised P&L, current value, drawdown, and peak for a position."""
        qty = state.position.quantity
        if qty == 0:
            state.unrealised_pnl = 0.0
            state.current_value = 0.0
            return

        avg_price = float(state.position.average_price)
        last = state.last_price
        lot_size = state.lot_size

        if qty > 0:
            state.unrealised_pnl = (last - avg_price) * abs(qty) * lot_size
        else:
            state.unrealised_pnl = (avg_price - last) * abs(qty) * lot_size

        state.current_value = last * qty * lot_size  # signed value

        # Update peak and drawdown
        if state.unrealised_pnl > state.peak_pnl:
            state.peak_pnl = state.unrealised_pnl
        if state.unrealised_pnl < state.max_drawdown:
            state.max_drawdown = state.unrealised_pnl

        # Sync pydantic model fields
        state.position.pnl_unrealized = Decimal(str(round(state.unrealised_pnl, 2)))
        state.position.value = Decimal(str(round(state.current_value, 2)))
        state.position.ltp = Decimal(str(round(last, 4)))

        # Day PnL = unrealised + realised (for the current session)
        state.day_pnl = state.unrealised_pnl + state.realised_pnl
