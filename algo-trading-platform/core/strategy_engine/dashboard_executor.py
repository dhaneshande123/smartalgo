"""
Dashboard Strategy Executor.

Runs deployed strategies created via the dashboard's StrategyBuilder
(``_deployed_strategies`` dict in ``core.api``). These strategies are
template-based (iron condor, straddle, etc.) rather than full Python classes,
so they need a different executor from ``core.strategy_engine.runner``.

What it does
------------
Every ``TICK_INTERVAL`` seconds, the executor iterates over every running
strategy and:

1.  **Schedule check** — if the strategy's scheduled entry time has been
    reached and it has not yet entered, place all entry leg orders.
2.  **Live P&L update** — refresh per-leg LTP and unrealized P&L using
    the Fyers option chain cache.
3.  **Risk checks** — if SL, target, trailing-stop, or max-loss-per-day
    is breached, close all legs.
4.  **End-of-day square-off** — at 15:15 IST, close all intraday legs.

By default the executor places orders through the PaperTradingManager
even when the global trading mode is ``live`` — auto-execution is
paper-only unless a strategy has ``execution_mode: "live"`` explicitly
set on it. This is a safety default per user policy.

This module is intentionally NOT registered as a FastAPI dependency —
the api.py lifespan wires it up directly.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, time as dtime
from typing import Any, Callable

from core.constants import IST

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TICK_INTERVAL = 5.0  # seconds between executor ticks

SCHEDULE_TIMES = {
    "market_open": dtime(9, 20),     # 09:20 IST
    "mid_morning": dtime(10, 30),
    "afternoon": dtime(13, 0),
    "pre_close": dtime(15, 0),
}

SQUARE_OFF_TIME = dtime(15, 15)  # NSE auto square-off buffer
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class DashboardStrategyExecutor:
    """Background task that drives the lifecycle of dashboard-deployed strategies.

    Construct with references to the platform's mutable state (the
    ``_deployed_strategies`` dict from api.py) and helper functions. Call
    :py:meth:`start` once at platform startup and :py:meth:`stop` on shutdown.
    """

    def __init__(
        self,
        deployed_strategies: dict[str, dict],
        refresh_pnl_fn: Callable[[dict], None],
        paper_trading_manager,
        live_feed,
        place_order_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._deployed = deployed_strategies
        self._refresh_pnl = refresh_pnl_fn
        self._paper_mgr = paper_trading_manager
        self._live_feed = live_feed
        self._place_order_fn = place_order_fn  # optional: live Fyers placement

        self._task: asyncio.Task | None = None
        self._running = False
        self._ticks_processed = 0
        self._last_tick_at: datetime | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task and not self._task.done():
            logger.warning("DashboardStrategyExecutor already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="dashboard-strategy-executor")
        logger.info("DashboardStrategyExecutor started (tick interval=%.1fs)", TICK_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DashboardStrategyExecutor stopped")

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "ticks_processed": self._ticks_processed,
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
            "deployed_count": len(self._deployed),
        }

    # ── Main loop ────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Executor tick error: {e}")
            await asyncio.sleep(TICK_INTERVAL)

    async def _tick(self) -> None:
        self._ticks_processed += 1
        self._last_tick_at = datetime.now(IST)
        now_t = self._last_tick_at.time()

        for sid, strat in list(self._deployed.items()):
            try:
                await self._process_strategy(sid, strat, now_t)
            except Exception as e:
                logger.warning(f"Strategy {sid} processing failed: {e}")

    async def _process_strategy(self, sid: str, strat: dict, now_t: dtime) -> None:
        status = strat.get("status", "")
        # Skip stopped / failed strategies
        if status in {"STOPPED", "EXITED", "FAILED"}:
            return

        # ---- 1. Refresh P&L from current LTPs (always)
        self._refresh_pnl(strat)

        # ---- 2. Entry: only if within the schedule window AND entry conditions pass
        if not strat.get("entered", False):
            if self._within_entry_window(strat, now_t):
                if await self._entry_conditions_met(sid, strat):
                    await self._place_entry_orders(sid, strat)

        # ---- 3. Risk checks (only after entry)
        if strat.get("entered", False):
            exit_reason = self._check_exit_conditions(strat, now_t)
            if exit_reason:
                await self._place_exit_orders(sid, strat, reason=exit_reason)

    # ── Entry gating ─────────────────────────────────────────────────────

    def _within_entry_window(self, strat: dict, now_t: dtime) -> bool:
        """Return True if the current time is within the strategy's allowed
        entry window. Two modes are supported:

        1. **Explicit window**: ``schedule_window: ["09:20", "15:00"]`` — only
           consider entries within this range.
        2. **Legacy schedule**: ``schedule: "market_open"`` (or mid_morning /
           afternoon / pre_close / custom) — entry-time-or-after, while market
           is open.

        If neither is set, defaults to "09:20–15:00 IST".
        """
        # Always block outside market hours
        if not (MARKET_OPEN <= now_t < SQUARE_OFF_TIME):
            return False

        window = strat.get("schedule_window") or strat.get("scheduleWindow")
        if isinstance(window, (list, tuple)) and len(window) == 2:
            try:
                start = self._parse_hhmm(window[0]) or dtime(9, 20)
                end = self._parse_hhmm(window[1]) or dtime(15, 0)
                return start <= now_t < end
            except Exception:
                return True  # malformed window → be permissive

        # Legacy schedule field
        schedule = strat.get("schedule", "market_open")
        if schedule == "custom":
            custom = strat.get("custom_time") or strat.get("customTime") or "09:20"
            target = self._parse_hhmm(custom) or SCHEDULE_TIMES["market_open"]
        else:
            target = SCHEDULE_TIMES.get(schedule, SCHEDULE_TIMES["market_open"])

        # If a schedule is set, the strategy is allowed to enter from that
        # time onwards (until square-off). If the strategy ALSO has entry
        # conditions, those still need to pass — schedule is just the window.
        return now_t >= target

    @staticmethod
    def _parse_hhmm(s: str) -> dtime | None:
        try:
            h, m = s.strip().split(":")
            return dtime(int(h), int(m))
        except Exception:
            return None

    async def _entry_conditions_met(self, sid: str, strat: dict) -> bool:
        """If the strategy has ``entry_conditions``, evaluate them against
        live market data. If it has none, return True (schedule alone decides).
        """
        conditions = strat.get("entry_conditions") or strat.get("entryConditions") or []
        if not conditions:
            return True

        trigger = strat.get("entry_trigger") or strat.get("entryTrigger") or "ALL"
        underlying = strat.get("underlying", "NIFTY")

        try:
            from core.condition_evaluator import evaluate_conditions
            result = await evaluate_conditions(
                symbol=underlying,
                conditions=conditions,
                trigger=trigger,
                live_feed=self._live_feed,
                timeframe=strat.get("condition_timeframe", "M5"),
            )
            # Store the latest evaluation for the UI
            strat["last_condition_check"] = {
                "at": datetime.now(IST).isoformat(),
                "passed": result["passed"],
                "summary": result["summary"],
                "results": result["results"],
            }
            if not result["passed"]:
                # Conditions failed → wait
                return False
            logger.info(f"Strategy {sid} entry conditions PASSED: {result['summary']}")
            return True
        except Exception as e:
            logger.warning(f"Condition evaluation failed for {sid}: {e}")
            return False

    # ── Exit conditions ──────────────────────────────────────────────────

    def _check_exit_conditions(self, strat: dict, now_t: dtime) -> str | None:
        """Return a reason string if any exit condition is met, else None."""
        risk = strat.get("risk_params", {}) or {}
        pnl = float(strat.get("pnl", 0.0))

        # End-of-day square-off (highest priority)
        if now_t >= SQUARE_OFF_TIME:
            return "eod_square_off"

        # Max loss per trade
        max_loss = self._get_risk_value(risk, "maxLossPerTrade", "max_loss")
        if max_loss is not None and pnl <= -abs(max_loss):
            return f"stop_loss_hit (pnl={pnl:.0f} ≤ -{abs(max_loss):.0f})"

        # Target profit
        target = self._get_risk_value(risk, "target", "targetProfit", "target_profit", "targetPct")
        if target is not None and pnl >= abs(target):
            return f"target_hit (pnl={pnl:.0f} ≥ {abs(target):.0f})"

        # Trailing stop
        hwm = strat.get("_high_water_mark", 0.0)
        if pnl > hwm:
            strat["_high_water_mark"] = pnl
            hwm = pnl

        trailing_pct = self._get_risk_value(risk, "trailingStopPct", "trailing_stop_pct")
        if trailing_pct and hwm > 0:
            drawdown = (hwm - pnl) / hwm if hwm > 0 else 0
            if drawdown * 100 >= float(trailing_pct):
                return f"trailing_stop_hit (drawdown={drawdown*100:.1f}% from peak ₹{hwm:.0f})"

        return None

    @staticmethod
    def _get_risk_value(risk: dict, *keys: str) -> float | None:
        """First non-None value across the given keys, coerced to float."""
        for k in keys:
            if k in risk and risk[k] is not None:
                try:
                    return float(risk[k])
                except (ValueError, TypeError):
                    continue
        return None

    # ── Order placement ─────────────────────────────────────────────────

    async def _place_entry_orders(self, sid: str, strat: dict) -> None:
        """Place all entry leg orders for a strategy.

        For now this only updates internal state — the positions list was
        already created at deploy time. We mark ``entered=True`` and record
        ``entered_at``. Real Fyers order placement happens via the place_order
        function only if execution_mode is "live".
        """
        positions = strat.get("positions", [])
        if not positions:
            logger.warning(f"Strategy {sid} has no positions to enter")
            return

        execution_mode = (strat.get("execution_mode") or strat.get("mode") or "paper").lower()

        entry_orders = []
        if execution_mode == "live" and self._place_order_fn and self._live_feed:
            # Place real Fyers orders per leg
            for pos in positions:
                try:
                    order = await self._place_order_fn({
                        "symbol": pos["symbol"],
                        "side": pos["side"],
                        "qty": pos["qty"],
                        "orderType": "MARKET",
                        "product": "MIS",
                        "mode": "live",
                    })
                    if order and order.get("order_id"):
                        entry_orders.append(order["order_id"])
                except Exception as e:
                    logger.error(f"Strategy {sid} leg entry failed for {pos['symbol']}: {e}")
        else:
            # Paper mode: synthetic order IDs, broker simulates fills via PaperBroker
            for pos in positions:
                entry_orders.append(f"paper-{uuid.uuid4().hex[:10]}")

        strat["entered"] = True
        strat["entered_at"] = datetime.now(IST).isoformat()
        strat["entry_orders"] = entry_orders
        strat["_high_water_mark"] = 0.0
        logger.info(
            f"Strategy {sid} ENTERED ({execution_mode}) — {len(entry_orders)} legs, "
            f"name={strat.get('name')}"
        )

    async def _place_exit_orders(self, sid: str, strat: dict, *, reason: str) -> None:
        """Square off all open legs by placing reverse orders."""
        positions = strat.get("positions", [])
        execution_mode = (strat.get("execution_mode") or strat.get("mode") or "paper").lower()

        exit_orders = []
        if execution_mode == "live" and self._place_order_fn and self._live_feed:
            for pos in positions:
                try:
                    # Reverse the side to close: BUY → SELL, SELL → BUY
                    close_side = "SELL" if pos["side"] == "BUY" else "BUY"
                    order = await self._place_order_fn({
                        "symbol": pos["symbol"],
                        "side": close_side,
                        "qty": pos["qty"],
                        "orderType": "MARKET",
                        "product": "MIS",
                        "mode": "live",
                    })
                    if order and order.get("order_id"):
                        exit_orders.append(order["order_id"])
                except Exception as e:
                    logger.error(f"Strategy {sid} leg exit failed for {pos['symbol']}: {e}")
        else:
            for pos in positions:
                exit_orders.append(f"paper-exit-{uuid.uuid4().hex[:10]}")

        # Lock in the realized P&L at exit
        final_pnl = float(strat.get("pnl", 0.0))
        strat["realized_pnl"] = final_pnl
        strat["unrealized_pnl"] = 0.0
        strat["pnl"] = final_pnl
        strat["status"] = "EXITED"
        strat["exit_reason"] = reason
        strat["exit_orders"] = exit_orders
        strat["exited_at"] = datetime.now(IST).isoformat()

        logger.info(
            f"Strategy {sid} EXITED ({execution_mode}) — reason={reason}, "
            f"final_pnl=₹{final_pnl:.2f}"
        )
