"""
Platform metrics collection for the algo trading platform.

Collects counters, gauges, and histograms for observability.
Provides snapshot access and a rolling history of metric data points
suitable for dashboard consumption.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric data point
# ---------------------------------------------------------------------------


@dataclass
class MetricPoint:
    """A single recorded metric data point."""

    name: str
    value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for API / event payloads."""
        return {
            "name": self.name,
            "value": self.value,
            "timestamp": self.timestamp.isoformat(),
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Collects platform metrics for observability.

    Metric types:
    - **Counters** (orders_placed, trades_executed) -- monotonically
      increasing values.
    - **Gauges** (active_orders, portfolio_value, unrealised_pnl) --
      point-in-time values that can go up or down.
    - **Histograms** (order_latency, fill_time) -- distributions of
      observed values with statistical summaries.

    Thread-safe: all mutations are protected by a lock so the collector
    can be used safely from multiple threads / tasks.

    Usage::

        mc = MetricsCollector()
        mc.increment("orders_placed", tags={"strategy": "iron_condor"})
        mc.set_gauge("portfolio_value", 10_500_000)
        mc.record_histogram("order_latency_ms", 45.2)
        snapshot = mc.get_snapshot()
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._history: list[MetricPoint] = []
        self._max_history: int = max_history
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------

    def increment(self, name: str, value: float = 1.0, tags: dict[str, str] | None = None) -> None:
        """Increment a counter metric.

        Args:
            name: Metric name (e.g. ``"orders_placed"``).
            value: Amount to add (default ``1``).
            tags: Optional key-value tags for the data point.
        """
        with self._lock:
            self._counters[name] += value
            self._record_point(name, self._counters[name], tags)

    def get_counter(self, name: str) -> float:
        """Return the current value of a counter (``0.0`` if not found)."""
        with self._lock:
            return self._counters.get(name, 0.0)

    # ------------------------------------------------------------------
    # Gauges
    # ------------------------------------------------------------------

    def set_gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Set a gauge metric to an absolute value.

        Args:
            name: Metric name (e.g. ``"portfolio_value"``).
            value: The current value.
            tags: Optional key-value tags for the data point.
        """
        with self._lock:
            self._gauges[name] = value
            self._record_point(name, value, tags)

    def get_gauge(self, name: str) -> float:
        """Return the current value of a gauge (``0.0`` if not found)."""
        with self._lock:
            return self._gauges.get(name, 0.0)

    # ------------------------------------------------------------------
    # Histograms
    # ------------------------------------------------------------------

    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record an observation in a histogram.

        Args:
            name: Metric name (e.g. ``"order_latency_ms"``).
            value: Observed value.
            tags: Optional key-value tags for the data point.
        """
        with self._lock:
            self._histograms[name].append(value)
            self._record_point(name, value, tags)

    def get_histogram_stats(self, name: str) -> dict[str, float]:
        """Compute statistics for a histogram.

        Returns a dict with keys ``min``, ``max``, ``avg``, ``p50``,
        ``p95``, ``p99``, ``count``.  If the histogram has no data,
        all values are ``0.0``.
        """
        with self._lock:
            values = list(self._histograms.get(name, []))

        if not values:
            return {
                "min": 0.0,
                "max": 0.0,
                "avg": 0.0,
                "p50": 0.0,
                "p95": 0.0,
                "p99": 0.0,
                "count": 0.0,
            }

        values.sort()
        n = len(values)
        return {
            "min": values[0],
            "max": values[-1],
            "avg": sum(values) / n,
            "p50": _percentile(values, 50),
            "p95": _percentile(values, 95),
            "p99": _percentile(values, 99),
            "count": float(n),
        }

    # ------------------------------------------------------------------
    # Snapshot / history
    # ------------------------------------------------------------------

    def get_snapshot(self) -> dict[str, Any]:
        """Return a point-in-time snapshot of all metrics.

        Returns a dict with keys ``counters``, ``gauges``, and
        ``histograms`` (each histogram key maps to its stats dict).
        """
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            hist_names = list(self._histograms.keys())

        histogram_stats = {name: self.get_histogram_stats(name) for name in hist_names}

        return {
            "counters": counters,
            "gauges": gauges,
            "histograms": histogram_stats,
        }

    def get_history(self, name: str | None = None, limit: int = 100) -> list[MetricPoint]:
        """Return recent metric data points.

        Args:
            name: If provided, filter history to this metric name only.
            limit: Maximum number of data points to return (newest first).

        Returns:
            A list of :class:`MetricPoint` instances.
        """
        with self._lock:
            if name is not None:
                filtered = [p for p in self._history if p.name == name]
            else:
                filtered = list(self._history)
        # Return newest first, up to limit
        return list(reversed(filtered[-limit:]))

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all metrics and history."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._history.clear()
        logger.info("Metrics collector reset")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _record_point(self, name: str, value: float, tags: dict[str, str] | None) -> None:
        """Append a data point to the rolling history (caller holds lock)."""
        point = MetricPoint(name=name, value=value, tags=tags or {})
        self._history.append(point)
        # Trim if over capacity
        if len(self._history) > self._max_history:
            overflow = len(self._history) - self._max_history
            self._history = self._history[overflow:]


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Compute the *pct*-th percentile from a **sorted** list using
    linear interpolation (the same method as NumPy's default)."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    # Rank (0-indexed fractional position)
    rank = (pct / 100.0) * (n - 1)
    low = int(math.floor(rank))
    high = min(low + 1, n - 1)
    frac = rank - low
    return sorted_values[low] + frac * (sorted_values[high] - sorted_values[low])
