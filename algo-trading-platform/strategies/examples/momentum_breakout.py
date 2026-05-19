"""
Opening Range Breakout (ORB) Strategy — Futures
================================================

Calculates the 15-minute opening range (09:15 -- 09:30 IST) and trades
the breakout direction using NIFTY futures.

Entry logic:
    - BUY if price breaks above the opening range high.
    - SELL (short) if price breaks below the opening range low.
    - Only one entry per direction per day.

Exit logic:
    - Trailing stoploss of 0.5 x range width.
    - Hard stoploss at opposite end of opening range.
    - Square off by 15:15 IST (intraday only).
"""

from __future__ import annotations

from datetime import datetime, time as dt_time
from decimal import Decimal
from typing import Any

from core.models import (
    OrderSide,
    OrderType,
    ProductType,
    Candle,
    TimeFrame,
)
from strategies.base_strategy import BaseStrategy, StrategyContext


class MomentumBreakoutStrategy(BaseStrategy):
    """15-min ORB breakout on NIFTY futures with trailing SL."""

    SYMBOL: str = "NIFTY-FUT"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 1
    ORB_START: dt_time = dt_time(9, 15)
    ORB_END: dt_time = dt_time(9, 30)
    SQUARE_OFF_TIME: dt_time = dt_time(15, 15)
    TRAIL_FACTOR: float = 0.5  # trailing SL = 0.5 x range width

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.range_high: float = 0.0
        self.range_low: float = float("inf")
        self.range_set: bool = False
        self.position_side: str | None = None  # "LONG" or "SHORT"
        self.entry_price: float = 0.0
        self.trailing_sl: float = 0.0
        self.best_price: float = 0.0  # tracks best price for trailing SL
        self.quantity: int = 0
        self.traded_long: bool = False
        self.traded_short: bool = False

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_init(self, context: StrategyContext) -> None:
        self.ctx = context
        self.SYMBOL = context.params.get("symbol", self.SYMBOL)
        self.NUM_LOTS = context.params.get("num_lots", self.NUM_LOTS)
        self.quantity = self.LOT_SIZE * self.NUM_LOTS
        self.ctx.log.info("MomentumBreakoutStrategy initialized")

    async def on_start(self) -> None:
        await self.ctx.schedule("square_off", "15:15")
        await self.ctx.schedule("reset_day", "09:14")
        self.ctx.log.info("MomentumBreakoutStrategy started")

    async def on_stop(self) -> None:
        if self.position_side:
            await self.ctx.square_off_all()
            self.position_side = None

    async def on_schedule(self, event: Any) -> None:
        if event.name == "square_off":
            await self._square_off()
        elif event.name == "reset_day":
            self._reset_day()

    def _reset_day(self) -> None:
        """Reset state for a new trading day."""
        self.range_high = 0.0
        self.range_low = float("inf")
        self.range_set = False
        self.position_side = None
        self.traded_long = False
        self.traded_short = False

    async def _square_off(self) -> None:
        if self.position_side:
            await self.ctx.square_off_all()
            pnl = self._calc_pnl()
            await self.ctx.alert(f"ORB day-end square off | PnL={pnl:.2f}")
            self.position_side = None

    def _calc_pnl(self) -> float:
        """Rough unrealized PnL based on best tracked price."""
        if self.position_side == "LONG":
            return (self.best_price - self.entry_price) * self.quantity
        elif self.position_side == "SHORT":
            return (self.entry_price - self.best_price) * self.quantity
        return 0.0

    # ── candle handler (5-min candles) ────────────────────────────────

    async def on_candle(self, candle: Candle) -> None:
        if candle.symbol != self.SYMBOL:
            return

        candle_time = candle.timestamp.time()
        close = float(candle.close)
        high = float(candle.high)
        low = float(candle.low)

        # Phase 1: Build opening range from 09:15 -- 09:30
        if not self.range_set:
            if self.ORB_START <= candle_time < self.ORB_END:
                self.range_high = max(self.range_high, high)
                self.range_low = min(self.range_low, low)
                return
            elif candle_time >= self.ORB_END and self.range_high > 0:
                self.range_set = True
                self.ctx.log.info(
                    f"ORB set | High={self.range_high:.2f} Low={self.range_low:.2f}"
                )
            else:
                return

        # Do not trade after square-off time
        if candle_time >= self.SQUARE_OFF_TIME:
            return

        range_width = self.range_high - self.range_low
        if range_width <= 0:
            return

        # Phase 2: Check for breakout entry
        if self.position_side is None:
            if close > self.range_high and not self.traded_long:
                await self._enter("LONG", close, range_width)
            elif close < self.range_low and not self.traded_short:
                await self._enter("SHORT", close, range_width)
            return

        # Phase 3: Manage open position — trailing SL
        if self.position_side == "LONG":
            self.best_price = max(self.best_price, high)
            self.trailing_sl = max(
                self.trailing_sl,
                self.best_price - range_width * self.TRAIL_FACTOR,
            )
            if low <= self.trailing_sl:
                await self._exit("Trailing SL hit (LONG)")
        elif self.position_side == "SHORT":
            self.best_price = min(self.best_price, low)
            self.trailing_sl = min(
                self.trailing_sl,
                self.best_price + range_width * self.TRAIL_FACTOR,
            )
            if high >= self.trailing_sl:
                await self._exit("Trailing SL hit (SHORT)")

    async def _enter(self, side: str, price: float, range_width: float) -> None:
        order_side = OrderSide.BUY if side == "LONG" else OrderSide.SELL
        await self.ctx.place_order(
            symbol=self.SYMBOL,
            side=order_side,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=self.quantity,
        )
        self.position_side = side
        self.entry_price = price
        self.best_price = price
        self.trailing_sl = (
            price - range_width * self.TRAIL_FACTOR
            if side == "LONG"
            else price + range_width * self.TRAIL_FACTOR
        )
        if side == "LONG":
            self.traded_long = True
        else:
            self.traded_short = True
        await self.ctx.alert(f"ORB {side} entry at {price:.2f} | TSL={self.trailing_sl:.2f}")

    async def _exit(self, reason: str) -> None:
        if not self.position_side:
            return
        close_side = OrderSide.SELL if self.position_side == "LONG" else OrderSide.BUY
        await self.ctx.place_order(
            symbol=self.SYMBOL,
            side=close_side,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=self.quantity,
        )
        pnl = self._calc_pnl()
        await self.ctx.alert(f"ORB exit | {reason} | PnL={pnl:.2f}")
        self.position_side = None
