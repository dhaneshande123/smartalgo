"""
Event-driven Backtest Engine for the algo trading platform.

Replays historical OHLCV / tick data through a strategy using a
SimulatedBroker, tracks equity curve, and collects trades for
subsequent performance analysis.

The engine is intentionally decoupled from ``pandas`` / ``numpy``.
It reads CSV files directly and drives the strategy via its
``on_candle`` / ``on_tick`` lifecycle hooks.

Usage::

    engine = BacktestEngine()
    result = await engine.run(BacktestConfig(
        strategy_class=MyMomentumStrategy,
        strategy_params={"lookback": 20},
        data_files=["data/nifty_5min_2024.csv"],
        initial_capital=10_000_000,
    ))
    print(result.sharpe_ratio, result.max_drawdown_pct)
"""

from __future__ import annotations

import asyncio
import csv
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from core.backtest.performance import PerformanceAnalyzer
from core.backtest.simulated_broker import SimulatedBroker
from core.constants import IST
from core.models import (
    Candle,
    Exchange,
    Instrument,
    InstrumentType,
    Order,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
    Segment,
    Tick,
    TimeFrame,
    Trade,
)
from strategies.base_strategy import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timestamp parsing helpers (same logic as ReplayEngine)
# ---------------------------------------------------------------------------

_TIMESTAMP_FORMATS: list[str] = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
]


def _parse_timestamp(value: str) -> datetime:
    """Parse a timestamp string, trying multiple common formats."""
    value = value.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Unable to parse timestamp: {value!r}")


def _to_decimal(value: str, default: Decimal = Decimal("0")) -> Decimal:
    value = value.strip()
    if not value:
        return default
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return default


def _to_int(value: str, default: int = 0) -> int:
    value = value.strip()
    if not value:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Configuration & Result
# ---------------------------------------------------------------------------


@dataclass
class BacktestConfig:
    """Configuration for a backtest run.

    Attributes:
        strategy_class: A :class:`BaseStrategy` subclass to instantiate.
        strategy_params: Keyword arguments forwarded to the strategy.
        start_date: First date (inclusive).  ``None`` means no lower bound.
        end_date: Last date (inclusive).  ``None`` means no upper bound.
        initial_capital: Starting cash in the account (INR).
        commission_per_order: Flat brokerage per filled order (INR).
        slippage_bps: Slippage in basis points applied on each fill.
        data_files: Paths to CSV files containing historical candle/tick
            data.  Each file is loaded and merged chronologically.
        timeframes: Timeframe strings used by the strategy (e.g.
            ``["1m", "5m"]``).  The first value is used to tag candles.
    """

    strategy_class: type  # BaseStrategy subclass
    strategy_params: dict[str, Any] = field(default_factory=dict)
    start_date: date | None = None
    end_date: date | None = None
    initial_capital: float = 10_000_000  # 1 Cr INR
    commission_per_order: float = 20.0  # flat brokerage
    slippage_bps: float = 1.0  # slippage in basis points
    data_files: list[str] = field(default_factory=list)
    timeframes: list[str] = field(default_factory=lambda: ["1m", "5m"])

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative")
        if self.commission_per_order < 0:
            raise ValueError("commission_per_order must be non-negative")


@dataclass
class BacktestResult:
    """Complete results from a backtest run.

    Contains raw data (trades, equity curve) and all computed
    performance metrics as top-level attributes.
    """

    config: BacktestConfig | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_seconds: float = 0.0

    # Capital
    initial_capital: float = 0.0
    final_capital: float = 0.0
    total_return: float = 0.0
    total_return_pct: float = 0.0

    # Trade stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    # Risk
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Trade detail
    avg_trade_pnl: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    largest_winner: float = 0.0
    largest_loser: float = 0.0
    avg_holding_period: float = 0.0  # in minutes

    # Charges & net
    total_charges: float = 0.0
    net_pnl: float = 0.0

    # Time-series
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    daily_returns: list[tuple[date, float]] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    monthly_returns: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Backtest Strategy Context — wires the strategy to the SimulatedBroker
# ---------------------------------------------------------------------------


class _BacktestStrategyContext(StrategyContext):
    """Concrete StrategyContext for backtesting.

    Routes order placement, position queries, and price lookups
    through the SimulatedBroker.
    """

    def __init__(
        self,
        strategy_id: str,
        strategy_name: str,
        params: dict[str, Any],
        broker: SimulatedBroker,
        instruments: dict[str, Instrument],
    ) -> None:
        self._strategy_id = strategy_id
        self._strategy_name = strategy_name
        self._params = params
        self._broker = broker
        self._instruments = instruments
        self._logger = logging.getLogger(f"strategy.{strategy_name}")

    # -- Order management --

    async def place_order(self, **kwargs: Any) -> Any:
        """Create and place an order through the simulated broker.

        Accepts the same keyword arguments as ``Order`` plus ``symbol``
        as a convenience shorthand.
        """
        symbol: str = kwargs.pop("symbol", "")
        side: OrderSide = kwargs.pop("side", OrderSide.BUY)
        quantity: int = kwargs.pop("quantity", 1)
        order_type: OrderType = kwargs.pop("order_type", OrderType.MARKET)
        price = kwargs.pop("price", None)
        trigger_price = kwargs.pop("trigger_price", None)
        tag = kwargs.pop("tag", None)

        instrument = self._instruments.get(symbol)
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                exchange=Exchange.NSE,
                segment=Segment.EQUITY,
                instrument_type=InstrumentType.STOCK,
                lot_size=1,
                tick_size=Decimal("0.05"),
            )
            self._instruments[symbol] = instrument

        order_kwargs: dict[str, Any] = {
            "instrument": instrument,
            "order_type": order_type,
            "side": side,
            "product_type": ProductType.NRML,
            "quantity": quantity,
            "strategy_id": self._strategy_id,
            "tag": tag,
        }
        if price is not None:
            order_kwargs["price"] = Decimal(str(price))
        if trigger_price is not None:
            order_kwargs["trigger_price"] = Decimal(str(trigger_price))

        order = Order(**order_kwargs)
        return await self._broker.place_order(order)

    async def cancel_order(self, order_id: str) -> Any:
        return await self._broker.cancel_order(order_id)

    async def cancel_all_orders(self) -> None:
        for oid in list(self._broker.pending_orders.keys()):
            await self._broker.cancel_order(oid)

    async def square_off_all(self) -> None:
        for symbol, pos in self._broker.positions.items():
            if pos.quantity == 0:
                continue
            side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
            await self.place_order(
                symbol=symbol,
                side=side,
                quantity=abs(pos.quantity),
                order_type=OrderType.MARKET,
            )

    # -- Position & portfolio --

    async def get_positions(self) -> list[Any]:
        return list(self._broker.positions.values())

    async def get_pnl(self) -> Any:
        return {
            "equity": self._broker.equity,
            "cash": self._broker.cash,
            "commission": self._broker.total_commission,
        }

    # -- Market data --

    async def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        result: dict[str, float] = {}
        for sym in symbols:
            price = self._broker._current_prices.get(sym)
            if price is not None:
                result[sym] = price
        return result

    async def get_underlying_price(self, symbol: str) -> float:
        return self._broker._current_prices.get(symbol, 0.0)

    async def get_option_chain(self, symbol: str, expiry: Any = None) -> Any:
        return None

    async def get_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[Any]:
        return []

    async def get_greeks(self, symbol: str) -> Any:
        return {}

    async def get_iv(self, symbol: str) -> float:
        return 0.0

    async def get_iv_percentile(self, symbol: str, window: int = 252) -> float:
        return 0.0

    async def get_portfolio_greeks(self) -> Any:
        return {}

    async def get_margin_info(self) -> Any:
        return {}

    # -- Scheduling (no-op in backtest) --

    async def schedule(self, name: str, time_str: str, data: dict[str, Any] | None = None) -> None:
        self._logger.debug("Schedule '%s' at %s (ignored in backtest)", name, time_str)

    async def cancel_schedule(self, name: str) -> None:
        pass

    # -- Alerts --

    async def alert(self, message: str, level: str = "INFO") -> None:
        self._logger.info("[ALERT %s] %s", level, message)

    # -- Metadata --

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    @property
    def params(self) -> dict[str, Any]:
        return dict(self._params)

    @property
    def mode(self) -> str:
        return "BACKTEST"

    @property
    def log(self) -> Any:
        return self._logger


# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, str], None]
"""Signature: (current_bar_index, total_bars, message) -> None"""


# ---------------------------------------------------------------------------
# Backtest Engine
# ---------------------------------------------------------------------------


class BacktestEngine:
    """Event-driven backtesting engine.

    Simulates the full trading pipeline using historical data:

    1. Loads historical candle/tick data from CSV files.
    2. Creates a simulated broker (instant fills with slippage).
    3. Instantiates the strategy and wires it via a StrategyContext.
    4. Feeds data bar-by-bar through the strategy's ``on_candle`` hook.
    5. Tracks all orders, trades, positions, P&L.
    6. Computes comprehensive performance metrics.

    Usage::

        engine = BacktestEngine()
        result = await engine.run(BacktestConfig(
            strategy_class=IronCondorStrategy,
            strategy_params={"strike_offset": 200},
            data_files=["data/nifty_5min_2024.csv"],
            initial_capital=10_000_000,
        ))
        print(result.sharpe_ratio, result.max_drawdown_pct)
    """

    def __init__(self) -> None:
        self._progress_callback: ProgressCallback | None = None
        self._cancelled: bool = False
        self._is_running: bool = False

    def set_progress_callback(self, callback: ProgressCallback) -> None:
        """Register a callback invoked after each bar is processed."""
        self._progress_callback = callback

    def cancel(self) -> None:
        """Request cancellation of a running backtest."""
        self._cancelled = True

    @property
    def is_running(self) -> bool:
        """Whether the engine is currently executing a backtest."""
        return self._is_running

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self, config: BacktestConfig) -> BacktestResult:
        """Execute a full backtest.

        Args:
            config: Backtest configuration specifying the strategy,
                data files, capital, and simulation parameters.

        Returns:
            A :class:`BacktestResult` with all trades, equity curve,
            and performance metrics.

        Raises:
            RuntimeError: If the engine is already running.
            ValueError: If no data files are specified or data is empty.
        """
        if self._is_running:
            raise RuntimeError("BacktestEngine is already running")

        self._is_running = True
        self._cancelled = False
        wall_start = time.monotonic()
        start_time = datetime.now(timezone.utc)

        try:
            return await self._execute(config, wall_start, start_time)
        finally:
            self._is_running = False

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------

    async def _execute(
        self,
        config: BacktestConfig,
        wall_start: float,
        start_time: datetime,
    ) -> BacktestResult:
        """Core backtest loop."""

        # 1. Create the simulated broker
        broker = self._create_simulated_broker(config)

        # 2. Load data
        candles = self._load_data(config)
        if not candles:
            logger.warning("No data loaded — returning empty result")
            return BacktestResult(config=config, start_time=start_time)

        total_bars = len(candles)
        logger.info(
            "Backtest starting: %d bars, capital=%.0f, strategy=%s",
            total_bars,
            config.initial_capital,
            config.strategy_class.__name__,
        )

        # 3. Build instruments from data
        instruments: dict[str, Instrument] = {}
        for c in candles:
            sym = c.symbol
            if sym not in instruments:
                instruments[sym] = Instrument(
                    symbol=sym,
                    exchange=Exchange.NSE,
                    segment=Segment.EQUITY,
                    instrument_type=InstrumentType.STOCK,
                    lot_size=1,
                    tick_size=Decimal("0.05"),
                )

        # 4. Instantiate strategy
        strategy_id = uuid.uuid4().hex[:12]
        ctx = _BacktestStrategyContext(
            strategy_id=strategy_id,
            strategy_name=config.strategy_class.__name__,
            params=config.strategy_params,
            broker=broker,
            instruments=instruments,
        )

        strategy: BaseStrategy = config.strategy_class()
        await strategy.on_init(ctx)
        await strategy.on_start()

        # 5. Bar-by-bar replay
        equity_curve: list[tuple[datetime, float]] = []

        for bar_idx, candle in enumerate(candles):
            if self._cancelled:
                logger.info(
                    "Backtest cancelled at bar %d / %d", bar_idx, total_bars
                )
                break

            symbol = candle.symbol
            close_price = float(candle.close)
            ts = candle.timestamp

            # Update broker clock
            broker.set_timestamp(ts)

            # Simulate intra-bar price walk: open -> high -> low -> close
            # This allows pending limit/SL orders to trigger correctly.
            broker.update_price(symbol, float(candle.open), ts)
            broker.update_price(symbol, float(candle.high), ts)
            broker.update_price(symbol, float(candle.low), ts)
            broker.update_price(symbol, close_price, ts)

            # Deliver candle to strategy
            try:
                await strategy.on_candle(candle)
            except Exception:
                logger.exception(
                    "Strategy on_candle failed at bar %d (%s)",
                    bar_idx,
                    ts.isoformat(),
                )

            # Record equity snapshot
            equity = broker.equity
            equity_curve.append((ts, equity))

            # Progress callback
            if self._progress_callback is not None:
                self._progress_callback(
                    bar_idx + 1,
                    total_bars,
                    f"Bar {bar_idx + 1}/{total_bars} | Equity: {equity:,.0f}",
                )

            # Yield control periodically
            if bar_idx % 200 == 0:
                await asyncio.sleep(0)

        # 6. Notify strategy of completion
        try:
            await strategy.on_stop()
        except Exception:
            logger.exception("Strategy on_stop failed")

        # 7. Compute metrics
        wall_elapsed = time.monotonic() - wall_start
        end_time = datetime.now(timezone.utc)

        result = self._compute_metrics(
            trades=broker.trades,
            equity_curve=equity_curve,
            config=config,
            total_commission=broker.total_commission,
        )
        result.config = config
        result.start_time = start_time
        result.end_time = end_time
        result.duration_seconds = round(wall_elapsed, 3)

        logger.info(
            "Backtest complete: %d bars, %d trades, return=%.2f%%, "
            "sharpe=%.2f, max_dd=%.2f%%, duration=%.1fs",
            total_bars,
            result.total_trades,
            result.total_return_pct,
            result.sharpe_ratio,
            result.max_drawdown_pct,
            wall_elapsed,
        )

        return result

    # ------------------------------------------------------------------
    # Broker creation
    # ------------------------------------------------------------------

    def _create_simulated_broker(self, config: BacktestConfig) -> SimulatedBroker:
        """Create and initialize a SimulatedBroker from the config.

        Args:
            config: Backtest configuration.

        Returns:
            A SimulatedBroker with capital set.
        """
        broker = SimulatedBroker(
            slippage_bps=config.slippage_bps,
            commission=config.commission_per_order,
        )
        broker.set_capital(config.initial_capital)
        return broker

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_data(self, config: BacktestConfig) -> list[Candle]:
        """Load and merge candle data from CSV files.

        Supports standard OHLCV CSVs with columns:
        ``Date, Open, High, Low, Close, Volume``
        (case-insensitive, flexible date formats).

        Optionally filters by ``config.start_date`` / ``config.end_date``.

        Args:
            config: Backtest configuration with ``data_files`` paths.

        Returns:
            A sorted list of ``Candle`` objects.

        Raises:
            FileNotFoundError: If a CSV file does not exist.
        """
        all_candles: list[Candle] = []

        # Map timeframe string to enum
        tf_map: dict[str, TimeFrame] = {
            "tick": TimeFrame.TICK,
            "1s": TimeFrame.S1,
            "5s": TimeFrame.S5,
            "1m": TimeFrame.M1,
            "5m": TimeFrame.M5,
            "15m": TimeFrame.M15,
            "30m": TimeFrame.M30,
            "1h": TimeFrame.H1,
            "1d": TimeFrame.D1,
            "d": TimeFrame.D1,
            "daily": TimeFrame.D1,
            "1w": TimeFrame.W1,
            "weekly": TimeFrame.W1,
            "1mo": TimeFrame.MO1,
            "monthly": TimeFrame.MO1,
        }
        primary_tf = tf_map.get(
            config.timeframes[0].lower() if config.timeframes else "1m",
            TimeFrame.M1,
        )

        for file_path in config.data_files:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"Data file not found: {path}")

            candles = self._parse_csv(path, primary_tf, config)
            all_candles.extend(candles)
            logger.info("Loaded %d candles from %s", len(candles), path.name)

        # Sort chronologically
        all_candles.sort(key=lambda c: c.timestamp)

        logger.info("Total candles loaded: %d", len(all_candles))
        return all_candles

    def _parse_csv(
        self,
        path: Path,
        timeframe: TimeFrame,
        config: BacktestConfig,
    ) -> list[Candle]:
        """Parse a single CSV file into Candle objects.

        Auto-detects column mapping from headers.  Supports ``Date``,
        ``Open``, ``High``, ``Low``, ``Close``, ``Volume`` columns
        in any order and case.

        Args:
            path: Path to the CSV file.
            timeframe: TimeFrame enum to tag on each candle.
            config: Config for date filtering.

        Returns:
            List of Candle objects.
        """
        candles: list[Candle] = []

        # Derive symbol from filename (strip extension)
        symbol = path.stem.upper()
        # Common patterns: "nifty_5min_2024" -> "NIFTY"
        for sep in ("_", "-", " "):
            if sep in symbol:
                symbol = symbol.split(sep)[0]
                break

        instrument_id = f"backtest:{symbol}"

        # Convert date bounds to datetime for comparison
        start_dt: datetime | None = None
        end_dt: datetime | None = None
        if config.start_date is not None:
            start_dt = datetime.combine(config.start_date, datetime.min.time()).replace(
                tzinfo=IST
            )
        if config.end_date is not None:
            end_dt = datetime.combine(config.end_date, datetime.max.time()).replace(
                tzinfo=IST
            )

        with open(path, "r", newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            raw_headers = next(reader, None)
            if raw_headers is None:
                logger.warning("Empty CSV: %s", path)
                return []

            # Build column index map
            headers = [h.strip().lower().replace(" ", "_") for h in raw_headers]
            col: dict[str, int] = {h: i for i, h in enumerate(headers)}

            def _get(name: str, row: list[str], default: str = "") -> str:
                idx = col.get(name)
                if idx is not None and idx < len(row):
                    return row[idx].strip()
                return default

            for row_num, row in enumerate(reader, start=2):
                if not row or all(c.strip() == "" for c in row):
                    continue

                try:
                    ts_str = (
                        _get("date", row)
                        or _get("timestamp", row)
                        or _get("datetime", row)
                        or _get("time", row)
                    )
                    if not ts_str:
                        continue

                    ts = _parse_timestamp(ts_str)

                    # Date range filter
                    if start_dt is not None and ts < start_dt:
                        continue
                    if end_dt is not None and ts > end_dt:
                        continue

                    open_p = _to_decimal(_get("open", row))
                    high_p = _to_decimal(_get("high", row))
                    low_p = _to_decimal(_get("low", row))
                    close_p = _to_decimal(_get("close", row))
                    volume = _to_int(_get("volume", row))
                    oi = _to_int(_get("oi", row) or _get("open_interest", row))

                    if close_p <= 0:
                        continue

                    # Ensure OHLC consistency
                    if high_p < low_p:
                        high_p, low_p = low_p, high_p
                    if open_p < low_p:
                        open_p = low_p
                    if open_p > high_p:
                        open_p = high_p
                    if close_p < low_p:
                        close_p = low_p
                    if close_p > high_p:
                        close_p = high_p

                    candle = Candle(
                        instrument_id=instrument_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        open=open_p,
                        high=high_p,
                        low=low_p,
                        close=close_p,
                        volume=volume,
                        oi=oi,
                        timestamp=ts,
                    )
                    candles.append(candle)

                except Exception as exc:
                    logger.debug("Skipping row %d in %s: %s", row_num, path.name, exc)
                    continue

        return candles

    # ------------------------------------------------------------------
    # Metrics computation
    # ------------------------------------------------------------------

    def _compute_metrics(
        self,
        trades: list[Trade],
        equity_curve: list[tuple[datetime, float]],
        config: BacktestConfig,
        total_commission: float,
    ) -> BacktestResult:
        """Compute all performance metrics and populate a BacktestResult.

        Delegates to :class:`PerformanceAnalyzer` for the heavy lifting.

        Args:
            trades: All executed trades.
            equity_curve: Time-series of (timestamp, equity) snapshots.
            config: Backtest config (for initial capital).
            total_commission: Total commission paid.

        Returns:
            A fully populated ``BacktestResult``.
        """
        metrics = PerformanceAnalyzer.compute_metrics(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=config.initial_capital,
        )

        # Extract daily returns for the result
        daily_returns = metrics.get("daily_returns", [])
        monthly_returns = metrics.get("monthly_returns", {})

        net_pnl = metrics.get("total_return", 0.0) - total_commission

        return BacktestResult(
            initial_capital=config.initial_capital,
            final_capital=metrics.get("final_capital", config.initial_capital),
            total_return=metrics.get("total_return", 0.0),
            total_return_pct=metrics.get("total_return_pct", 0.0),
            total_trades=metrics.get("total_trades", 0),
            winning_trades=metrics.get("winning_trades", 0),
            losing_trades=metrics.get("losing_trades", 0),
            win_rate=metrics.get("win_rate", 0.0),
            profit_factor=metrics.get("profit_factor", 0.0),
            max_drawdown=metrics.get("max_drawdown", 0.0),
            max_drawdown_pct=metrics.get("max_drawdown_pct", 0.0),
            sharpe_ratio=metrics.get("sharpe_ratio", 0.0),
            sortino_ratio=metrics.get("sortino_ratio", 0.0),
            calmar_ratio=metrics.get("calmar_ratio", 0.0),
            avg_trade_pnl=metrics.get("avg_trade_pnl", 0.0),
            avg_winner=metrics.get("avg_winner", 0.0),
            avg_loser=metrics.get("avg_loser", 0.0),
            largest_winner=metrics.get("largest_winner", 0.0),
            largest_loser=metrics.get("largest_loser", 0.0),
            avg_holding_period=metrics.get("avg_holding_period_minutes", 0.0),
            total_charges=total_commission,
            net_pnl=round(net_pnl, 2),
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            trades=trades,
            monthly_returns=monthly_returns,
        )

    # ------------------------------------------------------------------
    # Standalone metrics (for direct use without full backtest)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sharpe(daily_returns: list[float]) -> float:
        """Convenience wrapper for Sharpe ratio computation."""
        return PerformanceAnalyzer.compute_sharpe_ratio(daily_returns)

    @staticmethod
    def _compute_sortino(daily_returns: list[float]) -> float:
        """Convenience wrapper for Sortino ratio computation."""
        return PerformanceAnalyzer.compute_sortino_ratio(daily_returns)

    @staticmethod
    def _compute_max_drawdown(
        equity_curve: list[tuple[datetime, float]],
    ) -> tuple[float, float]:
        """Convenience wrapper for max drawdown computation.

        Returns:
            Tuple of (max_dd_abs, max_dd_pct).
        """
        dd_abs, dd_pct, _, _ = PerformanceAnalyzer.compute_max_drawdown(equity_curve)
        return dd_abs, dd_pct
