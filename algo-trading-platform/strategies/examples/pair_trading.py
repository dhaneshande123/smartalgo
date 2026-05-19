"""
Statistical Arbitrage Pair Trading Strategy
=============================================

Trades a co-integrated pair of NSE stocks (e.g., SBIN vs ICICIBANK)
by monitoring the price ratio (spread) and entering when it deviates
significantly from its historical mean.

Pair selection:
    - Default pair: SBIN / ICICIBANK (public-sector vs private-sector bank).
    - Can be configured to any co-integrated pair via params.

Entry logic:
    - Compute the rolling Z-score of the price ratio (stock_a / stock_b).
    - LONG spread (buy A, sell B) when Z-score < -2.0 (A is cheap vs B).
    - SHORT spread (sell A, buy B) when Z-score > +2.0 (A is rich vs B).

Exit logic:
    - Exit when Z-score reverts to 0 (mean reversion target).
    - Stoploss when Z-score exceeds +/- 3.0 (divergence widening).
    - Time-based exit at 15:15 IST for intraday mode.
"""

from __future__ import annotations

import statistics
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


class PairTradingStrategy(BaseStrategy):
    """Statistical arbitrage pair trading on correlated NSE stocks."""

    # Configurable parameters
    STOCK_A: str = "SBIN"
    STOCK_B: str = "ICICIBANK"
    LOT_A: int = 1500        # quantity for stock A (approx equal notional)
    LOT_B: int = 500         # quantity for stock B
    ZSCORE_ENTRY: float = 2.0
    ZSCORE_EXIT: float = 0.0
    ZSCORE_STOPLOSS: float = 3.0
    LOOKBACK: int = 60       # number of candles for rolling stats
    SQUARE_OFF_TIME: dt_time = dt_time(15, 15)

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.ratios: deque[float] = deque(maxlen=100)
        self.position_side: str | None = None  # "LONG_SPREAD" or "SHORT_SPREAD"
        self.entry_zscore: float = 0.0
        self.price_a: float = 0.0
        self.price_b: float = 0.0
        self.candle_count_a: int = 0
        self.candle_count_b: int = 0
        self.latest_a: float = 0.0
        self.latest_b: float = 0.0

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_init(self, context: StrategyContext) -> None:
        self.ctx = context
        params = context.params
        self.STOCK_A = params.get("stock_a", self.STOCK_A)
        self.STOCK_B = params.get("stock_b", self.STOCK_B)
        self.LOT_A = params.get("lot_a", self.LOT_A)
        self.LOT_B = params.get("lot_b", self.LOT_B)
        self.ZSCORE_ENTRY = params.get("zscore_entry", self.ZSCORE_ENTRY)
        self.ctx.log.info(
            f"PairTradingStrategy initialized | {self.STOCK_A} vs {self.STOCK_B}"
        )

    async def on_start(self) -> None:
        await self.ctx.schedule("square_off", "15:15")
        await self.ctx.schedule("reset_day", "09:14")
        self.ctx.log.info("PairTradingStrategy started")

    async def on_stop(self) -> None:
        if self.position_side:
            await self._exit_pair("shutdown")

    async def on_schedule(self, event: Any) -> None:
        if event.name == "square_off":
            if self.position_side:
                await self._exit_pair("time-based square off")
        elif event.name == "reset_day":
            self._reset_day()

    def _reset_day(self) -> None:
        self.ratios.clear()
        self.position_side = None
        self.candle_count_a = 0
        self.candle_count_b = 0
        self.latest_a = 0.0
        self.latest_b = 0.0

    # ── helpers ────────────────────────────────────────────────────────

    def _compute_zscore(self) -> float | None:
        """Compute Z-score of the current ratio vs rolling mean/stdev."""
        if len(self.ratios) < self.LOOKBACK:
            return None
        recent = list(self.ratios)[-self.LOOKBACK:]
        mean = statistics.mean(recent)
        std = statistics.pstdev(recent)
        if std < 1e-8:
            return None
        current_ratio = self.ratios[-1]
        return (current_ratio - mean) / std

    # ── candle handler ────────────────────────────────────────────────

    async def on_candle(self, candle: Candle) -> None:
        """Process candles for both stocks and update the spread."""
        symbol = candle.symbol
        close = float(candle.close)

        if symbol == self.STOCK_A:
            self.latest_a = close
            self.candle_count_a += 1
        elif symbol == self.STOCK_B:
            self.latest_b = close
            self.candle_count_b += 1
        else:
            return

        # Only update ratio when both stocks have a new candle
        if self.latest_a <= 0 or self.latest_b <= 0:
            return
        if abs(self.candle_count_a - self.candle_count_b) > 1:
            return  # Wait for sync

        ratio = self.latest_a / self.latest_b
        self.ratios.append(ratio)

        zscore = self._compute_zscore()
        if zscore is None:
            return

        candle_time = candle.timestamp.time()
        if candle_time >= self.SQUARE_OFF_TIME:
            return

        # -- Manage existing position --
        if self.position_side:
            await self._check_exit(zscore)
            return

        # -- Entry signals --
        if zscore < -self.ZSCORE_ENTRY:
            await self._enter_spread("LONG_SPREAD", zscore)
        elif zscore > self.ZSCORE_ENTRY:
            await self._enter_spread("SHORT_SPREAD", zscore)

    async def _enter_spread(self, side: str, zscore: float) -> None:
        """Enter the pair trade."""
        if side == "LONG_SPREAD":
            # Buy A (cheap), Sell B (expensive relative)
            await self.ctx.place_order(
                symbol=self.STOCK_A, side=OrderSide.BUY,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=self.LOT_A,
            )
            await self.ctx.place_order(
                symbol=self.STOCK_B, side=OrderSide.SELL,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=self.LOT_B,
            )
        else:
            # Sell A (expensive), Buy B (cheap relative)
            await self.ctx.place_order(
                symbol=self.STOCK_A, side=OrderSide.SELL,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=self.LOT_A,
            )
            await self.ctx.place_order(
                symbol=self.STOCK_B, side=OrderSide.BUY,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=self.LOT_B,
            )

        self.position_side = side
        self.entry_zscore = zscore
        self.price_a = self.latest_a
        self.price_b = self.latest_b

        await self.ctx.alert(
            f"PairTrade {side} entered | Z={zscore:.2f} "
            f"| {self.STOCK_A}={self.latest_a:.2f} {self.STOCK_B}={self.latest_b:.2f}"
        )

    async def _check_exit(self, zscore: float) -> None:
        """Exit when Z-score reverts to mean or hits stoploss."""
        should_exit = False
        reason = ""

        if self.position_side == "LONG_SPREAD":
            if zscore >= self.ZSCORE_EXIT:
                should_exit = True
                reason = "Z-score reverted to mean"
            elif zscore < -self.ZSCORE_STOPLOSS:
                should_exit = True
                reason = f"Stoploss — Z-score={zscore:.2f}"
        elif self.position_side == "SHORT_SPREAD":
            if zscore <= self.ZSCORE_EXIT:
                should_exit = True
                reason = "Z-score reverted to mean"
            elif zscore > self.ZSCORE_STOPLOSS:
                should_exit = True
                reason = f"Stoploss — Z-score={zscore:.2f}"

        if should_exit:
            await self._exit_pair(reason)

    async def _exit_pair(self, reason: str) -> None:
        """Close both legs of the pair trade."""
        if not self.position_side:
            return

        if self.position_side == "LONG_SPREAD":
            await self.ctx.place_order(
                symbol=self.STOCK_A, side=OrderSide.SELL,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=self.LOT_A,
            )
            await self.ctx.place_order(
                symbol=self.STOCK_B, side=OrderSide.BUY,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=self.LOT_B,
            )
        else:
            await self.ctx.place_order(
                symbol=self.STOCK_A, side=OrderSide.BUY,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=self.LOT_A,
            )
            await self.ctx.place_order(
                symbol=self.STOCK_B, side=OrderSide.SELL,
                order_type=OrderType.MARKET, product_type=ProductType.MIS,
                quantity=self.LOT_B,
            )

        pnl_a = (
            (self.latest_a - self.price_a) * self.LOT_A
            if self.position_side == "LONG_SPREAD"
            else (self.price_a - self.latest_a) * self.LOT_A
        )
        pnl_b = (
            (self.price_b - self.latest_b) * self.LOT_B
            if self.position_side == "LONG_SPREAD"
            else (self.latest_b - self.price_b) * self.LOT_B
        )

        await self.ctx.alert(
            f"PairTrade exit | {reason} | PnL_A={pnl_a:.2f} PnL_B={pnl_b:.2f} "
            f"| Net={pnl_a + pnl_b:.2f}"
        )
        self.position_side = None
