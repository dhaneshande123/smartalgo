"""Unit tests for the Strategy Engine (StrategyRunner, StrategyScheduler, ParameterStore).

Tests cover:
- StrategyRunner: register_strategy, start, stop, pause, resume, get_status,
  event routing, error handling
- StrategyScheduler: schedule_interval, schedule_once, cancel_job, list_jobs
- ParameterStore: set/get/delete params, list params, default values,
  history, listeners, partial updates
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from core.models import (
    StrategyConfig,
    StrategyState,
    StrategyStatus,
    TradingMode,
)
from core.strategy_engine.runner import StrategyRunner
from core.strategy_engine.scheduler import StrategyScheduler, ScheduledJob
from core.strategy_engine.parameter_store import ParameterStore
from strategies.base_strategy import BaseStrategy, StrategyContext


# =====================================================================
# Helpers
# =====================================================================


class DummyStrategy(BaseStrategy):
    """Minimal concrete strategy for testing."""

    async def on_init(self, ctx: StrategyContext) -> None:
        self.ctx = ctx
        self.init_called = True

    async def on_start(self) -> None:
        self.start_called = True

    async def on_stop(self) -> None:
        self.stop_called = True

    async def on_pause(self) -> None:
        self.pause_called = True

    async def on_resume(self) -> None:
        self.resume_called = True

    async def on_tick(self, tick_data: dict) -> None:
        self.last_tick = tick_data

    async def on_candle(self, candle_data: dict) -> None:
        self.last_candle = candle_data

    async def on_order_update(self, order_data: dict) -> None:
        self.last_order = order_data

    async def on_trade(self, trade_data: dict) -> None:
        self.last_trade = trade_data

    async def on_error(self, exc: Exception) -> None:
        self.last_error = exc

    async def on_schedule(self, event) -> None:
        pass


class FailingStrategy(BaseStrategy):
    """Strategy that fails during on_init."""

    async def on_init(self, ctx: StrategyContext) -> None:
        raise RuntimeError("Init failed")

    async def on_start(self) -> None:
        pass

    async def on_stop(self) -> None:
        pass

    async def on_tick(self, tick_data: dict) -> None:
        pass

    async def on_candle(self, candle_data: dict) -> None:
        pass

    async def on_order_update(self, order_data: dict) -> None:
        pass

    async def on_trade(self, trade_data: dict) -> None:
        pass

    async def on_error(self, exc: Exception) -> None:
        pass

    async def on_schedule(self, event) -> None:
        pass


def _make_event_bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = AsyncMock(return_value="sub-id")
    bus.unsubscribe = AsyncMock()
    return bus


def _make_runner():
    bus = _make_event_bus()
    order_fn = AsyncMock(return_value="order-ok")
    market_fn = AsyncMock(return_value={})
    risk_fn = AsyncMock()
    runner = StrategyRunner(bus, order_fn, market_fn, risk_fn)
    return runner, bus


def _make_config(name="test_strat", class_path="strategies.dummy.DummyStrategy"):
    return StrategyConfig(
        name=name,
        class_path=class_path,
        mode=TradingMode.PAPER,
        params={"strike_offset": 200, "lots": 2},
    )


# =====================================================================
# StrategyRunner Tests
# =====================================================================


class TestStrategyRunner:

    def test_register_strategy(self):
        runner, _ = _make_runner()
        runner.register_strategy("dummy", DummyStrategy)
        assert "dummy" in runner._registry

    def test_register_strategy_invalid_class(self):
        runner, _ = _make_runner()
        with pytest.raises(TypeError):
            runner.register_strategy("bad", str)

    @pytest.mark.asyncio
    async def test_start_strategy(self):
        runner, bus = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        state = runner.get_strategy_state("test_strat")
        assert state is not None
        assert state.status == StrategyStatus.RUNNING

    @pytest.mark.asyncio
    async def test_start_strategy_already_running(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        with pytest.raises(RuntimeError, match="already running"):
            await runner.start_strategy("test_strat", config)

    @pytest.mark.asyncio
    async def test_start_strategy_init_failure(self):
        runner, bus = _make_runner()
        runner.register_strategy("failing", FailingStrategy)
        config = _make_config(name="failing")
        await runner.start_strategy("failing", config)
        state = runner.get_strategy_state("failing")
        assert state.status == StrategyStatus.ERROR

    @pytest.mark.asyncio
    async def test_stop_strategy(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        await runner.stop_strategy("test_strat")
        state = runner.get_strategy_state("test_strat")
        assert state.status == StrategyStatus.STOPPED

    @pytest.mark.asyncio
    async def test_stop_strategy_not_loaded(self):
        runner, _ = _make_runner()
        with pytest.raises(KeyError, match="not loaded"):
            await runner.stop_strategy("nonexistent")

    @pytest.mark.asyncio
    async def test_pause_strategy(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        await runner.pause_strategy("test_strat")
        state = runner.get_strategy_state("test_strat")
        assert state.status == StrategyStatus.PAUSED

    @pytest.mark.asyncio
    async def test_pause_non_running_strategy_raises(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        await runner.pause_strategy("test_strat")
        with pytest.raises(RuntimeError, match="Cannot pause"):
            await runner.pause_strategy("test_strat")

    @pytest.mark.asyncio
    async def test_resume_strategy(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        await runner.pause_strategy("test_strat")
        await runner.resume_strategy("test_strat")
        state = runner.get_strategy_state("test_strat")
        assert state.status == StrategyStatus.RUNNING

    @pytest.mark.asyncio
    async def test_resume_non_paused_strategy_raises(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        with pytest.raises(RuntimeError, match="Cannot resume"):
            await runner.resume_strategy("test_strat")

    @pytest.mark.asyncio
    async def test_get_strategy_state_returns_none_for_unknown(self):
        runner, _ = _make_runner()
        assert runner.get_strategy_state("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_all_states(self):
        runner, _ = _make_runner()
        runner.register_strategy("s1", DummyStrategy)
        runner.register_strategy("s2", DummyStrategy)
        await runner.start_strategy("s1", _make_config(name="s1"))
        await runner.start_strategy("s2", _make_config(name="s2"))
        states = runner.get_all_states()
        assert "s1" in states
        assert "s2" in states

    @pytest.mark.asyncio
    async def test_get_running_strategies(self):
        runner, _ = _make_runner()
        runner.register_strategy("s1", DummyStrategy)
        runner.register_strategy("s2", DummyStrategy)
        await runner.start_strategy("s1", _make_config(name="s1"))
        await runner.start_strategy("s2", _make_config(name="s2"))
        await runner.pause_strategy("s2")
        running = runner.get_running_strategies()
        assert "s1" in running
        assert "s2" not in running

    @pytest.mark.asyncio
    async def test_on_tick_routes_to_running_strategy(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        tick = {"symbol": "NIFTY", "ltp": 24300}
        await runner.on_tick("NIFTY", tick)
        instance = runner.get_strategy_instance("test_strat")
        assert hasattr(instance, "last_tick")
        assert instance.last_tick == tick

    @pytest.mark.asyncio
    async def test_on_tick_skips_paused_strategy(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        await runner.pause_strategy("test_strat")
        tick = {"symbol": "NIFTY", "ltp": 24300}
        await runner.on_tick("NIFTY", tick)
        instance = runner.get_strategy_instance("test_strat")
        assert not hasattr(instance, "last_tick")

    @pytest.mark.asyncio
    async def test_stop_all(self):
        runner, _ = _make_runner()
        runner.register_strategy("s1", DummyStrategy)
        runner.register_strategy("s2", DummyStrategy)
        await runner.start_strategy("s1", _make_config(name="s1"))
        await runner.start_strategy("s2", _make_config(name="s2"))
        await runner.stop_all()
        assert runner.get_strategy_state("s1").status == StrategyStatus.STOPPED
        assert runner.get_strategy_state("s2").status == StrategyStatus.STOPPED

    @pytest.mark.asyncio
    async def test_subscribe_symbols_filters_ticks(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        runner.subscribe_symbols("test_strat", {"BANKNIFTY"})
        await runner.on_tick("NIFTY", {"symbol": "NIFTY"})
        instance = runner.get_strategy_instance("test_strat")
        assert not hasattr(instance, "last_tick")

    @pytest.mark.asyncio
    async def test_subscribe_symbols_for_unknown_strategy(self):
        runner, _ = _make_runner()
        with pytest.raises(KeyError, match="not loaded"):
            runner.subscribe_symbols("nonexistent", {"NIFTY"})

    @pytest.mark.asyncio
    async def test_strategy_params_stored_on_start(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        params = runner.parameter_store.get("test_strat")
        assert params["strike_offset"] == 200
        assert params["lots"] == 2

    @pytest.mark.asyncio
    async def test_on_order_update_targeted(self):
        runner, _ = _make_runner()
        runner.register_strategy("test_strat", DummyStrategy)
        config = _make_config(name="test_strat")
        await runner.start_strategy("test_strat", config)
        await runner.on_order_update({"strategy": "test_strat", "status": "FILLED"})
        instance = runner.get_strategy_instance("test_strat")
        assert instance.last_order["status"] == "FILLED"

    @pytest.mark.asyncio
    async def test_scheduler_access(self):
        runner, _ = _make_runner()
        assert isinstance(runner.scheduler, StrategyScheduler)

    @pytest.mark.asyncio
    async def test_parameter_store_access(self):
        runner, _ = _make_runner()
        assert isinstance(runner.parameter_store, ParameterStore)


# =====================================================================
# StrategyScheduler Tests
# =====================================================================


class TestStrategyScheduler:

    def test_schedule_interval(self):
        sched = StrategyScheduler()
        callback = AsyncMock()
        job_id = sched.schedule_interval("strat_1", "rebalance", callback, 300)
        assert job_id is not None
        assert "strat_1" in job_id
        assert "rebalance" in job_id

    def test_schedule_interval_invalid_seconds(self):
        sched = StrategyScheduler()
        with pytest.raises(ValueError, match="positive"):
            sched.schedule_interval("strat_1", "bad", AsyncMock(), -1)

    def test_schedule_interval_zero_seconds(self):
        sched = StrategyScheduler()
        with pytest.raises(ValueError, match="positive"):
            sched.schedule_interval("strat_1", "bad", AsyncMock(), 0)

    def test_schedule_once(self):
        sched = StrategyScheduler()
        callback = AsyncMock()
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        job_id = sched.schedule_once("strat_1", "entry", callback, run_at)
        assert job_id is not None

    def test_schedule_once_naive_datetime_raises(self):
        sched = StrategyScheduler()
        with pytest.raises(ValueError, match="timezone-aware"):
            sched.schedule_once("strat_1", "bad", AsyncMock(), datetime(2026, 1, 1))

    def test_cancel_job(self):
        sched = StrategyScheduler()
        job_id = sched.schedule_interval("strat_1", "j1", AsyncMock(), 60)
        sched.cancel_job(job_id)
        assert sched.get_job(job_id) is None

    def test_cancel_job_nonexistent(self):
        sched = StrategyScheduler()
        # Should not raise
        sched.cancel_job("nonexistent-id")

    def test_cancel_strategy_jobs(self):
        sched = StrategyScheduler()
        sched.schedule_interval("strat_1", "j1", AsyncMock(), 60)
        sched.schedule_interval("strat_1", "j2", AsyncMock(), 120)
        sched.schedule_interval("strat_2", "j1", AsyncMock(), 60)
        sched.cancel_strategy_jobs("strat_1")
        jobs = sched.get_jobs("strat_1")
        assert len(jobs) == 0
        jobs2 = sched.get_jobs("strat_2")
        assert len(jobs2) == 1

    def test_get_jobs_all(self):
        sched = StrategyScheduler()
        sched.schedule_interval("s1", "j1", AsyncMock(), 60)
        sched.schedule_interval("s2", "j2", AsyncMock(), 120)
        all_jobs = sched.get_jobs()
        assert len(all_jobs) == 2

    def test_get_jobs_by_strategy(self):
        sched = StrategyScheduler()
        sched.schedule_interval("s1", "j1", AsyncMock(), 60)
        sched.schedule_interval("s2", "j2", AsyncMock(), 120)
        jobs = sched.get_jobs("s1")
        assert len(jobs) == 1
        assert jobs[0].strategy_name == "s1"

    def test_get_job_by_id(self):
        sched = StrategyScheduler()
        job_id = sched.schedule_interval("s1", "j1", AsyncMock(), 60)
        job = sched.get_job(job_id)
        assert job is not None
        assert job.job_id == job_id
        assert job.schedule_type == "interval"

    def test_get_job_returns_none_for_unknown(self):
        sched = StrategyScheduler()
        assert sched.get_job("unknown") is None

    def test_interval_job_has_next_run(self):
        sched = StrategyScheduler()
        job_id = sched.schedule_interval("s1", "j1", AsyncMock(), 60)
        job = sched.get_job(job_id)
        assert job.next_run is not None

    def test_once_job_has_run_at(self):
        sched = StrategyScheduler()
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        job_id = sched.schedule_once("s1", "j1", AsyncMock(), run_at)
        job = sched.get_job(job_id)
        assert job.run_at == run_at
        assert job.next_run == run_at

    @pytest.mark.asyncio
    async def test_start_stop(self):
        sched = StrategyScheduler()
        await sched.start()
        assert sched._running is True
        await sched.stop()
        assert sched._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        sched = StrategyScheduler()
        await sched.start()
        await sched.start()  # should not raise
        assert sched._running is True
        await sched.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        sched = StrategyScheduler()
        await sched.stop()  # should not raise


# =====================================================================
# ParameterStore Tests
# =====================================================================


class TestParameterStore:

    def test_set_and_get(self):
        store = ParameterStore()
        store.set("strat_1", {"strike_offset": 200, "lots": 2})
        params = store.get("strat_1")
        assert params["strike_offset"] == 200
        assert params["lots"] == 2

    def test_get_returns_empty_for_unknown(self):
        store = ParameterStore()
        params = store.get("nonexistent")
        assert params == {}

    def test_get_returns_deep_copy(self):
        store = ParameterStore()
        store.set("strat_1", {"nested": {"a": 1}})
        p1 = store.get("strat_1")
        p1["nested"]["a"] = 999
        p2 = store.get("strat_1")
        assert p2["nested"]["a"] == 1  # original unchanged

    def test_set_stores_deep_copy(self):
        store = ParameterStore()
        original = {"key": [1, 2, 3]}
        store.set("strat_1", original)
        original["key"].append(4)
        params = store.get("strat_1")
        assert params["key"] == [1, 2, 3]  # not mutated

    def test_update_partial(self):
        store = ParameterStore()
        store.set("strat_1", {"a": 1, "b": 2})
        store.update("strat_1", {"b": 99})
        params = store.get("strat_1")
        assert params["a"] == 1
        assert params["b"] == 99

    def test_update_adds_new_keys(self):
        store = ParameterStore()
        store.set("strat_1", {"a": 1})
        store.update("strat_1", {"c": 3})
        params = store.get("strat_1")
        assert params["a"] == 1
        assert params["c"] == 3

    def test_update_on_nonexistent_creates(self):
        store = ParameterStore()
        store.update("strat_1", {"x": 10})
        params = store.get("strat_1")
        assert params["x"] == 10

    def test_remove(self):
        store = ParameterStore()
        store.set("strat_1", {"a": 1})
        store.remove("strat_1")
        assert store.get("strat_1") == {}
        assert not store.has("strat_1")

    def test_remove_nonexistent(self):
        store = ParameterStore()
        store.remove("nonexistent")  # should not raise

    def test_has(self):
        store = ParameterStore()
        assert not store.has("strat_1")
        store.set("strat_1", {"a": 1})
        assert store.has("strat_1")

    def test_list_strategies(self):
        store = ParameterStore()
        store.set("s1", {"a": 1})
        store.set("s2", {"b": 2})
        assert set(store.list_strategies()) == {"s1", "s2"}

    def test_list_strategies_empty(self):
        store = ParameterStore()
        assert store.list_strategies() == []

    def test_history_recorded_on_set(self):
        store = ParameterStore()
        store.set("strat_1", {"a": 1})
        history = store.get_history("strat_1")
        assert len(history) == 1
        ts, params = history[0]
        assert params["a"] == 1

    def test_history_recorded_on_update(self):
        store = ParameterStore()
        store.set("strat_1", {"a": 1})
        store.update("strat_1", {"a": 2})
        history = store.get_history("strat_1")
        assert len(history) == 2

    def test_history_limit(self):
        store = ParameterStore()
        for i in range(20):
            store.set("strat_1", {"v": i})
        history = store.get_history("strat_1", limit=5)
        assert len(history) == 5
        # Should be the most recent 5 (newest last)
        assert history[-1][1]["v"] == 19

    def test_on_change_callback(self):
        store = ParameterStore()
        received = []
        store.on_change("strat_1", lambda name, params: received.append((name, params)))
        store.set("strat_1", {"a": 1})
        assert len(received) == 1
        assert received[0][0] == "strat_1"
        assert received[0][1]["a"] == 1

    def test_on_change_called_on_update(self):
        store = ParameterStore()
        received = []
        store.on_change("strat_1", lambda name, params: received.append(params))
        store.set("strat_1", {"a": 1})
        store.update("strat_1", {"a": 2})
        assert len(received) == 2
        assert received[1]["a"] == 2

    def test_on_change_exception_does_not_break(self):
        store = ParameterStore()

        def bad_listener(name, params):
            raise ValueError("boom")

        store.on_change("strat_1", bad_listener)
        # Should not raise
        store.set("strat_1", {"a": 1})

    def test_remove_listeners(self):
        store = ParameterStore()
        received = []
        store.on_change("strat_1", lambda name, params: received.append(params))
        store.remove_listeners("strat_1")
        store.set("strat_1", {"a": 1})
        assert len(received) == 0
