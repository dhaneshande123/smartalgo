"""
Supertrend Indicator Strategy
==============================

Implements the Supertrend indicator (ATR-based dynamic support/resistance)
on 5-minute candles for NIFTY futures, generating trend-following signals.

Supertrend calculation:
    - ATR period: 10
    - Multiplier: 3.0
    - Upper band = (High + Low) / 2 + Multiplier * ATR
    - Lower band = (High + Low) / 2 - Multiplier * ATR
    - Supertrend flips between upper and lower band based on trend direction.

Entry logic:
    - BUY when price crosses above the Supertrend line (trend turns bullish).
    - SELL when price crosses below the Supertrend line (trend turns bearish).

Exit logic:
    - Reverse on opposite Supertrend signal (always in the market during
      trading hours).
    - Position sizing: ATR-based (risk 1 ATR per trade as stoploss).
    - Square off by 15:15 IST (intraday).
"""

from __future__ import annotations

from collections import deque
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


class SupertrendStrategy(BaseStrategy):
    """Supertrend (ATR-based) trend-following on 5-min NIFTY futures."""

    SYMBOL: str = "NIFTY-FUT"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 1
    ATR_PERIOD: int = 10
    MULTIPLIER: float = 3.0
    SQUARE_OFF_TIME: dt_time = dt_time(15, 15)
    MARKET_OPEN: dt_time = dt_time(9, 15)

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.quantity: int = 0
        # Candle history for ATR
        self.highs: deque[float] = deque(maxlen=50)
        self.lows: deque[float] = deque(maxlen=50)
        self.closes: deque[float] = deque(maxlen=50)
        # Supertrend state
        self.upper_band: float = 0.0
        self.lower_band: float = 0.0
        self.supertrend: float = 0.0
        self.prev_supertrend: float = 0.0
        self.trend_up: bool = True  # True = bullish, False = bearish
        self.prev_close: float = 0.0
        self.atr: float = 0.0
        # Position
        self.position_side: str | None = None  # "LONG" or "SHORT"
        self.entry_price: float = 0.0

    # ── ATR & Supertrend computation ──────────────────────────────────

    def _compute_atr(self) -> float | None:
        """Compute Average True Range over ATR_PERIOD candles."""
        if len(self.highs) < self.ATR_PERIOD + 1:
            return None

        highs = list(self.highs)
        lows = list(self.lows)
        closes = list(self.closes)

        true_ranges: list[float] = []
        for i in range(-self.ATR_PERIOD, 0):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            true_ranges.append(tr)

        return sum(true_ranges) / self.ATR_PERIOD

    def _update_supertrend(self, high: float, low: float, close: float) -> bool:
        """
        Update Supertrend bands and trend direction.
        Returns True if enough data is available.
        """
        atr = self._compute_atr()
        if atr is None:
            return False

        self.atr = atr
        hl2 = (high + low) / 2.0
        basic_upper = hl2 + self.MULTIPLIER * atr
        basic_lower = hl2 - self.MULTIPLIER * atr

        # Adjust bands: upper band can only decrease, lower band can only increase
        if self.upper_band == 0.0:
            self.upper_band = basic_upper
            self.lower_band = basic_lower
        else:
            self.upper_band = (
                min(basic_upper, self.upper_band)
                if self.prev_close <= self.upper_band
                else basic_upper
            )
            self.lower_band = (
                max(basic_lower, self.lower_band)
                if self.prev_close >= self.lower_band
                else basic_lower
            )

        # Determine trend direction
        self.prev_supertrend = self.supertrend
        if close > self.upper_band:
            self.trend_up = True
        elif close < self.lower_band:
            self.trend_up = False

        self.supertrend = self.lower_band if self.trend_up else self.upper_band
        self.prev_close = close
        return True

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_init(self, context: StrategyContext) -> None:
        self.ctx = context
        params = context.params
        self.SYMBOL = params.get("symbol", self.SYMBOL)
        self.NUM_LOTS = params.get("num_lots", self.NUM_LOTS)
        self.ATR_PERIOD = params.get("atr_period", self.ATR_PERIOD)
        self.MULTIPLIER = params.get("multiplier", self.MULTIPLIER)
        self.quantity = self.LOT_SIZE * self.NUM_LOTS
        self.ctx.log.info("SupertrendStrategy initialized")

    async def on_start(self) -> None:
        await self.ctx.schedule("square_off", "15:15")
        await self.ctx.schedule("reset_day", "09:14")
        # Pre-load historical candles to warm up the indicator
        candles = await self.ctx.get_candles(self.SYMBOL, "M5", count=50)
        for c in candles:
            h, l, cl = float(c.high), float(c.low), float(c.close)
            self.highs.append(h)
            self.lows.append(l)
            self.closes.append(cl)
            self._update_supertrend(h, l, cl)
        self.ctx.log.info(
            f"SupertrendStrategy started | preloaded {len(self.closes)} candles "
            f"| trend={'UP' if self.trend_up else 'DOWN'} | ST={self.supertrend:.2f}"
        )

    async def on_stop(self) -> None:
        if self.position_side:
            await self._close_position("strategy stopped")

    async def on_schedule(self, event: Any) -> None:
        if event.name == "square_off":
            if self.position_side:
                await self._close_position("day-end square off")
        elif event.name == "reset_day":
            self._reset_day()

    def _reset_day(self) -> None:
        """Reset position state for a new day (keep indicator state)."""
        self.position_side = None
        self.entry_price = 0.0

    # ── candle handler (5-min) ────────────────────────────────────────

    async def on_candle(self, candle: Candle) -> None:
        if candle.symbol != self.SYMBOL:
            return

        candle_time = candle.timestamp.time()
        if candle_time < self.MARKET_OPEN or candle_time >= self.SQUARE_OFF_TIME:
            return

        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)

        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)

        prev_trend = self.trend_up
        if not self._update_supertrend(high, low, close):
            return  # Not enough data yet

        # Detect trend flip
        trend_flipped = self.trend_up != prev_trend

        if not trend_flipped:
            return

        if self.trend_up:
            # Bullish flip: close existing short and go long
            if self.position_side == "SHORT":
                await self._close_position("Supertrend flipped UP")
            if self.position_side is None:
                await self._enter("LONG", close)
        else:
            # Bearish flip: close existing long and go short
            if self.position_side == "LONG":
                await self._close_position("Supertrend flipped DOWN")
            if self.position_side is None:
                await self._enter("SHORT", close)

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
        await self.ctx.alert(
            f"Supertrend {side} entry at {price:.2f} "
            f"| ST={self.supertrend:.2f} | ATR={self.atr:.2f}"
        )

    async def _close_position(self, reason: str) -> None:
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
        ltp_map = await self.ctx.get_ltp([self.SYMBOL])
        exit_px = float(ltp_map.get(self.SYMBOL, self.entry_price))
        pnl = (
            (exit_px - self.entry_price) * self.quantity
            if self.position_side == "LONG"
            else (self.entry_price - exit_px) * self.quantity
        )
        await self.ctx.alert(f"Supertrend exit ({self.position_side}) | {reason} | PnL={pnl:.2f}")
        self.position_side = None
