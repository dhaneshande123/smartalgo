"""
Strategy Scheduler — time-based scheduling for strategy actions.

Supports interval-based scheduling (e.g. "every 5 minutes"), one-time
scheduling (e.g. "at 9:20 AM IST"), and market-hours-only gating so that
jobs do not fire outside trading hours.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from core.constants import IST, MARKET_OPEN, MARKET_CLOSE

logger = logging.getLogger(__name__)

# Type alias for async callback
JobCallback = Callable[[], Awaitable[None]]


@dataclass
class ScheduledJob:
    """Describes a single scheduled job."""

    job_id: str
    strategy_name: str
    job_name: str
    schedule_type: str  # "interval", "once"
    callback: JobCallback
    interval_seconds: float = 0.0
    run_at: datetime | None = None
    next_run: datetime | None = None
    last_run: datetime | None = None
    active: bool = True
    market_hours_only: bool = True
    _metadata: dict[str, Any] = field(default_factory=dict)


class StrategyScheduler:
    """Time-based scheduler for strategy actions.

    The scheduler runs a single asyncio background loop that checks all
    registered jobs roughly every second.  When a job's ``next_run`` time
    has arrived (and market-hours constraints are satisfied), the job's
    async callback is invoked.

    Supports:
    - **Interval-based** scheduling ("every 5 minutes")
    - **One-time** scheduling ("at 9:20 AM IST")
    - **Market-hours-only** gating (default: enabled)

    Usage::

        scheduler = StrategyScheduler()
        scheduler.schedule_interval("strat_1", "rebalance", callback, 300)
        scheduler.schedule_once("strat_1", "entry", callback, run_at)
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJob] = {}
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Scheduling API
    # ------------------------------------------------------------------

    def schedule_interval(
        self,
        strategy_name: str,
        job_name: str,
        callback: JobCallback,
        interval_seconds: float,
        market_hours_only: bool = True,
    ) -> str:
        """Schedule *callback* to run every *interval_seconds*.

        Returns the job_id.  The first invocation happens *interval_seconds*
        after the job is registered (or after the next market-open if
        *market_hours_only* is ``True`` and the market is currently closed).
        """
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

        job_id = self._make_job_id(strategy_name, job_name)
        now = datetime.now(timezone.utc)
        job = ScheduledJob(
            job_id=job_id,
            strategy_name=strategy_name,
            job_name=job_name,
            schedule_type="interval",
            callback=callback,
            interval_seconds=interval_seconds,
            next_run=now + _seconds_delta(interval_seconds),
            market_hours_only=market_hours_only,
            active=True,
        )
        self._jobs[job_id] = job
        logger.info(
            "Scheduled interval job %s for %s every %.1fs (market_hours_only=%s)",
            job_id, strategy_name, interval_seconds, market_hours_only,
        )
        return job_id

    def schedule_once(
        self,
        strategy_name: str,
        job_name: str,
        callback: JobCallback,
        run_at: datetime,
        market_hours_only: bool = False,
    ) -> str:
        """Schedule *callback* to run once at *run_at* (timezone-aware).

        Returns the job_id.  After firing, the job is automatically
        deactivated.
        """
        if run_at.tzinfo is None:
            raise ValueError("run_at must be timezone-aware")

        job_id = self._make_job_id(strategy_name, job_name)
        job = ScheduledJob(
            job_id=job_id,
            strategy_name=strategy_name,
            job_name=job_name,
            schedule_type="once",
            callback=callback,
            run_at=run_at,
            next_run=run_at,
            market_hours_only=market_hours_only,
            active=True,
        )
        self._jobs[job_id] = job
        logger.info(
            "Scheduled one-time job %s for %s at %s",
            job_id, strategy_name, run_at.isoformat(),
        )
        return job_id

    def cancel_job(self, job_id: str) -> None:
        """Cancel and remove a job by its *job_id*."""
        job = self._jobs.pop(job_id, None)
        if job is not None:
            logger.info("Cancelled job %s (%s/%s)", job_id, job.strategy_name, job.job_name)

    def cancel_strategy_jobs(self, strategy_name: str) -> None:
        """Cancel **all** jobs belonging to *strategy_name*."""
        to_remove = [
            jid for jid, job in self._jobs.items()
            if job.strategy_name == strategy_name
        ]
        for jid in to_remove:
            self._jobs.pop(jid, None)
        if to_remove:
            logger.info(
                "Cancelled %d jobs for strategy %s", len(to_remove), strategy_name,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduler's background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="strategy-scheduler")
        logger.info("StrategyScheduler started")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("StrategyScheduler stopped")

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_jobs(self, strategy_name: str | None = None) -> list[ScheduledJob]:
        """Return jobs, optionally filtered to *strategy_name*."""
        if strategy_name is None:
            return list(self._jobs.values())
        return [j for j in self._jobs.values() if j.strategy_name == strategy_name]

    def get_job(self, job_id: str) -> ScheduledJob | None:
        """Return a single job by its id."""
        return self._jobs.get(job_id)

    # ------------------------------------------------------------------
    # Market-hours check
    # ------------------------------------------------------------------

    @staticmethod
    def _is_market_hours() -> bool:
        """Return ``True`` if the current IST time is within market hours."""
        now_ist = datetime.now(IST)
        # Weekends
        if now_ist.weekday() >= 5:
            return False
        current_time = now_ist.time()
        return MARKET_OPEN <= current_time <= MARKET_CLOSE

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Background loop — checks all jobs roughly once per second."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler loop error")
            await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        """Evaluate all active jobs and fire those whose time has come."""
        now = datetime.now(timezone.utc)
        for job in list(self._jobs.values()):
            if not job.active:
                continue
            if job.next_run is None:
                continue
            if now < job.next_run:
                continue
            # Market-hours gate
            if job.market_hours_only and not self._is_market_hours():
                continue

            # Fire
            await self._execute_job(job, now)

    async def _execute_job(self, job: ScheduledJob, now: datetime) -> None:
        """Execute a job's callback and update its scheduling metadata."""
        try:
            await job.callback()
            logger.debug("Job %s (%s) executed successfully", job.job_id, job.job_name)
        except Exception:
            logger.exception("Job %s (%s) callback raised", job.job_id, job.job_name)

        job.last_run = now

        if job.schedule_type == "once":
            job.active = False
            job.next_run = None
            logger.info("One-time job %s completed, deactivated", job.job_id)
        elif job.schedule_type == "interval":
            job.next_run = now + _seconds_delta(job.interval_seconds)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_job_id(strategy_name: str, job_name: str) -> str:
        short = uuid.uuid4().hex[:8]
        return f"{strategy_name}.{job_name}.{short}"


def _seconds_delta(seconds: float) -> __import__("datetime").timedelta:
    """Return a timedelta from *seconds* (avoids top-level import clutter)."""
    from datetime import timedelta
    return timedelta(seconds=seconds)
