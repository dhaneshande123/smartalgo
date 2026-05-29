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

# Risk enforcement cadence
EQUITY_SNAPSHOT_INTERVAL = 60.0  # seconds between equity snapshots (1 per minute)
RISK_CHECK_EVERY_N_TICKS = 1     # check risk every tick (5s)


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
        # Risk enforcement state
        self._last_equity_snapshot_at: datetime | None = None
        self._last_breach_alert_at: dict[str, datetime] = {}  # debounce per-limit
        self._auto_kill_armed = True   # set False to disable auto-kill (manual mode)

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

        # 1. Refresh option chains for every underlying that has a RUNNING
        #    strategy. Without this the per-leg LTP lookup in
        #    _refresh_strategy_pnl is stale and P&L gets stuck at zero.
        await self._refresh_option_chains_if_needed()

        # 2. Per-strategy lifecycle processing
        for sid, strat in list(self._deployed.items()):
            try:
                await self._process_strategy(sid, strat, now_t)
            except Exception as e:
                logger.warning(f"Strategy {sid} processing failed: {e}")

        # 3. Risk enforcement (every tick) — check limits, fire alerts, auto-kill
        try:
            await self._enforce_risk_limits()
        except Exception as e:
            logger.warning(f"Risk enforcement tick failed: {e}")

        # 4. Equity snapshot (every ~60s) — drives drawdown tracking
        try:
            await self._maybe_snapshot_equity()
        except Exception as e:
            logger.debug(f"Equity snapshot skipped: {e}")

    async def _refresh_option_chains_if_needed(self) -> None:
        """Pull fresh option chains for underlyings with running strategies.

        The chain results are cached in the ``_fyers_chain_cache`` dict in
        ``core.api`` — same cache the ``_refresh_strategy_pnl`` helper reads
        when computing per-leg P&L. We import lazily to avoid a circular
        import at module load time.
        """
        if not self._live_feed or not self._live_feed.is_connected:
            return

        underlyings = {
            (s.get("underlying") or "NIFTY").upper()
            for s in self._deployed.values()
            if s.get("status") == "RUNNING"
        }
        if not underlyings:
            return

        try:
            # Import locally to avoid circular dependency
            from core import api as _api_mod
        except ImportError:
            return

        for sym in underlyings:
            try:
                chain = await self._live_feed.get_option_chain(sym, strike_count=25)
                if not (chain and chain.get("chain")):
                    continue
                # Re-pair the chain into the same {strike → {call_ltp, put_ltp, …}}
                # format _refresh_strategy_pnl expects.
                raw = chain["chain"]
                by_strike: dict[int, dict] = {}
                for opt in raw:
                    strike = int(opt.get("strike", 0))
                    if not strike:
                        continue
                    if strike not in by_strike:
                        by_strike[strike] = {"strike": strike, "isATM": False}
                    side = "call" if opt.get("option_type") == "CE" else "put"
                    by_strike[strike][f"{side}_ltp"] = float(opt.get("ltp", 0))
                    by_strike[strike][f"{side}_iv"] = float(opt.get("iv", 0) or 0)
                    by_strike[strike][f"{side}_oi"] = int(opt.get("oi", 0))
                    by_strike[strike][f"{side}_volume"] = int(opt.get("volume", 0))
                    by_strike[strike][f"{side}_oi_change"] = int(opt.get("oi_change", 0))
                    by_strike[strike][f"{side}_oi_change_pct"] = float(opt.get("oi_change_pct", 0))
                    by_strike[strike][f"{side}_change"] = float(opt.get("change", 0))
                    by_strike[strike][f"{side}_change_pct"] = float(opt.get("change_pct", 0))
                    by_strike[strike][f"{side}_bid"] = float(opt.get("bid", 0))
                    by_strike[strike][f"{side}_ask"] = float(opt.get("ask", 0))
                    by_strike[strike][f"{side}_prev_oi"] = int(opt.get("prev_oi", 0))

                # Mark ATM strike
                atm = chain.get("atm_strike", 0)
                for row in by_strike.values():
                    row["isATM"] = abs(row["strike"] - atm) < 25

                chain["chain"] = sorted(by_strike.values(), key=lambda r: r["strike"])
                _api_mod._fyers_chain_cache[sym] = chain
                # also stash by symbol:'' key the cache lookup uses
                _api_mod._fyers_chain_cache[f"{sym}:"] = chain
            except Exception as e:
                logger.debug(f"Chain refresh for {sym} failed: {e}")

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

        **AI auto-deployed strategies**: When the AI signal engine deploys a
        strategy it has already evaluated regime fit, IV rank, ADX, VIX, and
        win rate. Re-evaluating strict template conditions would double-gate
        entry and almost always fail (the AI uses partial-credit scoring, but
        template conditions are exact-match). So strategies with
        ``ai_deployed=True`` enter immediately within their schedule window.
        """
        # AI-deployed strategies: the AI's confidence score IS the entry condition
        if strat.get("ai_deployed"):
            conf = strat.get("ai_confidence", 0)
            strat["last_condition_check"] = {
                "at": datetime.now(IST).isoformat(),
                "passed": True,
                "summary": f"AI auto-entry (confidence {conf}%, signal {strat.get('ai_signal', '?')})",
                "results": [{"indicator": "AI_SIGNAL", "passed": True,
                             "reason": f"AI scored {conf}% → immediate entry"}],
            }
            logger.info(f"Strategy {sid} AI auto-entry (conf={conf}%)")
            return True

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

        # Overwrite each leg's entry_price with the current option-chain LTP
        # so paper P&L reflects realistic entry fills. The template/AI premiums
        # are just placeholders; the real market price at entry IS what gets
        # recorded. Falls back to an inline chain fetch if the cache is empty.
        try:
            await self._fill_entry_prices_from_chain(sid, strat, positions)
        except Exception as e:
            logger.warning(f"Entry-price fill from chain failed for {sid}: {e}")

        strat["entered"] = True
        strat["entered_at"] = datetime.now(IST).isoformat()
        strat["entry_orders"] = entry_orders
        strat["_high_water_mark"] = 0.0
        logger.info(
            f"Strategy {sid} ENTERED ({execution_mode}) — {len(entry_orders)} legs, "
            f"name={strat.get('name')}"
        )

        # Persist entry trades and updated strategy to SQLite
        try:
            from core.state_store import get_store
            store = get_store()
            for pos in positions:
                store.log_trade(sid, "ENTRY", pos["side"], pos["symbol"], pos["qty"], pos.get("entry_price", 0))
            store.save_strategy(sid, strat)
        except Exception:
            pass

    async def _fill_entry_prices_from_chain(
        self, sid: str, strat: dict, positions: list[dict]
    ) -> None:
        """Set each position's entry_price to the actual current option-chain
        LTP so paper P&L reflects realistic entry fills.

        Strategy:
        1. Try the in-memory cache populated by ``_refresh_option_chains_if_needed``
        2. If empty/stale, fetch the chain inline (with timeout)
        3. For each leg, look up its strike+option_type in the chain and
           overwrite entry_price + ltp with the live premium
        """
        if not positions:
            return

        from core import api as _api_mod

        underlying = (strat.get("underlying") or "NIFTY").upper()
        chain = _api_mod._fyers_chain_cache.get(underlying)

        # If cache is empty, fetch inline (blocking with timeout)
        if not (chain and chain.get("chain")):
            if self._live_feed and self._live_feed.is_connected:
                try:
                    chain = await asyncio.wait_for(
                        self._live_feed.get_option_chain(underlying, strike_count=25),
                        timeout=8.0,
                    )
                    if chain and chain.get("chain"):
                        # Build the same {call_ltp, put_ltp} per-strike format
                        raw = chain["chain"]
                        by_strike: dict[int, dict] = {}
                        for opt in raw:
                            strike = int(opt.get("strike", 0))
                            if not strike:
                                continue
                            if strike not in by_strike:
                                by_strike[strike] = {"strike": strike}
                            side = "call" if opt.get("option_type") == "CE" else "put"
                            by_strike[strike][f"{side}_ltp"] = float(opt.get("ltp", 0))
                        chain["chain"] = sorted(by_strike.values(), key=lambda r: r["strike"])
                        _api_mod._fyers_chain_cache[underlying] = chain
                    else:
                        chain = None
                except asyncio.TimeoutError:
                    logger.warning(f"Strategy {sid}: option-chain inline fetch timed out")
                    chain = None
                except Exception as e:
                    logger.warning(f"Strategy {sid}: option-chain inline fetch failed: {e}")
                    chain = None

        if not (chain and chain.get("chain")):
            logger.warning(
                f"Strategy {sid}: entered with template entry prices (chain unavailable). "
                f"P&L will be inaccurate until chain becomes available."
            )
            return

        lookup = {
            int(r["strike"]): r
            for r in chain["chain"]
            if isinstance(r, dict) and "strike" in r
        }

        filled_count = 0
        for pos in positions:
            strike = pos.get("strike")
            if not strike:
                parts = (pos.get("symbol") or "").split()
                if len(parts) >= 2:
                    try:
                        strike = int(parts[1])
                    except ValueError:
                        continue
            if not strike:
                continue
            row = lookup.get(int(strike))
            if not row:
                logger.debug(f"Strategy {sid}: strike {strike} not in chain")
                continue

            sym = (pos.get("symbol") or "").upper()
            opt_type = "CE" if sym.endswith(" CE") or sym.endswith("CE") else "PE"
            key = "call_ltp" if opt_type == "CE" else "put_ltp"
            ltp = float(row.get(key, 0) or 0)
            if ltp > 0:
                pos["entry_price"] = round(ltp, 2)
                pos["ltp"] = round(ltp, 2)
                pos["pnl"] = 0.0
                filled_count += 1

        if filled_count:
            logger.info(
                f"Strategy {sid}: filled {filled_count}/{len(positions)} entry prices "
                f"from live chain"
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

        # Persist exit trades and updated strategy to SQLite
        try:
            from core.state_store import get_store
            store = get_store()
            for pos in positions:
                store.log_trade(sid, "EXIT", "SELL" if pos["side"] == "BUY" else "BUY", pos["symbol"], pos["qty"], float(pos.get("ltp", 0)), reason=reason)
            store.save_strategy(sid, strat)
        except Exception:
            pass

    # ── Risk Enforcement ────────────────────────────────────────────────

    async def _enforce_risk_limits(self) -> None:
        """Each tick: aggregate portfolio metrics, check against limits, fire
        WebSocket alerts at 80% (WARN), auto-kill at 100% BREACH for daily-loss
        and drawdown limits (conservative mode).
        """
        try:
            from core import risk_engine as re
            from core import api as api_mod
            from core.state_store import get_store
            store = get_store()
        except ImportError:
            return

        # Already killed → nothing to enforce
        if store.is_kill_switch_active():
            return

        # Gather inputs
        try:
            limits = {**re.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}
            chain_cache = getattr(api_mod, "_fyers_chain_cache", {}) or {}
            lot_sizes = {}
            try:
                from core import symbol_master
                for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
                    lot_sizes[sym] = symbol_master.get_lot_size(sym) or 1
            except Exception:
                pass

            agg = re.aggregate_portfolio_greeks(
                self._deployed, chain_cache, lot_sizes=lot_sizes,
            )
            pf = agg["portfolio"]

            # Daily P&L
            today_iso = datetime.now(IST).date().isoformat()
            daily_pnl = 0.0
            for strat in self._deployed.values():
                entered_at = strat.get("entered_at", "")
                if entered_at and not entered_at.startswith(today_iso):
                    continue
                daily_pnl += float(strat.get("pnl", 0) or 0)
            daily_loss = abs(min(0.0, daily_pnl))

            # Drawdown from snapshots
            eq_curve = [r["equity"] for r in store.get_equity_curve(limit=500)]
            dd_pct = 0.0
            if eq_curve:
                dd_pct = re.calculate_drawdown(eq_curve)["drawdown_pct"]

            # Build metrics dict & check
            metrics = {
                "daily_loss_used": daily_loss,
                "current_drawdown_pct": dd_pct,
                "net_delta": pf["delta"],
                "net_gamma": pf["gamma"],
                "net_vega": pf["vega"],
                "net_theta": pf["theta"],
                "open_strategies_count": agg["strategies_count"],
            }
            breaches = re.check_risk_limits(metrics, limits)
        except Exception as e:
            logger.warning(f"Risk metric gathering failed: {e}")
            return

        if not breaches:
            return

        # Fire alerts (debounced 60s per limit)
        now = datetime.now(IST)
        for b in breaches:
            last = self._last_breach_alert_at.get(b.limit_name)
            if last and (now - last).total_seconds() < 60:
                continue
            self._last_breach_alert_at[b.limit_name] = now

            # Log to audit
            try:
                store.log_risk_event(
                    event_type=("BREACH" if b.severity == "BREACH" else "WARN"),
                    severity=b.severity,
                    limit_name=b.limit_name,
                    current_value=b.current_value,
                    limit_value=b.limit_value,
                    utilization_pct=b.utilization_pct,
                    message=b.message,
                )
            except Exception:
                pass

            # Broadcast WebSocket alert
            await self._broadcast_risk_alert({
                "type": "risk_breach",
                "severity": b.severity,
                "limit_name": b.limit_name,
                "current_value": b.current_value,
                "limit_value": b.limit_value,
                "utilization_pct": b.utilization_pct,
                "message": b.message,
                "timestamp": now.isoformat(),
            })

            level = "warning" if b.severity == "WARN" else "error"
            logger.log(
                logging.WARNING if level == "warning" else logging.ERROR,
                f"RISK {b.severity}: {b.message}",
            )

        # Auto-kill check (conservative mode: kill only on daily_loss / drawdown breach)
        if self._auto_kill_armed and re.should_auto_kill(breaches, limits):
            await self._trigger_auto_kill(breaches)

    async def _trigger_auto_kill(self, breaches: list) -> None:
        """Auto-engage kill switch + square off all open strategies."""
        try:
            from core.state_store import get_store
            store = get_store()
            if store.is_kill_switch_active():
                return  # already killed
            offending = [b.limit_name for b in breaches if b.severity == "BREACH"]
            reason = f"auto_kill: {', '.join(offending)}"
            store.set_kill_switch(True, reason=reason, triggered_by="auto")
            store.log_risk_event(
                event_type="AUTO_KILL", severity="CRITICAL",
                limit_name=",".join(offending), message=reason,
                metadata={"breaches": offending},
            )

            # Square off every running/entered strategy
            squared = []
            for sid, strat in list(self._deployed.items()):
                status = str(strat.get("status", "")).upper()
                if status in ("RUNNING", "ENTERED"):
                    try:
                        await self._place_exit_orders(sid, strat, reason="auto_kill_switch")
                        squared.append(sid)
                    except Exception as e:
                        logger.warning(f"Auto-kill: stop {sid} failed: {e}")

            await self._broadcast_risk_alert({
                "type": "kill_switch",
                "active": True,
                "auto": True,
                "reason": reason,
                "squared_off_count": len(squared),
                "timestamp": datetime.now(IST).isoformat(),
            })
            logger.critical(f"AUTO-KILL TRIGGERED: {reason}. Stopped {len(squared)} strategies.")
        except Exception as e:
            logger.error(f"Auto-kill failed: {e}", exc_info=True)

    async def _maybe_snapshot_equity(self) -> None:
        """Persist an equity snapshot once every EQUITY_SNAPSHOT_INTERVAL seconds."""
        now = datetime.now(IST)
        if self._last_equity_snapshot_at:
            elapsed = (now - self._last_equity_snapshot_at).total_seconds()
            if elapsed < EQUITY_SNAPSHOT_INTERVAL:
                return
        try:
            from core.state_store import get_store
            from core import api as api_mod, risk_engine as re
            store = get_store()

            # Starting capital (paper session or default)
            starting_capital = 1_000_000.0
            try:
                pm = getattr(api_mod, "_paper_manager", None) or api_mod.__dict__.get("_paper_mgr")
                if pm and getattr(pm, "_active_session", None):
                    starting_capital = float(pm._active_session.get("starting_capital", 1_000_000.0))
            except Exception:
                pass

            # Sum P&L of all entered strategies (today only)
            today_iso = now.date().isoformat()
            unrealized = 0.0
            realized = 0.0
            for strat in self._deployed.values():
                entered_at = strat.get("entered_at", "")
                if entered_at and not entered_at.startswith(today_iso):
                    continue
                status = str(strat.get("status", "")).upper()
                if status in ("EXITED", "STOPPED"):
                    realized += float(strat.get("realized_pnl", strat.get("pnl", 0)) or 0)
                else:
                    unrealized += float(strat.get("pnl", 0) or 0)
            daily_pnl = unrealized + realized
            equity = starting_capital + daily_pnl

            # Margin used
            chain_cache = getattr(api_mod, "_fyers_chain_cache", {}) or {}
            lot_sizes = {}
            try:
                from core import symbol_master
                for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
                    lot_sizes[sym] = symbol_master.get_lot_size(sym) or 1
            except Exception:
                pass
            margin = re.calculate_margin(
                self._deployed, chain_cache, lot_sizes=lot_sizes,
                available_capital=starting_capital,
            )

            open_count = sum(
                1 for s in self._deployed.values()
                if str(s.get("status", "")).upper() in ("RUNNING", "ENTERED")
            )

            store.save_equity_snapshot(
                equity=equity,
                daily_pnl=daily_pnl,
                cash=starting_capital - margin.get("total_margin_required", 0),
                margin_used=margin.get("total_margin_required", 0),
                open_strategies=open_count,
            )
            self._last_equity_snapshot_at = now
        except Exception as e:
            logger.debug(f"Equity snapshot failed: {e}")

    async def _broadcast_risk_alert(self, payload: dict) -> None:
        """Broadcast a risk alert via WebSocket if available."""
        try:
            from core import api as api_mod
            ws = getattr(api_mod, "_ws_manager", None)
            if ws and hasattr(ws, "broadcast"):
                await ws.broadcast(payload)
        except Exception as e:
            logger.debug(f"Risk alert broadcast failed: {e}")

    # ── Public helpers (used by api.py deploy endpoints) ────────────────

    def is_blocked_for_new_deploys(self) -> tuple[bool, str]:
        """Return (blocked, reason) — used by deploy endpoints to refuse new
        strategies when the kill switch is active.
        """
        try:
            from core.state_store import get_store
            store = get_store()
            if store.is_kill_switch_active():
                meta = store.get_kill_switch_meta() or {}
                reason = meta.get("reason", "kill switch active")
                return True, f"Kill switch active: {reason}"
        except Exception:
            pass
        return False, ""
