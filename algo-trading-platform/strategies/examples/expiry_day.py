"""
Expiry Day Theta Decay Strategy
================================

Sells far OTM options on the weekly expiry day (Thursday for NIFTY,
Wednesday for BANKNIFTY) to capture accelerated theta decay.
Options lose premium rapidly on expiry day, especially OTM contracts,
making this a high-probability but risk-managed premium-selling play.

Entry logic:
    - At 09:20 IST on expiry day, sell OTM CE and OTM PE that are
      ~300 points away from the current spot.
    - Select strikes with premium between Rs 5 and Rs 25 (sweet spot
      for theta decay vs. risk).

Adjustment logic:
    - If spot moves within 100 points of a short strike, close that
      leg to limit loss.

Exit logic:
    - Let options expire worthless (premium → 0) if spot stays away.
    - Time exit at 15:15 IST to avoid settlement risks.
    - Individual leg stoploss: 2x the entry premium.
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


class ExpiryDayLeg:
    """Tracks one short option leg."""

    def __init__(self) -> None:
        self.symbol: str = ""
        self.strike: float = 0.0
        self.opt_type: str = ""   # "CE" or "PE"
        self.entry_premium: float = 0.0
        self.quantity: int = 0
        self.active: bool = False


class ExpiryDayStrategy(BaseStrategy):
    """Sells far-OTM options on expiry day to harvest theta decay."""

    UNDERLYING: str = "NIFTY"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 2
    STRIKE_STEP: int = 50
    OTM_OFFSET: int = 300          # points away from spot
    MIN_PREMIUM: float = 5.0       # minimum acceptable premium (Rs)
    MAX_PREMIUM: float = 25.0      # maximum acceptable premium (Rs)
    PROXIMITY_ALERT: int = 100     # close leg if spot within this distance
    LEG_SL_MULTIPLIER: float = 2.0 # stoploss = 2x entry premium per leg
    SQUARE_OFF_TIME: dt_time = dt_time(15, 15)
    # NIFTY expiry = Thursday (3), BANKNIFTY expiry = Wednesday (2)
    EXPIRY_WEEKDAY: int = 3

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.ce_leg: ExpiryDayLeg = ExpiryDayLeg()
        self.pe_leg: ExpiryDayLeg = ExpiryDayLeg()
        self.quantity: int = 0
        self.total_premium: float = 0.0

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
        if self.UNDERLYING == "BANKNIFTY":
            self.LOT_SIZE = 15
            self.STRIKE_STEP = 100
            self.OTM_OFFSET = 500
            self.EXPIRY_WEEKDAY = 2  # Wednesday
        self.NUM_LOTS = params.get("num_lots", self.NUM_LOTS)
        self.quantity = self.LOT_SIZE * self.NUM_LOTS
        self.ctx.log.info(f"ExpiryDayStrategy initialized for {self.UNDERLYING}")

    async def on_start(self) -> None:
        await self.ctx.schedule("entry", "09:20")
        await self.ctx.schedule("square_off", "15:15")
        self.ctx.log.info("ExpiryDayStrategy started")

    async def on_stop(self) -> None:
        await self._close_all_legs()

    async def on_schedule(self, event: Any) -> None:
        if event.name == "entry":
            await self._try_entry()
        elif event.name == "square_off":
            await self._time_exit()

    # ── entry ─────────────────────────────────────────────────────────

    async def _try_entry(self) -> None:
        """Enter short strangles on expiry day only."""
        now = datetime.now()
        if now.weekday() != self.EXPIRY_WEEKDAY:
            self.ctx.log.info("Not expiry day — skipping entry")
            return

        if self.ce_leg.active or self.pe_leg.active:
            return

        spot = await self.ctx.get_underlying_price(self.UNDERLYING)
        atm = self._round_to_strike(spot, self.STRIKE_STEP)
        chain = await self.ctx.get_option_chain(self.UNDERLYING)
        expiry = chain.expiry

        ce_strike = atm + self.OTM_OFFSET
        pe_strike = atm - self.OTM_OFFSET

        # Validate premiums are in the sweet spot
        ce_symbol = self._build_symbol(ce_strike, "CE", expiry)
        pe_symbol = self._build_symbol(pe_strike, "PE", expiry)
        ltp_map = await self.ctx.get_ltp([ce_symbol, pe_symbol])
        ce_premium = float(ltp_map.get(ce_symbol, 0.0))
        pe_premium = float(ltp_map.get(pe_symbol, 0.0))

        # Adjust strikes if premium not in range
        ce_strike, ce_premium, ce_symbol = await self._find_valid_strike(
            atm, "CE", expiry, direction=1
        )
        pe_strike, pe_premium, pe_symbol = await self._find_valid_strike(
            atm, "PE", expiry, direction=-1
        )

        if ce_premium == 0.0 and pe_premium == 0.0:
            self.ctx.log.warning("No suitable strikes found — skipping entry")
            return

        self.total_premium = 0.0

        # Sell CE leg
        if ce_premium > 0:
            await self.ctx.place_order(
                symbol=ce_symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                quantity=self.quantity,
            )
            self.ce_leg.symbol = ce_symbol
            self.ce_leg.strike = ce_strike
            self.ce_leg.opt_type = "CE"
            self.ce_leg.entry_premium = ce_premium
            self.ce_leg.quantity = self.quantity
            self.ce_leg.active = True
            self.total_premium += ce_premium * self.quantity

        # Sell PE leg
        if pe_premium > 0:
            await self.ctx.place_order(
                symbol=pe_symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                quantity=self.quantity,
            )
            self.pe_leg.symbol = pe_symbol
            self.pe_leg.strike = pe_strike
            self.pe_leg.opt_type = "PE"
            self.pe_leg.entry_premium = pe_premium
            self.pe_leg.quantity = self.quantity
            self.pe_leg.active = True
            self.total_premium += pe_premium * self.quantity

        await self.ctx.alert(
            f"ExpiryDay short strangle entered | CE={ce_strike} PE={pe_strike} "
            f"| Total premium={self.total_premium:.2f}"
        )

    async def _find_valid_strike(
        self, atm: float, opt_type: str, expiry: Any, direction: int
    ) -> tuple[float, float, str]:
        """Search outward from ATM for a strike with premium in range."""
        for offset in range(self.OTM_OFFSET, self.OTM_OFFSET + 500, self.STRIKE_STEP):
            strike = atm + direction * offset
            symbol = self._build_symbol(strike, opt_type, expiry)
            ltp_map = await self.ctx.get_ltp([symbol])
            premium = float(ltp_map.get(symbol, 0.0))
            if self.MIN_PREMIUM <= premium <= self.MAX_PREMIUM:
                return strike, premium, symbol
        return atm + direction * self.OTM_OFFSET, 0.0, ""

    # ── tick handler — monitor proximity and per-leg SL ───────────────

    async def on_tick(self, tick: Tick) -> None:
        if not self.ce_leg.active and not self.pe_leg.active:
            return

        spot = await self.ctx.get_underlying_price(self.UNDERLYING)

        # Check CE leg
        if self.ce_leg.active:
            distance_ce = self.ce_leg.strike - spot
            if distance_ce <= self.PROXIMITY_ALERT:
                await self._close_leg(self.ce_leg, "CE proximity breach")
            else:
                ltp_map = await self.ctx.get_ltp([self.ce_leg.symbol])
                ce_now = float(ltp_map.get(self.ce_leg.symbol, 0.0))
                if ce_now >= self.ce_leg.entry_premium * self.LEG_SL_MULTIPLIER:
                    await self._close_leg(self.ce_leg, "CE stoploss hit")

        # Check PE leg
        if self.pe_leg.active:
            distance_pe = spot - self.pe_leg.strike
            if distance_pe <= self.PROXIMITY_ALERT:
                await self._close_leg(self.pe_leg, "PE proximity breach")
            else:
                ltp_map = await self.ctx.get_ltp([self.pe_leg.symbol])
                pe_now = float(ltp_map.get(self.pe_leg.symbol, 0.0))
                if pe_now >= self.pe_leg.entry_premium * self.LEG_SL_MULTIPLIER:
                    await self._close_leg(self.pe_leg, "PE stoploss hit")

    async def _close_leg(self, leg: ExpiryDayLeg, reason: str) -> None:
        if not leg.active:
            return
        await self.ctx.place_order(
            symbol=leg.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=leg.quantity,
        )
        leg.active = False
        await self.ctx.alert(f"ExpiryDay leg closed | {reason}", level="WARNING")

    async def _close_all_legs(self) -> None:
        if self.ce_leg.active:
            await self._close_leg(self.ce_leg, "shutdown")
        if self.pe_leg.active:
            await self._close_leg(self.pe_leg, "shutdown")

    async def _time_exit(self) -> None:
        await self._close_all_legs()
        await self.ctx.alert("ExpiryDay time-exit — all legs closed")
