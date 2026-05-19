"""
Paper Trading Manager — orchestrates paper trading sessions, integrates with
the strategy runner, and provides session lifecycle and performance reporting.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.event_bus.bus import BaseEventBus, Topics
from core.models import (
    Exchange,
    Instrument,
    InstrumentType,
    Order,
    OrderSide,
    OrderType,
    ProductType,
    Segment,
)
from .paper_broker import PaperBroker

logger = logging.getLogger(__name__)


class PaperTradingManager:
    """Orchestrates paper trading sessions.

    Manages the lifecycle of a :class:`PaperBroker`, feeds it with market
    data (live or mock), tracks session results, and produces performance
    reports that can be compared with backtests.

    Usage::

        manager = PaperTradingManager(event_bus=bus)
        await manager.start_session({"initial_capital": 500000})
        ...
        stats = manager.get_session_stats()
        report = await manager.stop_session()
    """

    def __init__(self, event_bus: BaseEventBus | None = None, live_feed=None) -> None:
        self._event_bus = event_bus
        self._live_feed = live_feed  # FyersLiveFeed instance for real prices
        self._broker: PaperBroker | None = None
        self._session_active: bool = False
        self._mock_feed_task: asyncio.Task[None] | None = None
        self._session_history: list[dict[str, Any]] = []
        self._active_strategies: dict[str, dict[str, Any]] = {}  # strategy_id -> info

    # ── Session Lifecycle ────────────────────────────────────────────

    async def start_session(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Start a new paper trading session.

        Args:
            config: Optional session configuration with keys:
                - initial_capital (float): Starting capital, default 1_000_000
                - slippage_bps (float): Slippage in basis points, default 2.0
                - commission (float): Commission per order in INR, default 20.0
                - mock_feed (bool): Run mock price feed, default True
                - symbols (list[str]): Symbols for mock feed

        Returns:
            A dict describing the started session.

        Raises:
            RuntimeError: If a session is already active.
        """
        if self._session_active:
            raise RuntimeError("A paper trading session is already active. Stop it first.")

        config = config or {}
        initial_capital = config.get("initial_capital", 1_000_000.0)
        slippage_bps = config.get("slippage_bps", 2.0)
        commission = config.get("commission", 20.0)
        symbols = config.get("symbols", ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS", "INFY"])

        # Determine feed mode: use live Fyers feed if available, else mock
        use_live_feed = (
            self._live_feed is not None
            and hasattr(self._live_feed, "is_connected")
            and self._live_feed.is_connected
        )
        use_mock_feed = config.get("mock_feed", not use_live_feed)
        if use_live_feed:
            use_mock_feed = False  # live feed overrides mock

        self._broker = PaperBroker(
            initial_capital=initial_capital,
            slippage_bps=slippage_bps,
            commission=commission,
            event_bus=self._event_bus,
        )
        await self._broker.connect()
        self._session_active = True

        # Start price feed
        if use_live_feed:
            self._mock_feed_task = asyncio.create_task(
                self._live_price_feed(symbols),
                name="paper-live-feed",
            )
            feed_mode = "fyers_live"
        elif use_mock_feed:
            self._mock_feed_task = asyncio.create_task(
                self._mock_price_feed(symbols),
                name="paper-mock-feed",
            )
            feed_mode = "mock"
        else:
            feed_mode = "none"

        info = {
            "status": "started",
            "session_id": self._broker._session_id,
            "initial_capital": initial_capital,
            "slippage_bps": slippage_bps,
            "commission": commission,
            "feed_mode": feed_mode,
            "symbols": symbols,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("Paper trading session started: %s", info)

        # Publish session start event
        if self._event_bus:
            await self._event_bus.publish(
                topic=Topics.STRATEGY_STATUS,
                event_type="PAPER_SESSION_START",
                payload=info,
                source="paper_trading_manager",
            )

        return info

    async def stop_session(self) -> dict[str, Any]:
        """Stop the active paper trading session and generate a performance report.

        Returns:
            A performance report dict.

        Raises:
            RuntimeError: If no session is active.
        """
        if not self._session_active or self._broker is None:
            raise RuntimeError("No active paper trading session to stop.")

        # Stop mock feed
        if self._mock_feed_task is not None:
            self._mock_feed_task.cancel()
            try:
                await self._mock_feed_task
            except asyncio.CancelledError:
                pass
            self._mock_feed_task = None

        # Cancel all pending orders
        await self._broker.cancel_all_orders()

        # Generate report
        report = self._generate_report()

        # Store in history
        self._session_history.append(report)

        # Publish session stop event
        if self._event_bus:
            await self._event_bus.publish(
                topic=Topics.STRATEGY_STATUS,
                event_type="PAPER_SESSION_STOP",
                payload=report,
                source="paper_trading_manager",
            )

        # Disconnect broker
        await self._broker.disconnect()
        self._session_active = False

        logger.info("Paper trading session stopped. Report: %s", report)
        return report

    # ── Stats & Data Access ──────────────────────────────────────────

    def get_session_stats(self) -> dict[str, Any]:
        """Return current session statistics.

        Returns:
            A dict with P&L, trade counts, win rate, etc.

        Raises:
            RuntimeError: If no session is active.
        """
        if not self._session_active or self._broker is None:
            raise RuntimeError("No active paper trading session.")
        return self._broker.session_summary()

    async def get_positions(self) -> list[dict[str, Any]]:
        """Return current open paper positions.

        Raises:
            RuntimeError: If no session is active.
        """
        if not self._session_active or self._broker is None:
            raise RuntimeError("No active paper trading session.")

        positions = await self._broker.get_positions()
        return [
            {
                "symbol": pos.instrument.symbol,
                "quantity": pos.quantity,
                "average_price": str(pos.average_price),
                "ltp": str(pos.ltp),
                "pnl_unrealized": str(pos.pnl_unrealized),
                "pnl_realized": str(pos.pnl_realized),
                "product_type": pos.product_type.value,
            }
            for pos in positions
        ]

    async def get_orders(self) -> list[dict[str, Any]]:
        """Return paper order history.

        Raises:
            RuntimeError: If no session is active.
        """
        if not self._session_active or self._broker is None:
            raise RuntimeError("No active paper trading session.")

        orders = await self._broker.get_order_history()
        return [
            {
                "order_id": o.order_id[:12],
                "symbol": o.instrument.symbol,
                "side": o.side.value,
                "order_type": o.order_type.value,
                "quantity": o.quantity,
                "price": str(o.price) if o.price else None,
                "status": o.status.value,
                "filled_quantity": o.filled_quantity,
                "average_price": str(o.average_price),
                "placed_at": o.placed_at.isoformat() if o.placed_at else None,
            }
            for o in orders
        ]

    async def place_order(self, order: Order) -> dict[str, Any]:
        """Place an order through the paper broker.

        Args:
            order: The order to place.

        Returns:
            A dict with the order response.

        Raises:
            RuntimeError: If no session is active.
        """
        if not self._session_active or self._broker is None:
            raise RuntimeError("No active paper trading session.")

        response = await self._broker.place_order(order)
        return {
            "success": response.success,
            "order_id": response.order_id,
            "message": response.message,
            "status": response.status.value if response.status else None,
        }

    @property
    def broker(self) -> PaperBroker | None:
        """Access the underlying PaperBroker (for direct integration)."""
        return self._broker

    @property
    def is_active(self) -> bool:
        """Whether a session is currently active."""
        return self._session_active

    def get_session_history(self) -> list[dict[str, Any]]:
        """Return reports from all previous sessions."""
        return list(self._session_history)

    # ── Strategy Deployment ──────────────────────────────────────────

    def deploy_strategy(
        self, strategy_id: str, strategy_name: str, strategy_class: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Register a strategy as deployed in paper mode."""
        if not self._session_active:
            raise RuntimeError("No active paper trading session.")
        self._active_strategies[strategy_id] = {
            "strategy_id": strategy_id,
            "name": strategy_name,
            "class": strategy_class,
            "status": "RUNNING",
            "params": params or {},
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "pnl": 0.0,
        }
        logger.info("Deployed strategy %s in paper mode", strategy_id)
        return self._active_strategies[strategy_id]

    def stop_strategy(self, strategy_id: str) -> dict[str, Any]:
        """Stop a deployed paper strategy."""
        if strategy_id not in self._active_strategies:
            raise ValueError(f"Strategy {strategy_id} not found in paper session")
        self._active_strategies[strategy_id]["status"] = "STOPPED"
        logger.info("Stopped paper strategy %s", strategy_id)
        return self._active_strategies[strategy_id]

    def pause_strategy(self, strategy_id: str) -> dict[str, Any]:
        """Pause a deployed paper strategy."""
        if strategy_id not in self._active_strategies:
            raise ValueError(f"Strategy {strategy_id} not found in paper session")
        self._active_strategies[strategy_id]["status"] = "PAUSED"
        return self._active_strategies[strategy_id]

    def get_deployed_strategies(self) -> list[dict[str, Any]]:
        """Return all deployed paper strategies."""
        return list(self._active_strategies.values())

    # ── Live Price Feed (Fyers) ─────────────────────────────────────

    async def _live_price_feed(self, symbols: list[str]) -> None:
        """Fetch live prices from Fyers and feed to paper broker.

        Uses the cached ticks from FyersLiveFeed (which runs its own
        background refresh). Falls back to mock if a symbol has no cached data.
        """
        try:
            while True:
                ts = datetime.now(timezone.utc)
                for sym in symbols:
                    price = None
                    # Try live feed first
                    if self._live_feed:
                        cached = self._live_feed.get_cached_tick(sym)
                        if cached and cached.get("ltp"):
                            price = float(cached["ltp"])

                    # Fall back to mock if no live data
                    if price is None:
                        base = self._BASE_PRICES.get(sym, 1000.0)
                        price = base + random.gauss(0, base * 0.0003)
                        price = round(max(base * 0.9, price), 2)

                    if self._broker:
                        self._broker.update_price(sym, price, ts)

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.debug("Live price feed stopped")

    # ── Mock Price Feed ──────────────────────────────────────────────

    _BASE_PRICES: dict[str, float] = {
        "NIFTY": 24250.0,
        "BANKNIFTY": 51500.0,
        "RELIANCE": 2950.0,
        "TCS": 3800.0,
        "INFY": 1580.0,
        "HDFCBANK": 1720.0,
        "ICICIBANK": 1280.0,
    }

    async def _mock_price_feed(self, symbols: list[str]) -> None:
        """Generate mock price ticks and feed them to the paper broker.

        Runs in a background task, producing ticks every ~500ms per symbol
        with realistic random-walk price movement.
        """
        prices: dict[str, float] = {}
        for sym in symbols:
            prices[sym] = self._BASE_PRICES.get(sym, 1000.0)

        try:
            while True:
                ts = datetime.now(timezone.utc)
                for sym in symbols:
                    # Random walk with mean reversion
                    base = self._BASE_PRICES.get(sym, 1000.0)
                    current = prices[sym]
                    drift = (base - current) * 0.001  # mean reversion
                    noise = random.gauss(0, current * 0.0003)
                    new_price = max(current * 0.9, current + drift + noise)
                    prices[sym] = round(new_price, 2)

                    if self._broker:
                        self._broker.update_price(sym, prices[sym], ts)

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.debug("Mock price feed stopped")

    # ── Report Generation ────────────────────────────────────────────

    def _generate_report(self) -> dict[str, Any]:
        """Generate a comprehensive performance report for the current session."""
        if self._broker is None:
            return {"error": "No broker available"}

        summary = self._broker.session_summary()
        trades = self._broker._sim.trades
        positions = self._broker._sim.positions

        # Calculate additional metrics
        pnl_values = []
        running_pnl = 0.0
        for trade in trades:
            if trade.side == OrderSide.SELL:
                pos = positions.get(trade.instrument.symbol)
                if pos:
                    realized = float(trade.price) - float(pos.average_price)
                    running_pnl += realized * trade.quantity
            pnl_values.append(running_pnl)

        max_drawdown = self._calculate_max_drawdown(pnl_values)
        sharpe = self._calculate_sharpe(pnl_values)

        report = {
            **summary,
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe, 4),
            "return_pct": round(
                (self._broker.equity - self._broker._initial_capital)
                / self._broker._initial_capital
                * 100,
                4,
            ),
            "positions_summary": [
                {
                    "symbol": sym,
                    "quantity": pos.quantity,
                    "average_price": str(pos.average_price),
                    "pnl_realized": str(pos.pnl_realized),
                }
                for sym, pos in positions.items()
            ],
        }

        return report

    @staticmethod
    def _calculate_max_drawdown(pnl_series: list[float]) -> float:
        """Calculate maximum drawdown from a P&L series."""
        if not pnl_series:
            return 0.0
        peak = pnl_series[0]
        max_dd = 0.0
        for val in pnl_series:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _calculate_sharpe(pnl_series: list[float], risk_free: float = 0.06) -> float:
        """Calculate annualized Sharpe ratio from a P&L series."""
        if len(pnl_series) < 2:
            return 0.0
        returns = [
            pnl_series[i] - pnl_series[i - 1]
            for i in range(1, len(pnl_series))
        ]
        if not returns:
            return 0.0
        avg_ret = sum(returns) / len(returns)
        std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in returns) / len(returns))
        if std_ret == 0:
            return 0.0
        # Annualize assuming ~250 trading days, ~390 ticks per day
        daily_sharpe = (avg_ret - risk_free / 252) / std_ret
        return daily_sharpe * math.sqrt(252)
