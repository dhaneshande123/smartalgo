"""
Strategy Runner — manages the full lifecycle of trading strategies.

Loads strategy classes, initialises them with a ``StrategyContext``, and
routes market-data / order events to the correct running strategies.
Handles graceful start, stop, pause, resume, and error recovery.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
import traceback
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Awaitable

from core.constants import IST
from core.event_bus.bus import BaseEventBus, BusEvent, EventPriority, Topics
from core.models import (
    StrategyConfig,
    StrategyState,
    StrategyStatus,
    TradingMode,
)
from core.strategy_engine.parameter_store import ParameterStore
from core.strategy_engine.scheduler import StrategyScheduler
from strategies.base_strategy import BaseStrategy, ScheduledEvent, StrategyContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Live StrategyContext implementation
# ---------------------------------------------------------------------------

class _LiveStrategyContext(StrategyContext):
    """Concrete ``StrategyContext`` wired to platform services.

    Created by the ``StrategyRunner`` for each deployed strategy and passed
    into ``BaseStrategy.on_init``.  All methods delegate to the function
    references provided by the runner (order submission, market data, risk
    checks, scheduling, etc.).
    """

    def __init__(
        self,
        *,
        strategy_id: str,
        strategy_name: str,
        mode: TradingMode,
        params: dict[str, Any],
        order_submit_fn: Callable[..., Awaitable[Any]],
        market_data_fn: Callable[..., Awaitable[Any]],
        risk_check_fn: Callable[..., Awaitable[Any]],
        event_bus: BaseEventBus,
        scheduler: StrategyScheduler,
        parameter_store: ParameterStore,
    ) -> None:
        self._strategy_id = strategy_id
        self._strategy_name = strategy_name
        self._mode = mode
        self._params = params
        self._order_submit_fn = order_submit_fn
        self._market_data_fn = market_data_fn
        self._risk_check_fn = risk_check_fn
        self._event_bus = event_bus
        self._scheduler = scheduler
        self._parameter_store = parameter_store
        self._logger = logging.getLogger(f"strategy.{strategy_name}")

    # -- metadata ----------------------------------------------------------

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    @property
    def mode(self) -> str:
        return self._mode.value

    @property
    def params(self) -> dict[str, Any]:
        """Return live params from parameter store (hot-reloadable)."""
        stored = self._parameter_store.get(self._strategy_name)
        if stored:
            return stored
        return dict(self._params)

    @property
    def log(self) -> logging.Logger:
        return self._logger

    # -- order management --------------------------------------------------

    async def place_order(self, **kwargs: Any) -> Any:
        # Run risk check first
        await self._risk_check_fn(self._strategy_name, kwargs)
        result = await self._order_submit_fn("place", kwargs)
        await self._event_bus.publish(
            topic=Topics.STRATEGY_SIGNALS,
            event_type="order_placed",
            payload={
                "strategy": self._strategy_name,
                "order": kwargs,
                "result": str(result),
            },
            source=self._strategy_name,
        )
        return result

    async def modify_order(self, order_id: str, **kwargs: Any) -> Any:
        return await self._order_submit_fn("modify", {"order_id": order_id, **kwargs})

    async def cancel_order(self, order_id: str) -> Any:
        return await self._order_submit_fn("cancel", {"order_id": order_id})

    async def cancel_all_orders(self) -> None:
        await self._order_submit_fn("cancel_all", {"strategy": self._strategy_name})

    async def square_off_all(self) -> None:
        await self._order_submit_fn("square_off_all", {"strategy": self._strategy_name})

    # -- position & portfolio ----------------------------------------------

    async def get_positions(self) -> list[Any]:
        return await self._market_data_fn("get_positions", {"strategy": self._strategy_name})

    async def get_portfolio_greeks(self) -> Any:
        return await self._market_data_fn("get_portfolio_greeks", {"strategy": self._strategy_name})

    async def get_margin_info(self) -> Any:
        return await self._market_data_fn("get_margin_info", {"strategy": self._strategy_name})

    async def get_pnl(self) -> Any:
        return await self._market_data_fn("get_pnl", {"strategy": self._strategy_name})

    # -- market data -------------------------------------------------------

    async def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        return await self._market_data_fn("get_ltp", {"symbols": symbols})

    async def get_option_chain(self, symbol: str, expiry: Any = None) -> Any:
        return await self._market_data_fn("get_option_chain", {"symbol": symbol, "expiry": expiry})

    async def get_greeks(self, symbol: str) -> Any:
        return await self._market_data_fn("get_greeks", {"symbol": symbol})

    async def get_iv(self, symbol: str) -> float:
        return await self._market_data_fn("get_iv", {"symbol": symbol})

    async def get_iv_percentile(self, symbol: str, window: int = 252) -> float:
        return await self._market_data_fn("get_iv_percentile", {"symbol": symbol, "window": window})

    async def get_candles(
        self, symbol: str, timeframe: str, count: int = 100
    ) -> list[Any]:
        return await self._market_data_fn(
            "get_candles", {"symbol": symbol, "timeframe": timeframe, "count": count}
        )

    async def get_underlying_price(self, symbol: str) -> float:
        return await self._market_data_fn("get_underlying_price", {"symbol": symbol})

    # -- scheduling --------------------------------------------------------

    async def schedule(
        self, name: str, time_str: str, data: dict[str, Any] | None = None
    ) -> None:
        """Schedule a one-time callback at *time_str* (HH:MM IST today)."""
        from datetime import timedelta

        now_ist = datetime.now(IST)
        hour, minute = (int(p) for p in time_str.split(":"))
        target = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now_ist:
            # Already past today — schedule for tomorrow
            target += timedelta(days=1)
        target_utc = target.astimezone(timezone.utc)

        strategy_ref = self  # capture for closure

        async def _on_fire() -> None:
            # Deliver to strategy via its on_schedule handler
            pass  # Actual delivery handled by runner's _schedule_bridge

        self._scheduler.schedule_once(
            strategy_name=self._strategy_name,
            job_name=name,
            callback=_on_fire,
            run_at=target_utc,
        )

    async def cancel_schedule(self, name: str) -> None:
        jobs = self._scheduler.get_jobs(self._strategy_name)
        for job in jobs:
            if job.job_name == name:
                self._scheduler.cancel_job(job.job_id)
                break

    # -- alerts & logging --------------------------------------------------

    async def alert(self, message: str, level: str = "INFO") -> None:
        await self._event_bus.publish(
            topic=Topics.RISK_ALERTS,
            event_type="strategy_alert",
            payload={
                "strategy": self._strategy_name,
                "message": message,
                "level": level,
            },
            source=self._strategy_name,
        )


# ---------------------------------------------------------------------------
# Strategy Runner
# ---------------------------------------------------------------------------

class StrategyRunner:
    """Manages the lifecycle of trading strategies.

    Responsibilities:
    - Load strategy classes dynamically (or from a pre-registered registry)
    - Initialize strategies with their ``StrategyContext``
    - Start / stop / pause / resume strategies
    - Route market data events to appropriate strategies
    - Handle strategy errors gracefully
    - Track strategy performance metrics via ``StrategyState``

    Usage::

        runner = StrategyRunner(event_bus, order_fn, market_fn, risk_fn)
        runner.register_strategy("iron_condor", IronCondorStrategy)
        await runner.start_strategy("iron_condor", config)
        await runner.stop_strategy("iron_condor")
    """

    def __init__(
        self,
        event_bus: BaseEventBus,
        order_submit_fn: Callable[..., Awaitable[Any]],
        market_data_fn: Callable[..., Awaitable[Any]],
        risk_check_fn: Callable[..., Awaitable[Any]],
    ) -> None:
        self._event_bus = event_bus
        self._order_submit_fn = order_submit_fn
        self._market_data_fn = market_data_fn
        self._risk_check_fn = risk_check_fn

        # strategy name -> live instance
        self._strategies: dict[str, BaseStrategy] = {}
        # strategy name -> config
        self._configs: dict[str, StrategyConfig] = {}
        # strategy name -> runtime state model
        self._states: dict[str, StrategyState] = {}
        # strategy name -> strategy class (registered)
        self._registry: dict[str, type[BaseStrategy]] = {}
        # strategy name -> set of subscribed symbols
        self._subscriptions: dict[str, set[str]] = {}
        # background tasks
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # event bus subscription ids (for cleanup)
        self._bus_sub_ids: list[str] = []

        # Shared sub-components
        self._scheduler = StrategyScheduler()
        self._parameter_store = ParameterStore()

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def register_strategy(self, name: str, strategy_class: type[BaseStrategy]) -> None:
        """Register a strategy class under *name* so it can be started later."""
        if not (isinstance(strategy_class, type) and issubclass(strategy_class, BaseStrategy)):
            raise TypeError(
                f"strategy_class must be a subclass of BaseStrategy, got {strategy_class!r}"
            )
        self._registry[name] = strategy_class
        logger.info("Registered strategy class %s as '%s'", strategy_class.__name__, name)

    def _resolve_class(self, config: StrategyConfig) -> type[BaseStrategy]:
        """Resolve a strategy class from the registry or by dynamic import."""
        # Try registry first
        if config.name in self._registry:
            return self._registry[config.name]

        # Dynamic import from class_path
        module_path, _, class_name = config.class_path.rpartition(".")
        if not module_path:
            raise ImportError(
                f"Invalid class_path '{config.class_path}' — expected 'module.ClassName'"
            )
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name, None)
        if cls is None:
            raise ImportError(
                f"Class '{class_name}' not found in module '{module_path}'"
            )
        if not (isinstance(cls, type) and issubclass(cls, BaseStrategy)):
            raise TypeError(
                f"Resolved class {cls!r} is not a BaseStrategy subclass"
            )
        return cls

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_strategy(self, name: str, config: StrategyConfig) -> None:
        """Instantiate and start a strategy identified by *name*."""
        if name in self._strategies:
            raise RuntimeError(f"Strategy '{name}' is already running")

        strategy_class = self._resolve_class(config)
        strategy_id = f"{name}-{uuid.uuid4().hex[:8]}"

        # Build state
        state = StrategyState(
            strategy_id=strategy_id,
            name=name,
            status=StrategyStatus.INITIALIZING,
            deployed_at=datetime.now(timezone.utc),
        )
        self._states[name] = state
        self._configs[name] = config

        # Store initial params
        if config.params:
            self._parameter_store.set(name, config.params)

        # Build context
        context = self._build_context(name, config, strategy_id)

        # Instantiate and initialise
        instance = strategy_class()
        try:
            await instance.on_init(context)
            instance.ctx = context
        except Exception as exc:
            state.status = StrategyStatus.ERROR
            state.error_message = f"on_init failed: {exc}"
            logger.exception("Strategy '%s' on_init failed", name)
            await self._publish_status_event(name, StrategyStatus.ERROR, str(exc))
            return

        self._strategies[name] = instance
        self._subscriptions[name] = set()

        # Transition to RUNNING
        try:
            await instance.on_start()
            state.status = StrategyStatus.RUNNING
            state.last_heartbeat = datetime.now(timezone.utc)
            logger.info("Strategy '%s' started (id=%s, mode=%s)", name, strategy_id, config.mode.value)
            await self._publish_status_event(name, StrategyStatus.RUNNING)
        except Exception as exc:
            state.status = StrategyStatus.ERROR
            state.error_message = f"on_start failed: {exc}"
            logger.exception("Strategy '%s' on_start failed", name)
            await self._handle_strategy_error(name, instance, exc)

    async def stop_strategy(self, name: str) -> None:
        """Gracefully stop a running strategy."""
        instance = self._strategies.get(name)
        if instance is None:
            raise KeyError(f"Strategy '{name}' is not loaded")

        state = self._states[name]
        logger.info("Stopping strategy '%s' ...", name)

        try:
            await instance.on_stop()
        except Exception as exc:
            logger.exception("Strategy '%s' on_stop raised", name)

        # Cancel any background tasks
        task = self._tasks.pop(name, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Cancel scheduled jobs
        self._scheduler.cancel_strategy_jobs(name)

        # Clean up
        state.status = StrategyStatus.STOPPED
        state.error_message = None
        self._strategies.pop(name, None)
        self._subscriptions.pop(name, None)
        self._parameter_store.remove_listeners(name)

        logger.info("Strategy '%s' stopped", name)
        await self._publish_status_event(name, StrategyStatus.STOPPED)

    async def pause_strategy(self, name: str) -> None:
        """Pause a running strategy — it will stop receiving events."""
        instance = self._strategies.get(name)
        if instance is None:
            raise KeyError(f"Strategy '{name}' is not loaded")

        state = self._states[name]
        if state.status != StrategyStatus.RUNNING:
            raise RuntimeError(
                f"Cannot pause strategy '{name}' in state {state.status.value}"
            )

        try:
            await instance.on_pause()
        except Exception as exc:
            logger.exception("Strategy '%s' on_pause raised", name)

        state.status = StrategyStatus.PAUSED
        logger.info("Strategy '%s' paused", name)
        await self._publish_status_event(name, StrategyStatus.PAUSED)

    async def resume_strategy(self, name: str) -> None:
        """Resume a paused strategy."""
        instance = self._strategies.get(name)
        if instance is None:
            raise KeyError(f"Strategy '{name}' is not loaded")

        state = self._states[name]
        if state.status != StrategyStatus.PAUSED:
            raise RuntimeError(
                f"Cannot resume strategy '{name}' in state {state.status.value}"
            )

        try:
            await instance.on_resume()
        except Exception as exc:
            logger.exception("Strategy '%s' on_resume raised", name)

        state.status = StrategyStatus.RUNNING
        state.last_heartbeat = datetime.now(timezone.utc)
        logger.info("Strategy '%s' resumed", name)
        await self._publish_status_event(name, StrategyStatus.RUNNING)

    async def stop_all(self) -> None:
        """Stop every running strategy and the shared scheduler."""
        names = list(self._strategies.keys())
        for name in names:
            try:
                await self.stop_strategy(name)
            except Exception:
                logger.exception("Error stopping strategy '%s'", name)
        await self._scheduler.stop()
        logger.info("All strategies stopped")

    # ------------------------------------------------------------------
    # Event routing
    # ------------------------------------------------------------------

    async def on_tick(self, symbol: str, tick_data: dict[str, Any]) -> None:
        """Route a market tick to every running strategy subscribed to *symbol*."""
        for name, instance in self._strategies.items():
            state = self._states.get(name)
            if state is None or state.status != StrategyStatus.RUNNING:
                continue
            # Route to all strategies (they filter internally), or check subscriptions
            subs = self._subscriptions.get(name, set())
            if subs and symbol not in subs:
                continue
            try:
                await instance.on_tick(tick_data)
                if state is not None:
                    state.last_heartbeat = datetime.now(timezone.utc)
            except Exception as exc:
                logger.exception("Strategy '%s' on_tick raised", name)
                await self._handle_strategy_error(name, instance, exc)

    async def on_candle(
        self, symbol: str, timeframe: str, candle_data: dict[str, Any]
    ) -> None:
        """Route a completed candle to running strategies."""
        for name, instance in self._strategies.items():
            state = self._states.get(name)
            if state is None or state.status != StrategyStatus.RUNNING:
                continue
            subs = self._subscriptions.get(name, set())
            if subs and symbol not in subs:
                continue
            try:
                await instance.on_candle(candle_data)
                if state is not None:
                    state.last_heartbeat = datetime.now(timezone.utc)
            except Exception as exc:
                logger.exception("Strategy '%s' on_candle raised", name)
                await self._handle_strategy_error(name, instance, exc)

    async def on_order_update(self, order_data: dict[str, Any]) -> None:
        """Route an order-status update to the owning strategy."""
        target = order_data.get("strategy")
        if target and target in self._strategies:
            instance = self._strategies[target]
            state = self._states.get(target)
            if state is not None and state.status in (
                StrategyStatus.RUNNING,
                StrategyStatus.PAUSED,
            ):
                try:
                    await instance.on_order_update(order_data)
                    if state is not None:
                        state.orders_today += 1
                except Exception as exc:
                    logger.exception("Strategy '%s' on_order_update raised", target)
                    await self._handle_strategy_error(target, instance, exc)
            return

        # Broadcast to all running strategies
        for name, instance in self._strategies.items():
            state = self._states.get(name)
            if state is None or state.status != StrategyStatus.RUNNING:
                continue
            try:
                await instance.on_order_update(order_data)
            except Exception as exc:
                logger.exception("Strategy '%s' on_order_update raised", name)
                await self._handle_strategy_error(name, instance, exc)

    async def on_trade(self, trade_data: dict[str, Any]) -> None:
        """Route a trade / fill event."""
        target = trade_data.get("strategy")
        if target and target in self._strategies:
            instance = self._strategies[target]
            state = self._states.get(target)
            if state is not None and state.status in (
                StrategyStatus.RUNNING,
                StrategyStatus.PAUSED,
            ):
                try:
                    await instance.on_trade(trade_data)
                except Exception as exc:
                    logger.exception("Strategy '%s' on_trade raised", target)
                    await self._handle_strategy_error(target, instance, exc)
            return

        for name, instance in self._strategies.items():
            state = self._states.get(name)
            if state is None or state.status != StrategyStatus.RUNNING:
                continue
            try:
                await instance.on_trade(trade_data)
            except Exception as exc:
                logger.exception("Strategy '%s' on_trade raised", name)
                await self._handle_strategy_error(name, instance, exc)

    # ------------------------------------------------------------------
    # Subscriptions — strategies declare which symbols they care about
    # ------------------------------------------------------------------

    def subscribe_symbols(self, strategy_name: str, symbols: set[str]) -> None:
        """Register symbol subscriptions for a strategy.

        If the set is empty, the strategy receives ticks/candles for **all**
        symbols.
        """
        if strategy_name not in self._strategies:
            raise KeyError(f"Strategy '{strategy_name}' is not loaded")
        self._subscriptions[strategy_name] = set(symbols)
        logger.debug(
            "Strategy '%s' subscribed to symbols: %s",
            strategy_name,
            symbols or "<all>",
        )

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_strategy_state(self, name: str) -> StrategyState | None:
        return self._states.get(name)

    def get_all_states(self) -> dict[str, StrategyState]:
        return dict(self._states)

    def get_running_strategies(self) -> list[str]:
        return [
            name for name, state in self._states.items()
            if state.status == StrategyStatus.RUNNING
        ]

    def get_strategy_instance(self, name: str) -> BaseStrategy | None:
        return self._strategies.get(name)

    # ------------------------------------------------------------------
    # Sub-component access
    # ------------------------------------------------------------------

    @property
    def scheduler(self) -> StrategyScheduler:
        return self._scheduler

    @property
    def parameter_store(self) -> ParameterStore:
        return self._parameter_store

    # ------------------------------------------------------------------
    # Event-bus wiring — call once at platform startup
    # ------------------------------------------------------------------

    async def wire_event_bus(self) -> None:
        """Subscribe to event-bus topics and forward events to strategies."""
        sid = await self._event_bus.subscribe(
            topic=Topics.TICKS,
            handler=self._on_bus_tick,
            subscriber_id="strategy-runner-ticks",
        )
        self._bus_sub_ids.append(sid)

        sid = await self._event_bus.subscribe(
            topic=Topics.CANDLES,
            handler=self._on_bus_candle,
            subscriber_id="strategy-runner-candles",
        )
        self._bus_sub_ids.append(sid)

        sid = await self._event_bus.subscribe(
            topic=Topics.ORDERS,
            handler=self._on_bus_order,
            subscriber_id="strategy-runner-orders",
        )
        self._bus_sub_ids.append(sid)

        sid = await self._event_bus.subscribe(
            topic=Topics.TRADES,
            handler=self._on_bus_trade,
            subscriber_id="strategy-runner-trades",
        )
        self._bus_sub_ids.append(sid)

        # Start the scheduler
        await self._scheduler.start()
        logger.info("StrategyRunner wired to event bus")

    async def unwire_event_bus(self) -> None:
        """Unsubscribe from all event-bus topics."""
        for sid in self._bus_sub_ids:
            await self._event_bus.unsubscribe(sid)
        self._bus_sub_ids.clear()

    # -- bus handlers (adapt BusEvent -> runner methods) ----------------

    async def _on_bus_tick(self, event: BusEvent) -> None:
        symbol = event.payload.get("symbol", "")
        await self.on_tick(symbol, event.payload)

    async def _on_bus_candle(self, event: BusEvent) -> None:
        symbol = event.payload.get("symbol", "")
        timeframe = event.payload.get("timeframe", "")
        await self.on_candle(symbol, timeframe, event.payload)

    async def _on_bus_order(self, event: BusEvent) -> None:
        await self.on_order_update(event.payload)

    async def _on_bus_trade(self, event: BusEvent) -> None:
        await self.on_trade(event.payload)

    # ------------------------------------------------------------------
    # Context builder
    # ------------------------------------------------------------------

    def _build_context(
        self, name: str, config: StrategyConfig, strategy_id: str
    ) -> _LiveStrategyContext:
        return _LiveStrategyContext(
            strategy_id=strategy_id,
            strategy_name=name,
            mode=config.mode,
            params=dict(config.params),
            order_submit_fn=self._order_submit_fn,
            market_data_fn=self._market_data_fn,
            risk_check_fn=self._risk_check_fn,
            event_bus=self._event_bus,
            scheduler=self._scheduler,
            parameter_store=self._parameter_store,
        )

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    async def _handle_strategy_error(
        self, name: str, instance: BaseStrategy, exc: Exception
    ) -> None:
        """Transition a strategy to ERROR state and notify via event bus."""
        state = self._states.get(name)
        if state is None:
            return

        state.status = StrategyStatus.ERROR
        state.error_message = f"{type(exc).__name__}: {exc}"

        try:
            await instance.on_error(exc)
        except Exception:
            logger.exception("Strategy '%s' on_error also raised", name)

        await self._publish_status_event(name, StrategyStatus.ERROR, str(exc))

    async def _publish_status_event(
        self,
        name: str,
        status: StrategyStatus,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"strategy": name, "status": status.value}
        if error:
            payload["error"] = error
        try:
            await self._event_bus.publish(
                topic=Topics.STRATEGY_STATUS,
                event_type="status_change",
                payload=payload,
                source="strategy-runner",
                priority=EventPriority.HIGH if status == StrategyStatus.ERROR else EventPriority.NORMAL,
            )
        except Exception:
            logger.exception("Failed to publish strategy status event for '%s'", name)
