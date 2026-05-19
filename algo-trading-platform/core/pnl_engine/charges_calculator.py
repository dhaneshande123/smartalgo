"""
Charges Calculator — wraps TransactionCharges.calculate() with running totals.

Maintains cumulative charge breakdowns per strategy and across the portfolio,
providing a single source of truth for all transaction-cost accounting.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from core.models import (
    InstrumentType,
    ProductType,
    Trade,
    TransactionCharges,
)

logger = logging.getLogger(__name__)


class ChargesCalculator:
    """Calculates and tracks all transaction charges.

    Wraps ``TransactionCharges.calculate()`` from ``core.models`` and maintains
    running totals per strategy and overall.

    Usage::

        calc = ChargesCalculator()
        charges = calc.calculate(trade, brokerage_per_order=20.0)
        total = calc.get_total_charges()
        breakdown = calc.get_breakdown()
    """

    def __init__(self) -> None:
        self._total_charges: float = 0.0
        self._strategy_charges: dict[str, float] = defaultdict(float)
        self._charge_breakdown: dict[str, float] = {
            "stt": 0.0,
            "exchange_txn": 0.0,
            "gst": 0.0,
            "sebi_fee": 0.0,
            "stamp_duty": 0.0,
            "brokerage": 0.0,
        }
        self._trade_count: int = 0
        self._day_total_charges: float = 0.0
        self._day_strategy_charges: dict[str, float] = defaultdict(float)
        self._day_breakdown: dict[str, float] = {
            "stt": 0.0,
            "exchange_txn": 0.0,
            "gst": 0.0,
            "sebi_fee": 0.0,
            "stamp_duty": 0.0,
            "brokerage": 0.0,
        }

    def calculate(
        self,
        trade: Trade,
        brokerage_per_order: float = 20.0,
    ) -> TransactionCharges:
        """Calculate transaction charges for a trade and update running totals.

        Args:
            trade: The executed trade.
            brokerage_per_order: Flat brokerage per order (default Rs 20).
                Currently used for reference; the actual brokerage is computed
                inside ``TransactionCharges.calculate()`` using its own logic.

        Returns:
            A fully populated ``TransactionCharges`` instance.
        """
        is_intraday = (
            trade.instrument.segment is not None
            and hasattr(trade, "_product_type")
            and getattr(trade, "_product_type", None) == ProductType.MIS
        )
        # Default to non-intraday since Trade model does not carry product_type.
        # Callers can set is_intraday via the instrument type heuristic below.
        if trade.instrument.instrument_type in (
            InstrumentType.FUTURE,
            InstrumentType.CALL_OPTION,
            InstrumentType.PUT_OPTION,
        ):
            is_intraday = False  # F&O is always NRML-style for charge purposes
        else:
            is_intraday = False  # conservative: treat as delivery

        charges = TransactionCharges.calculate(
            side=trade.side,
            instrument_type=trade.instrument.instrument_type,
            quantity=trade.quantity,
            price=trade.price,
            is_intraday=is_intraday,
        )

        total_float = float(charges.total)
        strategy_id = trade.strategy_id or "__unattributed__"

        # Update running totals
        self._total_charges += total_float
        self._strategy_charges[strategy_id] += total_float
        self._trade_count += 1

        # Update breakdown
        self._charge_breakdown["stt"] += float(charges.stt)
        self._charge_breakdown["exchange_txn"] += float(charges.exchange_txn_fee)
        self._charge_breakdown["gst"] += float(charges.gst)
        self._charge_breakdown["sebi_fee"] += float(charges.sebi_fee)
        self._charge_breakdown["stamp_duty"] += float(charges.stamp_duty)
        self._charge_breakdown["brokerage"] += float(charges.brokerage)

        # Update day totals
        self._day_total_charges += total_float
        self._day_strategy_charges[strategy_id] += total_float
        self._day_breakdown["stt"] += float(charges.stt)
        self._day_breakdown["exchange_txn"] += float(charges.exchange_txn_fee)
        self._day_breakdown["gst"] += float(charges.gst)
        self._day_breakdown["sebi_fee"] += float(charges.sebi_fee)
        self._day_breakdown["stamp_duty"] += float(charges.stamp_duty)
        self._day_breakdown["brokerage"] += float(charges.brokerage)

        logger.debug(
            "Charges for trade %s: total=%.2f (STT=%.2f, GST=%.2f)",
            trade.trade_id,
            total_float,
            float(charges.stt),
            float(charges.gst),
        )

        return charges

    def get_total_charges(self) -> float:
        """Return cumulative total charges across all trades."""
        return round(self._total_charges, 2)

    def get_strategy_charges(self, strategy_id: str) -> float:
        """Return cumulative charges for a specific strategy."""
        return round(self._strategy_charges.get(strategy_id, 0.0), 2)

    def get_breakdown(self) -> dict[str, float]:
        """Return cumulative charge breakdown by component."""
        return {k: round(v, 2) for k, v in self._charge_breakdown.items()}

    def get_day_total_charges(self) -> float:
        """Return today's total charges."""
        return round(self._day_total_charges, 2)

    def get_day_strategy_charges(self, strategy_id: str) -> float:
        """Return today's charges for a specific strategy."""
        return round(self._day_strategy_charges.get(strategy_id, 0.0), 2)

    def get_day_breakdown(self) -> dict[str, float]:
        """Return today's charge breakdown by component."""
        return {k: round(v, 2) for k, v in self._day_breakdown.items()}

    def get_trade_count(self) -> int:
        """Return total number of trades processed."""
        return self._trade_count

    def reset_day(self) -> None:
        """Reset day-level charge accumulators (called at start of new trading day).

        Cumulative (all-time) totals are preserved.
        """
        self._day_total_charges = 0.0
        self._day_strategy_charges.clear()
        for key in self._day_breakdown:
            self._day_breakdown[key] = 0.0
        logger.info("ChargesCalculator day totals reset")
