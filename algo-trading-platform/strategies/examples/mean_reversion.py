"""
Bollinger Band Mean Reversion Strategy — NIFTY Options
======================================================

Uses Bollinger Bands on the underlying NIFTY spot to identify
overbought / oversold conditions, then trades options to capture
mean reversion moves back toward the middle band (20-SMA).

Entry logic:
    - BUY ATM CE when NIFTY closes below the lower Bollinger Band
      (oversold — expecting bounce).
    - BUY ATM PE when NIFTY closes above the upper Bollinger Band
      (overbought — expecting pullback).
    - Only one position at a time.

Exit logic:
    - Target: price reverts to the 20-SMA (middle band).
    - Stoploss: 30 % of option premium paid.
    - Time exit: square off by 15:00 IST (intraday MIS product).
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
    Tick,
)
from strategies.base_strategy import BaseStrategy, StrategyContext


class MeanReversionStrategy(BaseStrategy):
    """Bollinger Band mean-reversion on NIFTY options (intraday)."""

    # -- configurable parameters --
    UNDERLYING: str = "NIFTY"
    LOT_SIZE: int = 25
    NUM_LOTS: int = 1
    STRIKE_STEP: int = 50
    BB_PERIOD: int = 20          # lookback for SMA and std-dev
    BB_STD_DEV: float = 2.0      # Bollinger Band width multiplier
    SL_PCT: float = 0.30         # stoploss as fraction of entry premium
    SQUARE_OFF_TIME: dt_time = dt_time(15, 0)
    MIN_CANDLES: int = 20        # need at least this many candles before trading

    def __init__(self) -> None:
        self.ctx: StrategyContext
        self.closes: deque[float] = deque(maxlen=50)
        self.upper_band: float = 0.0
        self.lower_band: float = 0.0
        self.sma: float = 0.0
        self.in_position: bool = False
        self.position_type: str = ""   # "CE" or "PE"
        self.option_symbol: str = ""
        self.entry_price: float = 0.0
        self.quantity: int = 0

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _round_to_strike(price: float, step: int = 50) -> float:
        return round(price / step) * step

    def _build_symbol(self, strike: float, opt_type: str, expiry: Any) -> str:
        exp_str = expiry.strftime("%d%b%y").upper() if hasattr(expiry, "strftime") else str(expiry)
        return f"{self.UNDERLYING}{exp_str}{int(strike)}{opt_type}"

    def _compute_bands(self) -> bool:
        """Recalculate Bollinger Bands. Returns True if enough data."""
        if len(self.closes) < self.BB_PERIOD:
            return False
        recent = list(self.closes)[-self.BB_PERIOD:]
        self.sma = statistics.mean(recent)
        std = statistics.pstdev(recent)
        self.upper_band = self.sma + self.BB_STD_DEV * std
        self.lower_band = self.sma - self.BB_STD_DEV * std
        return True

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_init(self, context: StrategyContext) -> None:
        self.ctx = context
        params = context.params
        self.UNDERLYING = params.get("underlying", self.UNDERLYING)
        self.NUM_LOTS = params.get("num_lots", self.NUM_LOTS)
        if self.UNDERLYING == "BANKNIFTY":
            self.LOT_SIZE = 15
            self.STRIKE_STEP = 100
        self.quantity = self.LOT_SIZE * self.NUM_LOTS
        self.ctx.log.info("MeanReversionStrategy initialized")

    async def on_start(self) -> None:
        await self.ctx.schedule("square_off", "15:00")
        self.ctx.log.info("MeanReversionStrategy started")

    async def on_stop(self) -> None:
        if self.in_position:
            await self.ctx.square_off_all()
            self.in_position = False

    async def on_schedule(self, event: Any) -> None:
        if event.name == "square_off":
            await self._time_exit()

    async def _time_exit(self) -> None:
        if not self.in_position:
            return
        await self.ctx.square_off_all()
        self.in_position = False
        await self.ctx.alert("MeanReversion time-exit triggered")

    # ── candle handler (5-min candles on underlying) ──────────────────

    async def on_candle(self, candle: Candle) -> None:
        """Process 5-min candles of the underlying to update bands and trade."""
        if candle.symbol != self.UNDERLYING:
            return

        close = float(candle.close)
        self.closes.append(close)

        if not self._compute_bands():
            return

        candle_time = candle.timestamp.time()
        if candle_time >= self.SQUARE_OFF_TIME:
            return

        # --- Entry signals ---
        if not self.in_position:
            if close < self.lower_band:
                await self._enter_option("CE", close)
            elif close > self.upper_band:
                await self._enter_option("PE", close)
            return

        # --- Exit: check if price reverted to SMA ---
        if self.position_type == "CE" and close >= self.sma:
            await self._exit_position("Target hit — reverted to SMA (CE long)")
        elif self.position_type == "PE" and close <= self.sma:
            await self._exit_position("Target hit — reverted to SMA (PE long)")

    async def _enter_option(self, opt_type: str, spot: float) -> None:
        """Buy ATM option of given type."""
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
        self.entry_price = float(ltp_map.get(symbol, 0.0))
        self.option_symbol = symbol
        self.position_type = opt_type
        self.in_position = True

        await self.ctx.alert(
            f"MeanReversion {opt_type} bought | ATM={atm} | Premium={self.entry_price:.2f}"
        )

    async def _exit_position(self, reason: str) -> None:
        if not self.in_position:
            return
        await self.ctx.place_order(
            symbol=self.option_symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            product_type=ProductType.MIS,
            quantity=self.quantity,
        )
        self.in_position = False
        await self.ctx.alert(f"MeanReversion exit | {reason}")

    # ── tick handler — stoploss monitoring ────────────────────────────

    async def on_tick(self, tick: Tick) -> None:
        """Monitor option premium for stoploss."""
        if not self.in_position:
            return

        ltp_map = await self.ctx.get_ltp([self.option_symbol])
        current = float(ltp_map.get(self.option_symbol, self.entry_price))

        # Stoploss: premium dropped by SL_PCT from entry
        sl_price = self.entry_price * (1.0 - self.SL_PCT)
        if current <= sl_price:
            await self._exit_position(
                f"Stoploss hit | entry={self.entry_price:.2f} current={current:.2f}"
            )
