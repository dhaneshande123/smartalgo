"""
ATM Straddle Seller Strategy
=============================

Sells an ATM straddle on NIFTY or BANKNIFTY at market open, collecting
premium from both the call and put side.

Entry logic:
    - At 09:20 IST (after opening auction), sell ATM CE + ATM PE.

Adjustment logic:
    - If one leg goes deep ITM (> 1.5x entry premium), close that leg
      and re-enter at the new ATM strike.

Exit logic:
    - Stoploss at 30 % of total premium collected.
    - Time-based exit 30 minutes before market close (15:00 IST).
    - Exit if combined premium drops to 50 % of collected (profit target).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from core.models import (
    OptionType,
    OrderSide,
    OrderType,
    ProductType,
    Tick,
)
from strategies.base_strategy import BaseStrategy, StrategyContext


class StraddleSellerStrategy(BaseStrategy):
    """Sells ATM straddle at open, manages via SL and time-based exit."""

    UNDERLYING: str = "NIFTY"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 1
    STRIKE_STEP: int = 50
    SL_PCT: float = 0.30       # stoploss = 30% of premium collected
    PROFIT_TARGET_PCT: float = 0.50  # exit when premium decays 50%
    ITM_ADJUSTMENT_FACTOR: float = 1.5
    EXIT_HOUR: int = 15
    EXIT_MINUTE: int = 0

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.ce_symbol: str = ""
        self.pe_symbol: str = ""
        self.ce_entry: float = 0.0
        self.pe_entry: float = 0.0
        self.premium_collected: float = 0.0
        self.in_position: bool = False
        self.quantity: int = 0

    @staticmethod
    def _round_to_strike(price: float, step: int = 50) -> float:
        return round(price / step) * step

    def _build_symbol(self, strike: float, opt_type: str, expiry: Any) -> str:
        exp_str = expiry.strftime("%d%b%y").upper() if hasattr(expiry, "strftime") else str(expiry)
        return f"{self.UNDERLYING}{exp_str}{int(strike)}{opt_type}"

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_init(self, context: StrategyContext) -> None:
        self.ctx = context
        self.UNDERLYING = context.params.get("underlying", self.UNDERLYING)
        if self.UNDERLYING == "BANKNIFTY":
            self.LOT_SIZE = 15
            self.STRIKE_STEP = 100
        self.NUM_LOTS = context.params.get("num_lots", self.NUM_LOTS)
        self.quantity = self.LOT_SIZE * self.NUM_LOTS
        self.ctx.log.info(f"StraddleSellerStrategy initialized for {self.UNDERLYING}")

    async def on_start(self) -> None:
        await self.ctx.schedule("entry", "09:20")
        await self.ctx.schedule("time_exit", f"{self.EXIT_HOUR:02d}:{self.EXIT_MINUTE:02d}")

    async def on_stop(self) -> None:
        if self.in_position:
            await self.ctx.square_off_all()
            self.in_position = False

    # ── schedule handlers ─────────────────────────────────────────────

    async def on_schedule(self, event: Any) -> None:
        if event.name == "entry":
            await self._enter_straddle()
        elif event.name == "time_exit":
            await self._time_exit()

    async def _enter_straddle(self) -> None:
        if self.in_position:
            return

        spot = await self.ctx.get_underlying_price(self.UNDERLYING)
        atm = self._round_to_strike(spot, self.STRIKE_STEP)
        chain = await self.ctx.get_option_chain(self.UNDERLYING)
        expiry = chain.expiry

        self.ce_symbol = self._build_symbol(atm, "CE", expiry)
        self.pe_symbol = self._build_symbol(atm, "PE", expiry)

        for symbol in (self.ce_symbol, self.pe_symbol):
            await self.ctx.place_order(
                symbol=symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                quantity=self.quantity,
            )

        ltp_map = await self.ctx.get_ltp([self.ce_symbol, self.pe_symbol])
        self.ce_entry = float(ltp_map.get(self.ce_symbol, 0.0))
        self.pe_entry = float(ltp_map.get(self.pe_symbol, 0.0))
        self.premium_collected = (self.ce_entry + self.pe_entry) * self.quantity
        self.in_position = True

        await self.ctx.alert(
            f"Straddle sold | ATM={atm} | CE={self.ce_entry:.2f} PE={self.pe_entry:.2f} "
            f"| Total premium={self.premium_collected:.2f}",
        )

    async def _time_exit(self) -> None:
        if not self.in_position:
            return
        await self.ctx.square_off_all()
        self.in_position = False
        await self.ctx.alert("Straddle time-exit triggered (30 min before close)")

    # ── tick handler ──────────────────────────────────────────────────

    async def on_tick(self, tick: Tick) -> None:
        if not self.in_position:
            return

        ltp_map = await self.ctx.get_ltp([self.ce_symbol, self.pe_symbol])
        ce_now = float(ltp_map.get(self.ce_symbol, self.ce_entry))
        pe_now = float(ltp_map.get(self.pe_symbol, self.pe_entry))
        current_premium = (ce_now + pe_now) * self.quantity

        # Profit target — premium decayed by target %
        target_premium = self.premium_collected * (1.0 - self.PROFIT_TARGET_PCT)
        if current_premium <= target_premium:
            await self.ctx.square_off_all()
            self.in_position = False
            await self.ctx.alert("Straddle profit target hit")
            return

        # Stoploss — premium inflated beyond threshold
        sl_premium = self.premium_collected * (1.0 + self.SL_PCT)
        if current_premium >= sl_premium:
            await self.ctx.square_off_all()
            self.in_position = False
            await self.ctx.alert("Straddle stoploss hit", level="WARNING")
            return

        # Adjustment — if one leg is > 1.5x entry, roll to new ATM
        if ce_now > self.ce_entry * self.ITM_ADJUSTMENT_FACTOR:
            await self._adjust_leg("CE")
        elif pe_now > self.pe_entry * self.ITM_ADJUSTMENT_FACTOR:
            await self._adjust_leg("PE")

    async def _adjust_leg(self, leg_type: str) -> None:
        """Close the breached leg and re-sell at the new ATM strike."""
        old_symbol = self.ce_symbol if leg_type == "CE" else self.pe_symbol

        # Close old leg
        await self.ctx.place_order(
            symbol=old_symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=self.quantity,
        )

        # Re-enter at new ATM
        spot = await self.ctx.get_underlying_price(self.UNDERLYING)
        new_atm = self._round_to_strike(spot, self.STRIKE_STEP)
        chain = await self.ctx.get_option_chain(self.UNDERLYING)
        new_symbol = self._build_symbol(new_atm, leg_type, chain.expiry)

        await self.ctx.place_order(
            symbol=new_symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=self.quantity,
        )

        ltp_map = await self.ctx.get_ltp([new_symbol])
        new_entry = float(ltp_map.get(new_symbol, 0.0))

        if leg_type == "CE":
            self.ce_symbol = new_symbol
            self.ce_entry = new_entry
        else:
            self.pe_symbol = new_symbol
            self.pe_entry = new_entry

        await self.ctx.alert(f"Straddle {leg_type} leg adjusted to strike {new_atm}")
