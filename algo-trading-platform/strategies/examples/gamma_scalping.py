"""
Delta-Neutral Gamma Scalping Strategy
======================================

Buys an ATM straddle (long gamma) and dynamically hedges delta using
NIFTY futures.  The strategy profits from large intraday moves
(realized volatility > implied volatility) by re-hedging delta at
regular intervals.

Position:
    - Long ATM CE + Long ATM PE (positive gamma, positive vega).
    - Hedge net delta with NIFTY futures (short futures if delta > 0,
      long futures if delta < 0).

Hedging logic:
    - Rebalance delta when portfolio net delta exceeds a threshold
      (default: 0.3 per lot, roughly 7-8 NIFTY points of delta).
    - Rebalance using market-order futures in lot-size increments.

Exit logic:
    - Close all positions at 15:00 IST (intraday).
    - Maximum loss cap: Rs 10,000 per lot.
    - Minimum 5 rebalances before allowing exit on loss (give the
      strategy time to scalp gamma).
"""

from __future__ import annotations

from datetime import datetime, time as dt_time
from decimal import Decimal
from typing import Any

from core.models import (
    OrderSide,
    OrderType,
    ProductType,
    Tick,
)
from strategies.base_strategy import BaseStrategy, StrategyContext


class GammaScalpingStrategy(BaseStrategy):
    """Delta-neutral gamma scalping with ATM straddle + futures hedge."""

    UNDERLYING: str = "NIFTY"
    FUTURES_SYMBOL: str = "NIFTY-FUT"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 1
    STRIKE_STEP: int = 50
    DELTA_THRESHOLD: float = 0.30    # rebalance when |net_delta| > this
    MAX_LOSS_PER_LOT: float = 10000.0
    MIN_REBALANCES: int = 5          # min hedges before loss exit allowed
    SQUARE_OFF_TIME: dt_time = dt_time(15, 0)

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.quantity: int = 0
        self.ce_symbol: str = ""
        self.pe_symbol: str = ""
        self.ce_entry: float = 0.0
        self.pe_entry: float = 0.0
        self.futures_position: int = 0   # signed: +ve = long, -ve = short
        self.rebalance_count: int = 0
        self.in_position: bool = False
        self.total_premium_paid: float = 0.0
        self.futures_pnl: float = 0.0
        self.futures_entries: list[tuple[int, float]] = []  # (qty, price) pairs

    @staticmethod
    def _round_to_strike(price: float, step: int = 50) -> float:
        return round(price / step) * step

    def _build_symbol(self, strike: float, opt_type: str, expiry: Any) -> str:
        exp_str = expiry.strftime("%d%b%y").upper() if hasattr(expiry, "strftime") else str(expiry)
        return f"{self.UNDERLYING}{exp_str}{int(strike)}{opt_type}"

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_init(self, context: StrategyContext) -> None:
        self.ctx = context
        params = context.params
        self.UNDERLYING = params.get("underlying", self.UNDERLYING)
        self.NUM_LOTS = params.get("num_lots", self.NUM_LOTS)
        if self.UNDERLYING == "BANKNIFTY":
            self.LOT_SIZE = 15
            self.STRIKE_STEP = 100
            self.FUTURES_SYMBOL = "BANKNIFTY-FUT"
        self.quantity = self.LOT_SIZE * self.NUM_LOTS
        self.ctx.log.info("GammaScalpingStrategy initialized")

    async def on_start(self) -> None:
        await self.ctx.schedule("entry", "09:20")
        await self.ctx.schedule("square_off", "15:00")
        self.ctx.log.info("GammaScalpingStrategy started")

    async def on_stop(self) -> None:
        if self.in_position:
            await self.ctx.square_off_all()
            self.in_position = False

    async def on_schedule(self, event: Any) -> None:
        if event.name == "entry":
            await self._enter_straddle()
        elif event.name == "square_off":
            await self._time_exit()

    # ── entry ─────────────────────────────────────────────────────────

    async def _enter_straddle(self) -> None:
        """Buy ATM straddle to establish long gamma position."""
        if self.in_position:
            return

        spot = await self.ctx.get_underlying_price(self.UNDERLYING)
        atm = self._round_to_strike(spot, self.STRIKE_STEP)
        chain = await self.ctx.get_option_chain(self.UNDERLYING)
        expiry = chain.expiry

        self.ce_symbol = self._build_symbol(atm, "CE", expiry)
        self.pe_symbol = self._build_symbol(atm, "PE", expiry)

        # Buy CE
        await self.ctx.place_order(
            symbol=self.ce_symbol, side=OrderSide.BUY,
            order_type=OrderType.MARKET, product_type=ProductType.MIS,
            quantity=self.quantity,
        )
        # Buy PE
        await self.ctx.place_order(
            symbol=self.pe_symbol, side=OrderSide.BUY,
            order_type=OrderType.MARKET, product_type=ProductType.MIS,
            quantity=self.quantity,
        )

        ltp_map = await self.ctx.get_ltp([self.ce_symbol, self.pe_symbol])
        self.ce_entry = float(ltp_map.get(self.ce_symbol, 0.0))
        self.pe_entry = float(ltp_map.get(self.pe_symbol, 0.0))
        self.total_premium_paid = (self.ce_entry + self.pe_entry) * self.quantity
        self.in_position = True
        self.rebalance_count = 0
        self.futures_position = 0
        self.futures_entries.clear()
        self.futures_pnl = 0.0

        await self.ctx.alert(
            f"Gamma scalp straddle bought | ATM={atm} "
            f"| CE={self.ce_entry:.2f} PE={self.pe_entry:.2f} "
            f"| Premium paid={self.total_premium_paid:.2f}"
        )

        # Initial delta hedge
        await self._rebalance_delta()

    # ── delta rebalancing ─────────────────────────────────────────────

    async def _rebalance_delta(self) -> None:
        """Check portfolio delta and hedge with futures if needed."""
        if not self.in_position:
            return

        greeks = await self.ctx.get_portfolio_greeks()
        net_delta: float = getattr(greeks, "net_delta", 0.0)

        if abs(net_delta) < self.DELTA_THRESHOLD:
            return

        # Determine hedge quantity (in lots)
        # Each NIFTY lot has delta ~1.0 per lot-size movement
        hedge_lots = round(abs(net_delta))
        if hedge_lots < 1:
            hedge_lots = 1
        hedge_qty = hedge_lots * self.LOT_SIZE

        if net_delta > 0:
            # Portfolio is long delta — sell futures to neutralise
            await self.ctx.place_order(
                symbol=self.FUTURES_SYMBOL, side=OrderSide.SELL,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=hedge_qty,
            )
            self.futures_position -= hedge_qty
        else:
            # Portfolio is short delta — buy futures
            await self.ctx.place_order(
                symbol=self.FUTURES_SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=hedge_qty,
            )
            self.futures_position += hedge_qty

        ltp_map = await self.ctx.get_ltp([self.FUTURES_SYMBOL])
        fut_price = float(ltp_map.get(self.FUTURES_SYMBOL, 0.0))
        signed_qty = -hedge_qty if net_delta > 0 else hedge_qty
        self.futures_entries.append((signed_qty, fut_price))

        self.rebalance_count += 1
        self.ctx.log.info(
            f"Delta rebalanced #{self.rebalance_count} | net_delta={net_delta:.3f} "
            f"| hedge_qty={hedge_qty} | fut_pos={self.futures_position}"
        )

    # ── tick handler — monitor and rebalance ──────────────────────────

    async def on_tick(self, tick: Tick) -> None:
        if not self.in_position:
            return

        # Rebalance delta
        await self._rebalance_delta()

        # Check loss cap
        pnl = await self.ctx.get_pnl()
        total_pnl: float = float(getattr(pnl, "total_pnl", 0.0))
        max_loss = self.MAX_LOSS_PER_LOT * self.NUM_LOTS

        if total_pnl < -max_loss and self.rebalance_count >= self.MIN_REBALANCES:
            await self.ctx.square_off_all()
            self.in_position = False
            await self.ctx.alert(
                f"Gamma scalp loss cap hit | PnL={total_pnl:.2f} "
                f"| Rebalances={self.rebalance_count}",
                level="WARNING",
            )

    # ── time exit ─────────────────────────────────────────────────────

    async def _time_exit(self) -> None:
        if not self.in_position:
            return
        await self.ctx.square_off_all()
        self.in_position = False

        pnl = await self.ctx.get_pnl()
        total_pnl = float(getattr(pnl, "total_pnl", 0.0))
        await self.ctx.alert(
            f"Gamma scalp time-exit | PnL={total_pnl:.2f} "
            f"| Rebalances={self.rebalance_count}"
        )
