"""
Indian F&O Trading Charges Calculator

Computes all regulatory and brokerage charges for NSE options & futures trades.
Used by the paper-trading P&L engine to produce realistic net P&L.

Charge structure (as of 2024-25):
  - Brokerage:          Rs 20/executed order (flat, discount broker)
  - STT:                Options SELL: 0.0625% of (premium * qty)
                        Futures:      0.0125% of turnover
  - Exchange Txn Fee:   NSE Options: 0.0495% of premium turnover
                        NSE Futures: 0.002% of turnover
  - GST:                18% on (brokerage + exchange txn fee)
  - SEBI Charges:       Rs 10 per crore of turnover
  - Stamp Duty:         Options: 0.003% of (premium * qty) — BUY side only
                        Futures: 0.002% of turnover — BUY side only

All amounts are in INR. Roundtrip = entry charges + exit charges.

Reference: NSE circulars + SEBI fee schedule (updated periodically).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Configurable charge rates ────────────────────────────────────
# Override these via config/settings if rates change.

BROKERAGE_PER_ORDER = 20.0          # flat per executed order (discount broker)
STT_OPTIONS_SELL_PCT = 0.000625     # 0.0625% — only on SELL side premium
STT_FUTURES_PCT = 0.000125          # 0.0125% — both sides
EXCHANGE_TXN_OPTIONS_PCT = 0.000495 # 0.0495% of premium turnover
EXCHANGE_TXN_FUTURES_PCT = 0.00002  # 0.002% of turnover
GST_PCT = 0.18                      # 18% on (brokerage + exchange txn fee)
SEBI_CHARGES_PER_CRORE = 10.0       # Rs 10 per crore
STAMP_DUTY_OPTIONS_PCT = 0.00003    # 0.003% — BUY side only
STAMP_DUTY_FUTURES_PCT = 0.00002    # 0.002% — BUY side only

# ── Slippage model ───────────────────────────────────────────────
# Realistic bid-ask spread for NIFTY/BANKNIFTY options.
# Deep ITM options have tighter spreads; far OTM have wider.
DEFAULT_SLIPPAGE_PCT = 0.005        # 0.5% of premium as slippage
MIN_SLIPPAGE_ABS = 0.10             # At least Rs 0.10 per contract


@dataclass
class TradeCharges:
    """Breakdown of charges for a single trade leg (one-way)."""
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn_fee: float = 0.0
    gst: float = 0.0
    sebi_charges: float = 0.0
    stamp_duty: float = 0.0
    slippage_cost: float = 0.0
    total: float = 0.0


@dataclass
class RoundtripCharges:
    """Charges for a complete entry + exit on one leg."""
    entry: TradeCharges = field(default_factory=TradeCharges)
    exit: TradeCharges = field(default_factory=TradeCharges)
    total: float = 0.0

    def to_dict(self) -> dict:
        return {
            "brokerage": round(self.entry.brokerage + self.exit.brokerage, 2),
            "stt": round(self.entry.stt + self.exit.stt, 2),
            "exchange_txn_fee": round(
                self.entry.exchange_txn_fee + self.exit.exchange_txn_fee, 2
            ),
            "gst": round(self.entry.gst + self.exit.gst, 2),
            "sebi_charges": round(
                self.entry.sebi_charges + self.exit.sebi_charges, 2
            ),
            "stamp_duty": round(self.entry.stamp_duty + self.exit.stamp_duty, 2),
            "slippage_cost": round(
                self.entry.slippage_cost + self.exit.slippage_cost, 2
            ),
            "total": round(self.total, 2),
        }


def compute_leg_charges(
    *,
    side: str,
    premium: float,
    qty: int,
    is_entry: bool,
    instrument_type: str = "options",
) -> TradeCharges:
    """Compute charges for a single leg (one direction).

    Args:
        side: "BUY" or "SELL"
        premium: option premium per unit at the time of trade
        qty: number of contracts (e.g. 65 for NIFTY 1 lot)
        is_entry: True for entry, False for exit
        instrument_type: "options" or "futures"
    """
    turnover = premium * qty
    ch = TradeCharges()

    # 1. Brokerage (flat per order)
    ch.brokerage = BROKERAGE_PER_ORDER

    # 2. STT
    if instrument_type == "options":
        # STT only on SELL side for options
        if side.upper() == "SELL":
            ch.stt = turnover * STT_OPTIONS_SELL_PCT
    else:
        ch.stt = turnover * STT_FUTURES_PCT

    # 3. Exchange transaction fee
    if instrument_type == "options":
        ch.exchange_txn_fee = turnover * EXCHANGE_TXN_OPTIONS_PCT
    else:
        ch.exchange_txn_fee = turnover * EXCHANGE_TXN_FUTURES_PCT

    # 4. GST (18% on brokerage + exchange txn fee)
    ch.gst = (ch.brokerage + ch.exchange_txn_fee) * GST_PCT

    # 5. SEBI charges (Rs 10 per crore)
    ch.sebi_charges = turnover * SEBI_CHARGES_PER_CRORE / 1e7

    # 6. Stamp duty (BUY side only)
    if side.upper() == "BUY":
        if instrument_type == "options":
            ch.stamp_duty = turnover * STAMP_DUTY_OPTIONS_PCT
        else:
            ch.stamp_duty = turnover * STAMP_DUTY_FUTURES_PCT

    # 7. Slippage (adverse fill from bid-ask spread)
    slip = max(premium * DEFAULT_SLIPPAGE_PCT, MIN_SLIPPAGE_ABS)
    ch.slippage_cost = slip * qty

    ch.total = (
        ch.brokerage
        + ch.stt
        + ch.exchange_txn_fee
        + ch.gst
        + ch.sebi_charges
        + ch.stamp_duty
        + ch.slippage_cost
    )
    return ch


def compute_roundtrip_charges(
    *,
    entry_side: str,
    entry_premium: float,
    exit_premium: float,
    qty: int,
    instrument_type: str = "options",
) -> RoundtripCharges:
    """Compute full roundtrip charges for one leg (entry + exit).

    The exit side is the reverse of entry_side.
    """
    exit_side = "SELL" if entry_side.upper() == "BUY" else "BUY"

    entry = compute_leg_charges(
        side=entry_side,
        premium=entry_premium,
        qty=qty,
        is_entry=True,
        instrument_type=instrument_type,
    )
    exit_ = compute_leg_charges(
        side=exit_side,
        premium=exit_premium,
        qty=qty,
        is_entry=False,
        instrument_type=instrument_type,
    )
    return RoundtripCharges(entry=entry, exit=exit_, total=entry.total + exit_.total)


def compute_strategy_charges(
    positions: list[dict],
    *,
    instrument_type: str = "options",
) -> dict:
    """Compute total charges for an entire strategy (all legs, roundtrip).

    Each position dict should have: side, entry_price, ltp (or exit_price), qty.

    Returns:
        {
            "total_charges": float,
            "total_slippage": float,
            "total_all": float,  # charges + slippage combined
            "per_leg": [RoundtripCharges.to_dict(), ...],
            "breakdown": {brokerage, stt, exchange_txn_fee, gst, sebi_charges, stamp_duty, slippage_cost}
        }
    """
    total_charges = 0.0
    total_slippage = 0.0
    per_leg = []
    breakdown = {
        "brokerage": 0.0,
        "stt": 0.0,
        "exchange_txn_fee": 0.0,
        "gst": 0.0,
        "sebi_charges": 0.0,
        "stamp_duty": 0.0,
        "slippage_cost": 0.0,
    }

    for pos in positions:
        entry_price = float(pos.get("entry_price", 0))
        exit_price = float(pos.get("ltp", 0) or pos.get("exit_price", 0))
        qty = int(pos.get("qty", 0))
        side = (pos.get("side") or "SELL").upper()

        if entry_price <= 0 or qty <= 0:
            continue

        rt = compute_roundtrip_charges(
            entry_side=side,
            entry_premium=entry_price,
            exit_premium=exit_price if exit_price > 0 else entry_price,
            qty=qty,
            instrument_type=instrument_type,
        )
        leg_dict = rt.to_dict()
        per_leg.append(leg_dict)

        for key in breakdown:
            breakdown[key] += leg_dict.get(key, 0)

        total_charges += rt.total - rt.entry.slippage_cost - rt.exit.slippage_cost
        total_slippage += rt.entry.slippage_cost + rt.exit.slippage_cost

    # Round everything
    for key in breakdown:
        breakdown[key] = round(breakdown[key], 2)

    return {
        "total_charges": round(total_charges, 2),
        "total_slippage": round(total_slippage, 2),
        "total_all": round(total_charges + total_slippage, 2),
        "per_leg": per_leg,
        "breakdown": breakdown,
    }
