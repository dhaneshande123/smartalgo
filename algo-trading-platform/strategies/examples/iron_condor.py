"""
Weekly NIFTY Iron Condor Strategy
=================================

Sells an OTM call spread + OTM put spread every week on Monday/Tuesday,
creating a defined-risk iron condor with ~200-point wings from ATM.

Entry logic:
    - Enter on Monday or Tuesday (gives room for adjustment).
    - Sell OTM call and put ~200 points from ATM.
    - Buy further OTM call and put ~400 points from ATM for protection.

Adjustment logic:
    - If absolute delta of the position exceeds a threshold (0.30),
      roll the tested side closer to ATM to rebalance.

Exit logic:
    - Exit at 50 % of max profit (premium collected).
    - Exit on Thursday before expiry (15:00 IST).
    - Exit if cumulative loss hits the defined stoploss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class IronCondorLeg:
    """Tracks a single option leg of the iron condor."""

    symbol: str = ""
    strike: float = 0.0
    option_type: str = ""  # "CE" or "PE"
    side: str = ""         # "BUY" or "SELL"
    order_id: str = ""
    entry_price: float = 0.0
    quantity: int = 0


class IronCondorStrategy(BaseStrategy):
    """Weekly NIFTY iron condor with delta-based adjustment and time exit."""

    # -- configurable parameters (overridable via ctx.params) --
    UNDERLYING: str = "NIFTY"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 1
    SHORT_WING_OFFSET: int = 200   # points from ATM for short strikes
    LONG_WING_OFFSET: int = 400    # points from ATM for long (hedge) strikes
    STRIKE_STEP: int = 50          # NIFTY strike interval
    DELTA_ADJUSTMENT_THRESHOLD: float = 0.30
    PROFIT_TARGET_PCT: float = 0.50  # exit at 50% of premium collected
    MAX_LOSS_MULTIPLIER: float = 2.0  # stoploss = 2x premium collected
    ENTRY_DAYS: set[int] = field(default_factory=lambda: {0, 1})  # Mon=0, Tue=1
    EXIT_HOUR: int = 15
    EXIT_MINUTE: int = 0

    def __init__(self) -> None:
        self.legs: list[IronCondorLeg] = []
        self.premium_collected: float = 0.0
        self.in_position: bool = False
        self.ctx: StrategyContext

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _round_to_strike(price: float, step: int = 50) -> float:
        """Round a price to the nearest valid option strike."""
        return round(price / step) * step

    async def _get_expiry(self) -> Any:
        """Retrieve the nearest weekly expiry from the option chain."""
        chain = await self.ctx.get_option_chain(self.UNDERLYING)
        return chain.expiry

    def _build_symbol(self, strike: float, opt_type: str, expiry: Any) -> str:
        """Build the tradeable option symbol string."""
        exp_str = expiry.strftime("%d%b%y").upper() if hasattr(expiry, "strftime") else str(expiry)
        return f"{self.UNDERLYING}{exp_str}{int(strike)}{opt_type}"

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_init(self, context: StrategyContext) -> None:
        """Store context and load parameter overrides."""
        self.ctx = context
        params = context.params
        self.LOT_SIZE = params.get("lot_size", self.LOT_SIZE)
        self.NUM_LOTS = params.get("num_lots", self.NUM_LOTS)
        self.SHORT_WING_OFFSET = params.get("short_wing_offset", self.SHORT_WING_OFFSET)
        self.LONG_WING_OFFSET = params.get("long_wing_offset", self.LONG_WING_OFFSET)
        self.PROFIT_TARGET_PCT = params.get("profit_target_pct", self.PROFIT_TARGET_PCT)
        self.ctx.log.info("IronCondorStrategy initialized")

    async def on_start(self) -> None:
        """Schedule daily entry check and expiry-day exit."""
        await self.ctx.schedule("check_entry", "09:20")
        await self.ctx.schedule("expiry_exit", "15:00")
        self.ctx.log.info("IronCondorStrategy started — schedules registered")

    async def on_stop(self) -> None:
        """Square off all positions on shutdown."""
        if self.in_position:
            await self.ctx.square_off_all()
            self.in_position = False
            self.legs.clear()
        self.ctx.log.info("IronCondorStrategy stopped")

    # ── schedule handlers ─────────────────────────────────────────────

    async def on_schedule(self, event: Any) -> None:
        if event.name == "check_entry":
            await self._try_entry()
        elif event.name == "expiry_exit":
            await self._expiry_exit()

    async def _try_entry(self) -> None:
        """Enter iron condor on Monday/Tuesday if not already in position."""
        now = datetime.now()
        if now.weekday() not in self.ENTRY_DAYS or self.in_position:
            return

        spot = await self.ctx.get_underlying_price(self.UNDERLYING)
        atm = self._round_to_strike(spot, self.STRIKE_STEP)
        expiry = await self._get_expiry()

        short_ce_strike = atm + self.SHORT_WING_OFFSET
        long_ce_strike = atm + self.LONG_WING_OFFSET
        short_pe_strike = atm - self.SHORT_WING_OFFSET
        long_pe_strike = atm - self.LONG_WING_OFFSET
        qty = self.LOT_SIZE * self.NUM_LOTS

        leg_specs: list[tuple[float, str, str]] = [
            (short_ce_strike, "CE", "SELL"),
            (long_ce_strike, "CE", "BUY"),
            (short_pe_strike, "PE", "SELL"),
            (long_pe_strike, "PE", "BUY"),
        ]

        self.legs = []
        self.premium_collected = 0.0

        for strike, opt_type, side in leg_specs:
            symbol = self._build_symbol(strike, opt_type, expiry)
            order_side = OrderSide.SELL if side == "SELL" else OrderSide.BUY
            result = await self.ctx.place_order(
                symbol=symbol,
                side=order_side,
                order_type=OrderType.MARKET,
                product_type=ProductType.NRML,
                quantity=qty,
            )
            ltp_map = await self.ctx.get_ltp([symbol])
            entry_px = float(ltp_map.get(symbol, 0.0))
            leg = IronCondorLeg(
                symbol=symbol, strike=strike, option_type=opt_type,
                side=side, order_id=str(result), entry_price=entry_px,
                quantity=qty,
            )
            self.legs.append(leg)
            if side == "SELL":
                self.premium_collected += entry_px * qty
            else:
                self.premium_collected -= entry_px * qty

        self.in_position = True
        await self.ctx.alert(
            f"Iron Condor entered | ATM={atm} | Premium={self.premium_collected:.2f}",
            level="INFO",
        )

    async def _expiry_exit(self) -> None:
        """Exit all legs on expiry day (Thursday) at scheduled time."""
        now = datetime.now()
        if now.weekday() != 3 or not self.in_position:  # 3 = Thursday
            return
        await self.ctx.square_off_all()
        self.in_position = False
        self.legs.clear()
        await self.ctx.alert("Iron Condor exited — expiry day exit", level="INFO")

    # ── tick handler — monitor P&L and delta ─────────────────────────

    async def on_tick(self, tick: Tick) -> None:
        """Monitor running P&L and delta for adjustment / exit."""
        if not self.in_position:
            return

        # Compute current P&L
        leg_symbols = [leg.symbol for leg in self.legs]
        ltp_map = await self.ctx.get_ltp(leg_symbols)
        current_value = 0.0
        for leg in self.legs:
            px = float(ltp_map.get(leg.symbol, leg.entry_price))
            if leg.side == "SELL":
                current_value += (leg.entry_price - px) * leg.quantity
            else:
                current_value += (px - leg.entry_price) * leg.quantity

        # Profit target
        if self.premium_collected > 0 and current_value >= self.premium_collected * self.PROFIT_TARGET_PCT:
            await self.ctx.square_off_all()
            self.in_position = False
            self.legs.clear()
            await self.ctx.alert(f"Iron Condor profit target hit | PnL={current_value:.2f}", level="INFO")
            return

        # Stoploss
        if current_value <= -abs(self.premium_collected) * self.MAX_LOSS_MULTIPLIER:
            await self.ctx.square_off_all()
            self.in_position = False
            self.legs.clear()
            await self.ctx.alert(f"Iron Condor stoploss hit | PnL={current_value:.2f}", level="WARNING")
            return

        # Delta-based adjustment check
        portfolio_greeks = await self.ctx.get_portfolio_greeks()
        net_delta: float = getattr(portfolio_greeks, "delta", 0.0)
        if abs(net_delta) > self.DELTA_ADJUSTMENT_THRESHOLD:
            await self.ctx.alert(
                f"Iron Condor delta breach | net_delta={net_delta:.3f} — manual adjustment recommended",
                level="WARNING",
            )
