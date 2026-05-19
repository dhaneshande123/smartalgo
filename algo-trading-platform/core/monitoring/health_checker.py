"""
System health monitoring for the algo trading platform.

Checks broker connections, market data feeds, strategy health,
system resources, and event bus queue depths. Supports periodic
background checks and exposes a unified health status view.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Health status dataclass
# ---------------------------------------------------------------------------


@dataclass
class HealthStatus:
    """Represents the health state of a single component."""

    component: str
    status: str  # "healthy", "degraded", "unhealthy"
    message: str = ""
    last_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for API / event payloads."""
        return {
            "component": self.component,
            "status": self.status,
            "message": self.message,
            "last_check": self.last_check.isoformat(),
            "metrics": self.metrics,
        }


# Type alias for health check functions.  Each check receives no arguments
# and must return a ``HealthStatus``.
HealthCheckFn = Callable[[], Awaitable[HealthStatus]]


# ---------------------------------------------------------------------------
# Status ranking helper
# ---------------------------------------------------------------------------

_STATUS_RANK: dict[str, int] = {
    "healthy": 0,
    "degraded": 1,
    "unhealthy": 2,
}


def _worst_status(statuses: list[str]) -> str:
    """Return the worst status from a list of status strings."""
    if not statuses:
        return "healthy"
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 2))


# ---------------------------------------------------------------------------
# Built-in check helpers
# ---------------------------------------------------------------------------


async def _check_system_resources() -> HealthStatus:
    """Built-in check that reports memory and basic system metrics.

    Uses only the stdlib so there is no hard dependency on ``psutil``.
    If ``psutil`` is available it will provide richer data; otherwise
    a degraded-but-functional report is returned.
    """
    metrics: dict[str, Any] = {
        "platform": platform.system(),
        "python_version": platform.python_version(),
    }
    status = "healthy"
    message = ""

    try:
        import psutil  # type: ignore[import-untyped]

        mem = psutil.virtual_memory()
        cpu_pct = psutil.cpu_percent(interval=0.1)
        metrics["memory_total_mb"] = round(mem.total / (1024 * 1024), 1)
        metrics["memory_used_pct"] = mem.percent
        metrics["cpu_percent"] = cpu_pct

        if mem.percent > 90:
            status = "unhealthy"
            message = f"Memory usage critical: {mem.percent}%"
        elif mem.percent > 75:
            status = "degraded"
            message = f"Memory usage elevated: {mem.percent}%"

        if cpu_pct > 95:
            status = _worst_status([status, "unhealthy"])
            message = (message + "; " if message else "") + f"CPU critical: {cpu_pct}%"
        elif cpu_pct > 80:
            status = _worst_status([status, "degraded"])
            message = (message + "; " if message else "") + f"CPU elevated: {cpu_pct}%"
    except ImportError:
        # psutil not installed — report what we can
        metrics["note"] = "psutil not installed; limited system metrics"

    if not message:
        message = "System resources normal"

    return HealthStatus(
        component="system_resources",
        status=status,
        message=message,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# HealthChecker
# ---------------------------------------------------------------------------


class HealthChecker:
    """Monitors system health across all platform components.

    Checks:
    - Broker connections (connected, latency)
    - Market data feed health (last tick age, gaps)
    - Strategy status (running, errors)
    - System resources (memory, CPU approximation)
    - Event bus queue depths
    - Database connectivity (future)

    Usage::

        checker = HealthChecker()
        checker.register_check("broker", broker_check_fn)
        status = await checker.run_all_checks()
    """

    def __init__(self) -> None:
        self._checks: dict[str, HealthCheckFn] = {}
        self._last_results: dict[str, HealthStatus] = {}
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

        # Register the built-in system resources check
        self.register_check("system_resources", _check_system_resources)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_check(self, name: str, check_fn: HealthCheckFn) -> None:
        """Register a named health check function.

        Args:
            name: Unique name for the check (e.g. ``"broker"``, ``"market_data"``).
            check_fn: An async callable that takes no arguments and returns a
                :class:`HealthStatus`.

        Raises:
            ValueError: If *name* is empty.
        """
        if not name:
            raise ValueError("Health check name must not be empty")
        self._checks[name] = check_fn
        logger.debug("Registered health check: %s", name)

    def unregister_check(self, name: str) -> bool:
        """Remove a previously registered health check.

        Returns ``True`` if the check existed, ``False`` otherwise.
        """
        removed = self._checks.pop(name, None)
        self._last_results.pop(name, None)
        return removed is not None

    async def run_all_checks(self) -> dict[str, HealthStatus]:
        """Execute all registered health checks concurrently.

        Returns a dict mapping check name to the resulting
        :class:`HealthStatus`.  Failed checks are reported as
        ``"unhealthy"`` with the exception message.
        """
        results: dict[str, HealthStatus] = {}

        async def _run_single(name: str, fn: HealthCheckFn) -> tuple[str, HealthStatus]:
            start = time.monotonic()
            try:
                hs = await asyncio.wait_for(fn(), timeout=10.0)
            except asyncio.TimeoutError:
                hs = HealthStatus(
                    component=name,
                    status="unhealthy",
                    message="Health check timed out (>10s)",
                )
            except Exception as exc:
                hs = HealthStatus(
                    component=name,
                    status="unhealthy",
                    message=f"Health check failed: {exc}",
                )
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            hs.metrics["check_duration_ms"] = elapsed_ms
            return name, hs

        tasks = [_run_single(name, fn) for name, fn in self._checks.items()]
        if tasks:
            completed = await asyncio.gather(*tasks, return_exceptions=False)
            for name, hs in completed:
                results[name] = hs

        self._last_results = dict(results)
        return results

    async def start_periodic(self, interval: float = 30.0) -> None:
        """Start running health checks periodically in the background.

        Args:
            interval: Seconds between consecutive check cycles.
        """
        if self._running:
            logger.warning("Periodic health checker already running")
            return
        self._running = True
        self._task = asyncio.create_task(
            self._periodic_loop(interval), name="health-checker-periodic"
        )
        logger.info("Periodic health checker started (interval=%.1fs)", interval)

    async def stop(self) -> None:
        """Stop the periodic health checker."""
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
        logger.info("Periodic health checker stopped")

    def get_status(self, component: str | None = None) -> dict[str, HealthStatus] | HealthStatus | None:
        """Return cached health status.

        Args:
            component: If provided, return the status for that single
                component (or ``None`` if no result cached).  If omitted,
                return the full dict of all cached results.
        """
        if component is not None:
            return self._last_results.get(component)
        return dict(self._last_results)

    def get_overall_status(self) -> str:
        """Compute the aggregate platform health.

        Returns ``"healthy"`` if every component is healthy,
        ``"degraded"`` if any component is degraded, or
        ``"unhealthy"`` if any component is unhealthy.
        If no checks have run yet, returns ``"unknown"``.
        """
        if not self._last_results:
            return "unknown"
        return _worst_status([hs.status for hs in self._last_results.values()])

    def get_summary(self) -> dict[str, Any]:
        """Return a JSON-friendly summary of all component statuses."""
        return {
            "overall": self.get_overall_status(),
            "components": {
                name: hs.to_dict() for name, hs in self._last_results.items()
            },
            "total_checks": len(self._checks),
            "checks_run": len(self._last_results),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _periodic_loop(self, interval: float) -> None:
        """Background loop that runs checks on a fixed interval."""
        while self._running:
            try:
                results = await self.run_all_checks()
                # Log any unhealthy components
                for name, hs in results.items():
                    if hs.status == "unhealthy":
                        logger.warning(
                            "Component %s is UNHEALTHY: %s", name, hs.message
                        )
                    elif hs.status == "degraded":
                        logger.info(
                            "Component %s is DEGRADED: %s", name, hs.message
                        )
            except Exception:
                logger.exception("Error during periodic health check")

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
