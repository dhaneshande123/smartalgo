"""
VWAP-Based Intraday Scalping Strategy
======================================

Uses the Volume Weighted Average Price (VWAP) as the anchor for
intraday scalp trades on NIFTY futures.  Entries are taken on
pullbacks to VWAP with confirmation from tick momentum.

Entry logic:
    - Track cumulative VWAP from market open (09:15 IST).
    - BUY when price pulls back to VWAP from above and bounces
      (candle close > VWAP after touching it, within 0.1 % band).
    - SELL when price rallies to VWAP from below and reverses
      (candle close < VWAP after touching it).
    - Confirmation: volume on the signal candle must be above
      1.5x the rolling 10-candle average volume.

Exit logic:
    - Fixed point target (15 points on NIFTY).
    - Fixed point stoploss (10 points on NIFTY).
    - Time-based exit at 15:15 IST.
    - Maximum 5 trades per day to prevent over-trading.
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
)
from strategies.base_strategy import BaseStrategy, StrategyContext


class VWAPScalperStrategy(BaseStrategy):
    """VWAP pullback scalper on NIFTY futures (intraday)."""

    SYMBOL: str = "NIFTY-FUT"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 1
    TARGET_POINTS: float = 15.0
    SL_POINTS: float = 10.0
    VWAP_BAND_PCT: float = 0.001     # 0.1 % proximity band around VWAP
    VOLUME_MULTIPLIER: float = 1.5   # signal candle volume threshold
    VOL_LOOKBACK: int = 10           # rolling volume average window
    MAX_TRADES_PER_DAY: int = 5
    SQUARE_OFF_TIME: dt_time = dt_time(15, 15)
    MARKET_OPEN: dt_time = dt_time(9, 15)

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.quantity: int = 0
        # VWAP tracking
        self.cum_volume: int = 0
        self.cum_tp_volume: float = 0.0  # cumulative (typical_price * volume)
        self.vwap: float = 0.0
        # Volume tracking
        self.recent_volumes: list[int] = []
        # Position state
        self.position_side: str | None = None
        self.entry_price: float = 0.0
        self.trade_count: int = 0
        self.prev_close: float = 0.0

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_init(self, context: StrategyContext) -> None:
        self.ctx = context
        params = context.params
        self.SYMBOL = params.get("symbol", self.SYMBOL)
        self.NUM_LOTS = params.get("num_lots", self.NUM_LOTS)
        self.TARGET_POINTS = params.get("target_points", self.TARGET_POINTS)
        self.SL_POINTS = params.get("sl_points", self.SL_POINTS)
        self.quantity = self.LOT_SIZE * self.NUM_LOTS
        self.ctx.log.info("VWAPScalperStrategy initialized")

    async def on_start(self) -> None:
        await self.ctx.schedule("square_off", "15:15")
        await self.ctx.schedule("reset_day", "09:14")
        self.ctx.log.info("VWAPScalperStrategy started")

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
        """Reset all intraday state for a new session."""
        self.cum_volume = 0
        self.cum_tp_volume = 0.0
        self.vwap = 0.0
        self.recent_volumes.clear()
        self.position_side = None
        self.entry_price = 0.0
        self.trade_count = 0
        self.prev_close = 0.0

    async def _square_off(self) -> None:
        if self.position_side:
            await self.ctx.square_off_all()
            await self.ctx.alert("VWAP Scalper day-end square off")
            self.position_side = None

    # ── candle handler (1-min candles) ────────────────────────────────

    async def on_candle(self, candle: Candle) -> None:
        if candle.symbol != self.SYMBOL:
            return

        candle_time = candle.timestamp.time()
        if candle_time < self.MARKET_OPEN or candle_time >= self.SQUARE_OFF_TIME:
            return

        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)
        volume = candle.volume

        # Update VWAP
        typical_price = (high + low + close) / 3.0
        self.cum_tp_volume += typical_price * volume
        self.cum_volume += volume
        self.vwap = self.cum_tp_volume / self.cum_volume if self.cum_volume > 0 else close

        # Update rolling volume
        self.recent_volumes.append(volume)
        if len(self.recent_volumes) > self.VOL_LOOKBACK:
            self.recent_volumes = self.recent_volumes[-self.VOL_LOOKBACK:]

        avg_vol = (
            sum(self.recent_volumes) / len(self.recent_volumes)
            if self.recent_volumes
            else 0
        )

        # -- Manage existing position --
        if self.position_side:
            self._check_exit(close)
            self.prev_close = close
            return

        # -- Check for new entry signals --
        if self.trade_count >= self.MAX_TRADES_PER_DAY:
            self.prev_close = close
            return

        if self.prev_close == 0.0:
            self.prev_close = close
            return

        vwap_band = self.vwap * self.VWAP_BAND_PCT
        price_near_vwap = abs(low - self.vwap) <= vwap_band or abs(high - self.vwap) <= vwap_band
        volume_confirmed = avg_vol > 0 and volume >= avg_vol * self.VOLUME_MULTIPLIER

        if price_near_vwap and volume_confirmed:
            # Bullish bounce: previous close above VWAP, dipped to VWAP, closed above
            if self.prev_close > self.vwap and close > self.vwap and low <= self.vwap + vwap_band:
                await self._enter("LONG", close)
            # Bearish rejection: previous close below VWAP, rallied to VWAP, closed below
            elif self.prev_close < self.vwap and close < self.vwap and high >= self.vwap - vwap_band:
                await self._enter("SHORT", close)

        self.prev_close = close

    def _check_exit(self, current_price: float) -> None:
        """Mark position for exit on target or stoploss (executed async)."""
        if self.position_side == "LONG":
            if current_price >= self.entry_price + self.TARGET_POINTS:
                self._pending_exit = "Target hit (LONG)"
            elif current_price <= self.entry_price - self.SL_POINTS:
                self._pending_exit = "Stoploss hit (LONG)"
        elif self.position_side == "SHORT":
            if current_price <= self.entry_price - self.TARGET_POINTS:
                self._pending_exit = "Target hit (SHORT)"
            elif current_price >= self.entry_price + self.SL_POINTS:
                self._pending_exit = "Stoploss hit (SHORT)"

    async def _enter(self, side: str, price: float) -> None:
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
        self.trade_count += 1
        self._pending_exit: str | None = None
        await self.ctx.alert(
            f"VWAP Scalper {side} entry at {price:.2f} | VWAP={self.vwap:.2f} "
            f"| Trade #{self.trade_count}"
        )

    # ── tick handler — exit execution ─────────────────────────────────

    async def on_tick(self, tick: Any) -> None:
        """Execute pending exits identified in on_candle."""
        if not self.position_side:
            return

        ltp_map = await self.ctx.get_ltp([self.SYMBOL])
        current = float(ltp_map.get(self.SYMBOL, self.entry_price))

        reason: str | None = getattr(self, "_pending_exit", None)
        if reason is None:
            # Also check real-time for stoploss
            if self.position_side == "LONG" and current <= self.entry_price - self.SL_POINTS:
                reason = "Stoploss hit (LONG) — tick"
            elif self.position_side == "SHORT" and current >= self.entry_price + self.SL_POINTS:
                reason = "Stoploss hit (SHORT) — tick"
            elif self.position_side == "LONG" and current >= self.entry_price + self.TARGET_POINTS:
                reason = "Target hit (LONG) — tick"
            elif self.position_side == "SHORT" and current <= self.entry_price - self.TARGET_POINTS:
                reason = "Target hit (SHORT) — tick"

        if reason:
            close_side = OrderSide.SELL if self.position_side == "LONG" else OrderSide.BUY
            await self.ctx.place_order(
                symbol=self.SYMBOL,
                side=close_side,
                order_type=OrderType.MARKET,
                product_type=ProductType.MIS,
                quantity=self.quantity,
            )
            pnl = (
                (current - self.entry_price) * self.quantity
                if self.position_side == "LONG"
                else (self.entry_price - current) * self.quantity
            )
            await self.ctx.alert(f"VWAP Scalper exit | {reason} | PnL={pnl:.2f}")
            self.position_side = None
            self._pending_exit = None
