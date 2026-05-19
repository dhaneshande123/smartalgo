"""
SPAN-like margin calculation for Indian markets (NSE F&O).

Implements a simplified margin model based on NSE's SPAN framework:

- **Futures**: Initial margin = price x lot_size x (SPAN% + exposure%).
- **Options buy**: Premium paid (no additional margin required).
- **Options sell**: max(SPAN margin, short option minimum margin).
- **Portfolio-level**: Hedging benefit for offsetting positions.

Margin percentages are configurable and default to typical NSE values for
index and stock derivatives.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import Decimal

from core.constants import LOT_SIZES
from core.models import (
    InstrumentType,
    MarginInfo,
    Order,
    OrderSide,
    Position,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — default margin percentages by underlying
# ---------------------------------------------------------------------------

# SPAN margin as a fraction of notional (e.g., 0.12 = 12%)
_DEFAULT_SPAN_MARGINS: dict[str, float] = {
    "NIFTY": 0.12,
    "BANKNIFTY": 0.14,
    "FINNIFTY": 0.13,
    "MIDCPNIFTY": 0.16,
    "SENSEX": 0.13,
    "BANKEX": 0.15,
    # Stock futures — generic fallback for unlisted symbols
    "__default__": 0.20,
}

# Exposure margin as a fraction of notional
_DEFAULT_EXPOSURE_MARGINS: dict[str, float] = {
    "NIFTY": 0.03,
    "BANKNIFTY": 0.035,
    "FINNIFTY": 0.03,
    "MIDCPNIFTY": 0.04,
    "SENSEX": 0.03,
    "BANKEX": 0.035,
    "__default__": 0.05,
}

# Minimum margin for short options as a fraction of notional
_SHORT_OPTION_MIN_MARGIN_PCT: float = 0.075  # 7.5% of notional

# Hedging benefit percentage for hedged positions
_HEDGING_BENEFIT_PCT: float = 0.30  # 30% reduction for hedged pairs


# ---------------------------------------------------------------------------
# Margin Calculator
# ---------------------------------------------------------------------------


class MarginCalculator:
    """Calculates margin requirements for NSE F&O positions.

    Implements a simplified SPAN-like margin model:

    - Futures: Initial margin = price x lot_size x margin_pct
    - Options buy: Premium paid (no additional margin)
    - Options sell: max(SPAN margin, short option minimum margin)
    - Portfolio-level margin benefit from hedged positions

    Uses NSE standard margin percentages:

    - NIFTY futures: ~12% SPAN + 3% exposure
    - BANKNIFTY futures: ~14% SPAN + 3.5% exposure
    - Stock futures: 15-45% depending on volatility

    Usage::

        calc = MarginCalculator()
        margin = calc.calculate_order_margin(order, current_price)
        total = calc.calculate_portfolio_margin(positions, prices)
    """

    def __init__(
        self,
        span_margins: dict[str, float] | None = None,
        exposure_margins: dict[str, float] | None = None,
    ) -> None:
        self.SPAN_MARGINS: dict[str, float] = dict(_DEFAULT_SPAN_MARGINS)
        if span_margins:
            self.SPAN_MARGINS.update(span_margins)

        self.EXPOSURE_MARGINS: dict[str, float] = dict(_DEFAULT_EXPOSURE_MARGINS)
        if exposure_margins:
            self.EXPOSURE_MARGINS.update(exposure_margins)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_order_margin(self, order: Order, spot_price: float) -> MarginInfo:
        """Calculate the margin required to place *order* at *spot_price*.

        Args:
            order: The order to evaluate.
            spot_price: Current spot / underlying price.

        Returns:
            :class:`MarginInfo` with ``span_margin``, ``exposure_margin``,
            and ``used_margin`` populated.
        """
        inst = order.instrument
        symbol = inst.underlying or inst.symbol
        lot_size = inst.lot_size
        qty = order.quantity
        price = float(order.price or order.trigger_price or Decimal("0")) or spot_price

        if inst.instrument_type == InstrumentType.FUTURE:
            span, exposure = self._futures_margin(symbol, qty, lot_size, price)
            return MarginInfo(
                span_margin=Decimal(str(round(span, 2))),
                exposure_margin=Decimal(str(round(exposure, 2))),
                used_margin=Decimal(str(round(span + exposure, 2))),
            )

        if inst.instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
            is_call = inst.instrument_type == InstrumentType.CALL_OPTION
            strike = float(inst.strike or Decimal("0"))

            if order.side == OrderSide.BUY:
                # Option buy — margin is just the premium paid
                premium_cost = self._option_buy_margin(price, qty, lot_size)
                return MarginInfo(
                    used_margin=Decimal(str(round(premium_cost, 2))),
                )

            # Option sell
            span, exposure = self._option_sell_margin(
                symbol, strike, spot_price, qty, lot_size, is_call,
            )
            return MarginInfo(
                span_margin=Decimal(str(round(span, 2))),
                exposure_margin=Decimal(str(round(exposure, 2))),
                used_margin=Decimal(str(round(span + exposure, 2))),
            )

        # Equity / Index — no derivative margin, just full value
        total = price * qty * lot_size
        return MarginInfo(used_margin=Decimal(str(round(total, 2))))

    def calculate_position_margin(
        self,
        position: Position,
        spot_price: float,
    ) -> MarginInfo:
        """Calculate margin required to hold an existing *position*.

        Uses the same logic as :meth:`calculate_order_margin` but derives
        side and quantity from the position.
        """
        inst = position.instrument
        symbol = inst.underlying or inst.symbol
        lot_size = inst.lot_size
        qty = abs(position.quantity)
        price = float(position.average_price) if float(position.average_price) > 0 else spot_price

        if qty == 0:
            return MarginInfo()

        if inst.instrument_type == InstrumentType.FUTURE:
            span, exposure = self._futures_margin(symbol, qty, lot_size, spot_price)
            return MarginInfo(
                span_margin=Decimal(str(round(span, 2))),
                exposure_margin=Decimal(str(round(exposure, 2))),
                used_margin=Decimal(str(round(span + exposure, 2))),
            )

        if inst.instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
            is_call = inst.instrument_type == InstrumentType.CALL_OPTION
            strike = float(inst.strike or Decimal("0"))

            if position.quantity > 0:
                # Long option — margin is embedded in the premium already paid
                premium_cost = self._option_buy_margin(price, qty, lot_size)
                return MarginInfo(
                    used_margin=Decimal(str(round(premium_cost, 2))),
                )

            # Short option
            span, exposure = self._option_sell_margin(
                symbol, strike, spot_price, qty, lot_size, is_call,
            )
            return MarginInfo(
                span_margin=Decimal(str(round(span, 2))),
                exposure_margin=Decimal(str(round(exposure, 2))),
                used_margin=Decimal(str(round(span + exposure, 2))),
            )

        total = spot_price * qty * lot_size
        return MarginInfo(used_margin=Decimal(str(round(total, 2))))

    def calculate_portfolio_margin(
        self,
        positions: list[Position],
        prices: dict[str, float],
    ) -> MarginInfo:
        """Calculate total margin for a portfolio of positions.

        Sums individual position margins and then applies a hedging benefit
        for offsetting positions (e.g., bull call spread).

        Args:
            positions: List of open positions.
            prices: Map of ``symbol -> spot_price`` for each underlying.

        Returns:
            Aggregate :class:`MarginInfo` with hedging benefit applied.
        """
        if not positions:
            return MarginInfo()

        total_span = Decimal("0")
        total_exposure = Decimal("0")
        total_used = Decimal("0")

        for pos in positions:
            if pos.quantity == 0:
                continue

            symbol = pos.instrument.underlying or pos.instrument.symbol
            spot = prices.get(symbol, 0.0)
            if spot <= 0:
                # Fallback to average price if no spot available
                spot = float(pos.average_price) if float(pos.average_price) > 0 else 0.0
            if spot <= 0:
                logger.warning(
                    "No price available for %s — skipping margin calculation", symbol,
                )
                continue

            margin = self.calculate_position_margin(pos, spot)
            total_span += margin.span_margin
            total_exposure += margin.exposure_margin
            total_used += margin.used_margin

        # Apply hedging benefit
        hedge_benefit = Decimal(str(round(self._hedging_benefit(positions), 2)))
        total_used = max(Decimal("0"), total_used - hedge_benefit)

        utilization = 0.0  # caller should compute based on available capital

        return MarginInfo(
            span_margin=total_span,
            exposure_margin=total_exposure,
            used_margin=total_used,
            utilization_pct=utilization,
        )

    # ------------------------------------------------------------------
    # Internal margin calculation methods
    # ------------------------------------------------------------------

    def _futures_margin(
        self,
        symbol: str,
        qty: int,
        lot_size: int,
        price: float,
    ) -> tuple[float, float]:
        """Calculate SPAN and exposure margin for a futures position.

        Returns:
            ``(span_margin, exposure_margin)`` in INR.
        """
        notional = price * qty * lot_size
        span_pct = self.SPAN_MARGINS.get(symbol, self.SPAN_MARGINS["__default__"])
        exposure_pct = self.EXPOSURE_MARGINS.get(symbol, self.EXPOSURE_MARGINS["__default__"])

        span_margin = notional * span_pct
        exposure_margin = notional * exposure_pct

        logger.debug(
            "Futures margin for %s: notional=%.0f SPAN=%.0f (%.1f%%) exposure=%.0f (%.1f%%)",
            symbol, notional, span_margin, span_pct * 100, exposure_margin, exposure_pct * 100,
        )
        return span_margin, exposure_margin

    def _option_buy_margin(
        self,
        premium: float,
        qty: int,
        lot_size: int,
    ) -> float:
        """Calculate margin for buying options — just the premium paid.

        Returns:
            Total premium cost in INR.
        """
        return premium * qty * lot_size

    def _option_sell_margin(
        self,
        symbol: str,
        strike: float,
        spot: float,
        qty: int,
        lot_size: int,
        is_call: bool,
    ) -> tuple[float, float]:
        """Calculate SPAN and exposure margin for selling (writing) options.

        Uses a simplified SPAN model:

        1. **SPAN margin** = underlying_span_pct x notional +/- OTM adjustment
        2. **Short option minimum** = min_margin_pct x notional
        3. Final SPAN = max(SPAN margin, short option minimum)

        Returns:
            ``(span_margin, exposure_margin)`` in INR.
        """
        if spot <= 0:
            logger.warning("Spot price is zero for %s — cannot compute option sell margin", symbol)
            return 0.0, 0.0

        notional = spot * qty * lot_size
        span_pct = self.SPAN_MARGINS.get(symbol, self.SPAN_MARGINS["__default__"])
        exposure_pct = self.EXPOSURE_MARGINS.get(symbol, self.EXPOSURE_MARGINS["__default__"])

        # Base SPAN margin on the underlying
        base_span = notional * span_pct

        # OTM adjustment: reduce margin for deep OTM options
        if is_call:
            otm_amount = max(0.0, strike - spot)
        else:
            otm_amount = max(0.0, spot - strike)

        # OTM discount: reduce by half the OTM amount (capped)
        otm_discount = min(otm_amount * qty * lot_size * 0.5, base_span * 0.5)
        span_margin = base_span - otm_discount

        # Short option minimum margin
        min_margin = notional * _SHORT_OPTION_MIN_MARGIN_PCT

        # Take the higher of SPAN and minimum
        span_margin = max(span_margin, min_margin)

        # Exposure margin for short options
        exposure_margin = notional * exposure_pct

        logger.debug(
            "Option sell margin for %s %s strike=%.0f spot=%.0f: "
            "SPAN=%.0f exposure=%.0f (notional=%.0f)",
            symbol,
            "CE" if is_call else "PE",
            strike,
            spot,
            span_margin,
            exposure_margin,
            notional,
        )
        return span_margin, exposure_margin

    def _hedging_benefit(self, positions: list[Position]) -> float:
        """Estimate margin reduction for hedged positions.

        Identifies pairs of offsetting positions on the same underlying
        (e.g., long call + short call at different strikes, or long future +
        short option) and grants a percentage reduction on the smaller leg.

        This is a simplified model; the actual NSE SPAN scanning risk model
        is more sophisticated, evaluating 16 risk scenarios.

        Returns:
            Total hedging benefit (margin reduction) in INR.
        """
        # Group positions by underlying
        by_underlying: dict[str, list[Position]] = {}
        for pos in positions:
            if pos.quantity == 0:
                continue
            underlying = pos.instrument.underlying or pos.instrument.symbol
            by_underlying.setdefault(underlying, []).append(pos)

        total_benefit = 0.0

        for underlying, group in by_underlying.items():
            if len(group) < 2:
                continue

            # Separate longs and shorts
            longs = [p for p in group if p.quantity > 0]
            shorts = [p for p in group if p.quantity < 0]

            if not longs or not shorts:
                continue

            # Simple heuristic: for each long-short pair on the same underlying,
            # grant a hedging benefit on the smaller leg's notional.
            long_notional = sum(
                float(p.ltp or p.average_price) * abs(p.quantity) * p.instrument.lot_size
                for p in longs
            )
            short_notional = sum(
                float(p.ltp or p.average_price) * abs(p.quantity) * p.instrument.lot_size
                for p in shorts
            )

            hedged_notional = min(long_notional, short_notional)
            benefit = hedged_notional * _HEDGING_BENEFIT_PCT

            logger.debug(
                "Hedging benefit for %s: long_notional=%.0f short_notional=%.0f "
                "hedged=%.0f benefit=%.0f",
                underlying, long_notional, short_notional, hedged_notional, benefit,
            )
            total_benefit += benefit

        return total_benefit
