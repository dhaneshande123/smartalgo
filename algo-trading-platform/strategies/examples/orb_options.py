"""
Opening Range Breakout Using Options (ORB Options)
====================================================

Uses the 15-minute opening range (09:15 -- 09:30 IST) to determine
direction, then buys options for defined-risk directional exposure.

Entry logic:
    - Calculate the 15-minute opening range from the underlying (NIFTY).
    - If price breaks above the range high, buy an ATM Call (CE).
    - If price breaks below the range low, buy an ATM Put (PE).
    - Options provide built-in risk management (max loss = premium paid).

Exit logic:
    - Target: 50% of premium paid (option doubles in value).
    - Stoploss: 50% of premium paid (option halves in value).
    - Intraday square off at 15:15 IST.
    - Only one trade per day in each direction.
"""

from __future__ import annotations

from datetime import time as dt_time
from decimal import Decimal
from typing import Any

from core.models import (
    Candle,
    OrderSide,
    OrderType,
    ProductType,
    Tick,
)
from strategies.base_strategy import BaseStrategy, StrategyContext


class ORBOptionsStrategy(BaseStrategy):
    """15-min ORB on NIFTY underlying, traded via ATM options for defined risk."""

    UNDERLYING: str = "NIFTY"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 1
    STRIKE_STEP: int = 50
    ORB_START: dt_time = dt_time(9, 15)
    ORB_END: dt_time = dt_time(9, 30)
    SQUARE_OFF_TIME: dt_time = dt_time(15, 15)
    TARGET_PCT: float = 1.0    # exit when option premium doubles (100% gain)
    SL_PCT: float = 0.50       # exit when premium falls 50%

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.quantity: int = 0
        # Opening range state
        self.range_high: float = 0.0
        self.range_low: float = float("inf")
        self.range_set: bool = False
        # Position state
        self.in_position: bool = False
        self.option_symbol: str = ""
        self.entry_premium: float = 0.0
        self.position_type: str = ""  # "CE" or "PE"
        # Day tracking
        self.traded_long: bool = False
        self.traded_short: bool = False

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
        self.TARGET_PCT = params.get("target_pct", self.TARGET_PCT)
        self.SL_PCT = params.get("sl_pct", self.SL_PCT)
        if self.UNDERLYING == "BANKNIFTY":
            self.LOT_SIZE = 15
            self.STRIKE_STEP = 100
        self.quantity = self.LOT_SIZE * self.NUM_LOTS
        self.ctx.log.info(f"ORBOptionsStrategy initialized for {self.UNDERLYING}")

    async def on_start(self) -> None:
        await self.ctx.schedule("square_off", "15:15")
        await self.ctx.schedule("reset_day", "09:14")
        self.ctx.log.info("ORBOptionsStrategy started")

    async def on_stop(self) -> None:
        if self.in_position:
            await self._exit_position("strategy stopped")

    async def on_schedule(self, event: Any) -> None:
        if event.name == "square_off":
            if self.in_position:
                await self._exit_position("time-based square off")
        elif event.name == "reset_day":
            self._reset_day()

    def _reset_day(self) -> None:
        """Reset all intraday state for a new session."""
        self.range_high = 0.0
        self.range_low = float("inf")
        self.range_set = False
        self.in_position = False
        self.option_symbol = ""
        self.entry_premium = 0.0
        self.position_type = ""
        self.traded_long = False
        self.traded_short = False

    # ── candle handler (5-min candles on underlying) ──────────────────

    async def on_candle(self, candle: Candle) -> None:
        """Build opening range from underlying candles, then trade breakout."""
        if candle.symbol != self.UNDERLYING:
            return

        candle_time = candle.timestamp.time()
        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)

        # Phase 1: Build the opening range (09:15 -- 09:30)
        if not self.range_set:
            if self.ORB_START <= candle_time < self.ORB_END:
                self.range_high = max(self.range_high, high)
                self.range_low = min(self.range_low, low)
                return
            elif candle_time >= self.ORB_END and self.range_high > 0:
                self.range_set = True
                self.ctx.log.info(
                    f"ORB range set | High={self.range_high:.2f} Low={self.range_low:.2f}"
                )
            else:
                return

        if candle_time >= self.SQUARE_OFF_TIME:
            return

        # Phase 2: Detect breakout (only if not already in a position)
        if self.in_position:
            return

        if close > self.range_high and not self.traded_long:
            await self._enter_option("CE", close)
            self.traded_long = True
        elif close < self.range_low and not self.traded_short:
            await self._enter_option("PE", close)
            self.traded_short = True

    async def _enter_option(self, opt_type: str, spot: float) -> None:
        """Buy ATM option of the given type."""
        atm = self._round_to_strike(spot, self.STRIKE_STEP)
        chain = await self.ctx.get_option_chain(self.UNDERLYING)
        expiry = chain.expiry
        symbol = self._build_symbol(atm, opt_type, expiry)

        await self.ctx.place_order(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=self.quantity,
        )

        ltp_map = await self.ctx.get_ltp([symbol])
        self.entry_premium = float(ltp_map.get(symbol, 0.0))
        self.option_symbol = symbol
        self.position_type = opt_type
        self.in_position = True

        await self.ctx.alert(
            f"ORB Options {opt_type} bought | ATM={atm} | Premium={self.entry_premium:.2f}"
        )

    async def _exit_position(self, reason: str) -> None:
        """Sell the option to close the position."""
        if not self.in_position:
            return
        await self.ctx.place_order(
            symbol=self.option_symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=self.quantity,
        )
        ltp_map = await self.ctx.get_ltp([self.option_symbol])
        exit_px = float(ltp_map.get(self.option_symbol, 0.0))
        pnl = (exit_px - self.entry_premium) * self.quantity
        await self.ctx.alert(
            f"ORB Options exit ({self.position_type}) | {reason} "
            f"| Entry={self.entry_premium:.2f} Exit={exit_px:.2f} PnL={pnl:.2f}"
        )
        self.in_position = False

    # ── tick handler — target and stoploss monitoring ─────────────────

    async def on_tick(self, tick: Tick) -> None:
        """Monitor option premium for target and stoploss."""
        if not self.in_position:
            return

        ltp_map = await self.ctx.get_ltp([self.option_symbol])
        current = float(ltp_map.get(self.option_symbol, self.entry_premium))

        if self.entry_premium <= 0:
            return

        pct_change = (current - self.entry_premium) / self.entry_premium

        # Target hit — premium has gained TARGET_PCT
        if pct_change >= self.TARGET_PCT:
            await self._exit_position(f"Target hit (+{pct_change:.0%})")
            return

        # Stoploss hit — premium has dropped SL_PCT
        if pct_change <= -self.SL_PCT:
            await self._exit_position(f"Stoploss hit ({pct_change:.0%})")
