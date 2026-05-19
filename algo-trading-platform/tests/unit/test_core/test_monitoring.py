"""Unit tests for Monitoring (HealthChecker, AlertManager, MetricsCollector).

Tests cover:
- HealthChecker: register_check, run checks, aggregate status, periodic,
  unregister, timeouts, error handling
- AlertManager: create_alert, acknowledge, resolve, list alerts by severity,
  deduplication, cooldown, bulk acknowledge
- MetricsCollector: record metric, counters, gauges, histograms,
  query by name/time range, get latest, snapshot, reset
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models import Alert, AlertLevel
from core.monitoring.health_checker import HealthChecker, HealthStatus, _worst_status
from core.monitoring.alert_manager import AlertManager
from core.monitoring.metrics_collector import MetricsCollector, MetricPoint, _percentile


# =====================================================================
# Helpers
# =====================================================================


def _make_event_bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


async def _healthy_check() -> HealthStatus:
    return HealthStatus(component="test", status="healthy", message="all good")


async def _degraded_check() -> HealthStatus:
    return HealthStatus(component="test_deg", status="degraded", message="slow")


async def _unhealthy_check() -> HealthStatus:
    return HealthStatus(component="test_bad", status="unhealthy", message="down")


async def _timeout_check() -> HealthStatus:
    await asyncio.sleep(30)  # will exceed timeout
    return HealthStatus(component="slow", status="healthy")


async def _error_check() -> HealthStatus:
    raise RuntimeError("check failed")


# =====================================================================
# HealthChecker Tests
# =====================================================================


class TestHealthChecker:

    def test_register_check(self):
        hc = HealthChecker()
        hc.register_check("broker", _healthy_check)
        assert "broker" in hc._checks

    def test_register_check_empty_name_raises(self):
        hc = HealthChecker()
        with pytest.raises(ValueError, match="empty"):
            hc.register_check("", _healthy_check)

    def test_unregister_check(self):
        hc = HealthChecker()
        hc.register_check("broker", _healthy_check)
        result = hc.unregister_check("broker")
        assert result is True
        assert "broker" not in hc._checks

    def test_unregister_nonexistent(self):
        hc = HealthChecker()
        assert hc.unregister_check("nonexistent") is False

    @pytest.mark.asyncio
    async def test_run_all_checks_returns_results(self):
        hc = HealthChecker()
        hc.register_check("test_comp", _healthy_check)
        results = await hc.run_all_checks()
        assert "test_comp" in results
        assert results["test_comp"].status == "healthy"

    @pytest.mark.asyncio
    async def test_run_all_checks_includes_builtin(self):
        hc = HealthChecker()
        results = await hc.run_all_checks()
        assert "system_resources" in results

    @pytest.mark.asyncio
    async def test_run_all_checks_with_error(self):
        hc = HealthChecker()
        hc.register_check("failing", _error_check)
        results = await hc.run_all_checks()
        assert results["failing"].status == "unhealthy"
        assert "failed" in results["failing"].message

    @pytest.mark.asyncio
    async def test_run_all_checks_with_timeout(self):
        hc = HealthChecker()
        hc.register_check("slow", _timeout_check)
        results = await hc.run_all_checks()
        assert results["slow"].status == "unhealthy"
        assert "timed out" in results["slow"].message

    @pytest.mark.asyncio
    async def test_get_status_after_check(self):
        hc = HealthChecker()
        hc.register_check("test_comp", _healthy_check)
        await hc.run_all_checks()
        status = hc.get_status("test_comp")
        assert status is not None
        assert status.status == "healthy"

    @pytest.mark.asyncio
    async def test_get_status_returns_none_before_check(self):
        hc = HealthChecker()
        hc.register_check("test_comp", _healthy_check)
        status = hc.get_status("test_comp")
        assert status is None

    @pytest.mark.asyncio
    async def test_get_status_all(self):
        hc = HealthChecker()
        hc.register_check("comp_a", _healthy_check)
        await hc.run_all_checks()
        all_status = hc.get_status()
        assert isinstance(all_status, dict)

    @pytest.mark.asyncio
    async def test_get_overall_status_healthy(self):
        hc = HealthChecker()
        # Remove built-in to control test precisely
        hc.unregister_check("system_resources")
        hc.register_check("a", _healthy_check)
        await hc.run_all_checks()
        assert hc.get_overall_status() == "healthy"

    @pytest.mark.asyncio
    async def test_get_overall_status_degraded(self):
        hc = HealthChecker()
        hc.unregister_check("system_resources")
        hc.register_check("a", _healthy_check)
        hc.register_check("b", _degraded_check)
        await hc.run_all_checks()
        assert hc.get_overall_status() == "degraded"

    @pytest.mark.asyncio
    async def test_get_overall_status_unhealthy(self):
        hc = HealthChecker()
        hc.unregister_check("system_resources")
        hc.register_check("a", _healthy_check)
        hc.register_check("b", _unhealthy_check)
        await hc.run_all_checks()
        assert hc.get_overall_status() == "unhealthy"

    def test_get_overall_status_unknown_before_run(self):
        hc = HealthChecker()
        hc.unregister_check("system_resources")
        assert hc.get_overall_status() == "unknown"

    @pytest.mark.asyncio
    async def test_get_summary(self):
        hc = HealthChecker()
        hc.unregister_check("system_resources")
        hc.register_check("comp", _healthy_check)
        await hc.run_all_checks()
        summary = hc.get_summary()
        assert "overall" in summary
        assert "components" in summary
        assert summary["total_checks"] == 1
        assert summary["checks_run"] == 1

    def test_worst_status_helper(self):
        assert _worst_status([]) == "healthy"
        assert _worst_status(["healthy", "healthy"]) == "healthy"
        assert _worst_status(["healthy", "degraded"]) == "degraded"
        assert _worst_status(["healthy", "degraded", "unhealthy"]) == "unhealthy"

    @pytest.mark.asyncio
    async def test_check_duration_in_metrics(self):
        hc = HealthChecker()
        hc.register_check("comp", _healthy_check)
        results = await hc.run_all_checks()
        assert "check_duration_ms" in results["comp"].metrics

    def test_health_status_to_dict(self):
        hs = HealthStatus(component="test", status="healthy", message="ok")
        d = hs.to_dict()
        assert d["component"] == "test"
        assert d["status"] == "healthy"
        assert "last_check" in d


# =====================================================================
# AlertManager Tests
# =====================================================================


class TestAlertManager:

    @pytest.mark.asyncio
    async def test_send_alert_creates_alert(self):
        mgr = AlertManager()
        alert = await mgr.send_alert(AlertLevel.WARNING, "Test warning", source="test")
        assert isinstance(alert, Alert)
        assert alert.level == AlertLevel.WARNING
        assert alert.message == "Test warning"

    @pytest.mark.asyncio
    async def test_send_alert_with_event_bus(self):
        bus = _make_event_bus()
        mgr = AlertManager(event_bus=bus)
        await mgr.send_alert(AlertLevel.CRITICAL, "System down", source="test")
        bus.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_deduplication(self):
        mgr = AlertManager(cooldown_seconds=60.0)
        a1 = await mgr.send_alert(AlertLevel.WARNING, "Same msg", source="test")
        a2 = await mgr.send_alert(AlertLevel.WARNING, "Same msg", source="test")
        assert a1 is not None
        assert a2 is None  # deduplicated

    @pytest.mark.asyncio
    async def test_different_level_not_deduplicated(self):
        mgr = AlertManager(cooldown_seconds=60.0)
        a1 = await mgr.send_alert(AlertLevel.WARNING, "msg", source="test")
        a2 = await mgr.send_alert(AlertLevel.CRITICAL, "msg", source="test")
        assert a1 is not None
        assert a2 is not None

    @pytest.mark.asyncio
    async def test_get_alerts_all(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "info1", source="a")
        await mgr.send_alert(AlertLevel.WARNING, "warn1", source="b")
        alerts = mgr.get_alerts()
        assert len(alerts) == 2

    @pytest.mark.asyncio
    async def test_get_alerts_filter_by_level(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "info", source="a")
        await mgr.send_alert(AlertLevel.WARNING, "warn", source="b")
        await mgr.send_alert(AlertLevel.CRITICAL, "crit", source="c")
        warnings = mgr.get_alerts(level=AlertLevel.WARNING)
        assert len(warnings) == 1
        assert warnings[0].level == AlertLevel.WARNING

    @pytest.mark.asyncio
    async def test_get_alerts_filter_by_source(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "msg1", source="broker")
        await mgr.send_alert(AlertLevel.INFO, "msg2", source="risk")
        alerts = mgr.get_alerts(source="broker")
        assert len(alerts) == 1
        assert alerts[0].source == "broker"

    @pytest.mark.asyncio
    async def test_get_alerts_newest_first(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "first", source="a")
        await mgr.send_alert(AlertLevel.WARNING, "second", source="b")
        alerts = mgr.get_alerts()
        assert alerts[0].message == "second"
        assert alerts[1].message == "first"

    @pytest.mark.asyncio
    async def test_get_alerts_limit(self):
        mgr = AlertManager(cooldown_seconds=0)
        for i in range(10):
            await mgr.send_alert(AlertLevel.INFO, f"msg{i}", source=f"s{i}")
        alerts = mgr.get_alerts(limit=3)
        assert len(alerts) == 3

    @pytest.mark.asyncio
    async def test_acknowledge_alert(self):
        mgr = AlertManager()
        alert = await mgr.send_alert(AlertLevel.WARNING, "ack me", source="test")
        result = mgr.acknowledge_alert(alert.alert_id)
        assert result is True
        alerts = mgr.get_alerts(acknowledged=True)
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_acknowledge_nonexistent(self):
        mgr = AlertManager()
        result = mgr.acknowledge_alert("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_acknowledge_all(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "a", source="s1")
        await mgr.send_alert(AlertLevel.WARNING, "b", source="s2")
        count = mgr.acknowledge_all()
        assert count == 2
        assert mgr.get_unacknowledged_count() == 0

    @pytest.mark.asyncio
    async def test_acknowledge_all_by_level(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "a", source="s1")
        await mgr.send_alert(AlertLevel.WARNING, "b", source="s2")
        count = mgr.acknowledge_all(level=AlertLevel.WARNING)
        assert count == 1
        assert mgr.get_unacknowledged_count() == 1

    @pytest.mark.asyncio
    async def test_get_unacknowledged_count(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "a", source="s1")
        await mgr.send_alert(AlertLevel.WARNING, "b", source="s2")
        assert mgr.get_unacknowledged_count() == 2

    @pytest.mark.asyncio
    async def test_get_alert_counts(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "a", source="s1")
        await mgr.send_alert(AlertLevel.INFO, "b", source="s2")
        await mgr.send_alert(AlertLevel.CRITICAL, "c", source="s3")
        counts = mgr.get_alert_counts()
        assert counts["INFO"] == 2
        assert counts["CRITICAL"] == 1
        assert counts["WARNING"] == 0

    @pytest.mark.asyncio
    async def test_clear_old_alerts(self):
        mgr = AlertManager(cooldown_seconds=0)
        await mgr.send_alert(AlertLevel.INFO, "old", source="s1")
        cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)
        removed = mgr.clear_old_alerts(cutoff)
        assert removed == 1
        assert len(mgr.get_alerts()) == 0

    @pytest.mark.asyncio
    async def test_max_alerts_enforced(self):
        mgr = AlertManager(cooldown_seconds=0, max_alerts=5)
        for i in range(10):
            await mgr.send_alert(AlertLevel.INFO, f"msg{i}", source=f"s{i}")
        assert len(mgr.get_alerts(limit=100)) <= 5

    @pytest.mark.asyncio
    async def test_alert_with_details(self):
        mgr = AlertManager()
        alert = await mgr.send_alert(
            AlertLevel.WARNING,
            "loss limit",
            source="risk",
            strategy_id="strat_1",
            details={"pnl": -5000},
        )
        assert alert.data["strategy_id"] == "strat_1"
        assert alert.data["pnl"] == -5000

    @pytest.mark.asyncio
    async def test_filter_acknowledged_false(self):
        mgr = AlertManager(cooldown_seconds=0)
        a1 = await mgr.send_alert(AlertLevel.INFO, "a", source="s1")
        await mgr.send_alert(AlertLevel.WARNING, "b", source="s2")
        mgr.acknowledge_alert(a1.alert_id)
        unacked = mgr.get_alerts(acknowledged=False)
        assert len(unacked) == 1
        assert unacked[0].level == AlertLevel.WARNING


# =====================================================================
# MetricsCollector Tests
# =====================================================================


class TestMetricsCollector:

    def test_increment_counter(self):
        mc = MetricsCollector()
        mc.increment("orders_placed")
        assert mc.get_counter("orders_placed") == 1.0

    def test_increment_counter_by_value(self):
        mc = MetricsCollector()
        mc.increment("orders_placed", value=5.0)
        assert mc.get_counter("orders_placed") == 5.0

    def test_increment_counter_accumulates(self):
        mc = MetricsCollector()
        mc.increment("orders_placed", value=3.0)
        mc.increment("orders_placed", value=2.0)
        assert mc.get_counter("orders_placed") == 5.0

    def test_get_counter_unknown_returns_zero(self):
        mc = MetricsCollector()
        assert mc.get_counter("nonexistent") == 0.0

    def test_set_gauge(self):
        mc = MetricsCollector()
        mc.set_gauge("portfolio_value", 1_000_000.0)
        assert mc.get_gauge("portfolio_value") == 1_000_000.0

    def test_gauge_overwrites(self):
        mc = MetricsCollector()
        mc.set_gauge("portfolio_value", 1_000_000.0)
        mc.set_gauge("portfolio_value", 1_100_000.0)
        assert mc.get_gauge("portfolio_value") == 1_100_000.0

    def test_get_gauge_unknown_returns_zero(self):
        mc = MetricsCollector()
        assert mc.get_gauge("nonexistent") == 0.0

    def test_record_histogram(self):
        mc = MetricsCollector()
        mc.record_histogram("latency_ms", 45.2)
        stats = mc.get_histogram_stats("latency_ms")
        assert stats["count"] == 1.0
        assert stats["min"] == 45.2
        assert stats["max"] == 45.2

    def test_histogram_stats_multiple_values(self):
        mc = MetricsCollector()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            mc.record_histogram("latency_ms", v)
        stats = mc.get_histogram_stats("latency_ms")
        assert stats["count"] == 5.0
        assert stats["min"] == 10.0
        assert stats["max"] == 50.0
        assert stats["avg"] == 30.0
        assert stats["p50"] == 30.0

    def test_histogram_stats_empty(self):
        mc = MetricsCollector()
        stats = mc.get_histogram_stats("nonexistent")
        assert stats["count"] == 0.0
        assert stats["min"] == 0.0
        assert stats["max"] == 0.0

    def test_get_snapshot(self):
        mc = MetricsCollector()
        mc.increment("counter_a")
        mc.set_gauge("gauge_b", 42.0)
        mc.record_histogram("hist_c", 10.0)
        snap = mc.get_snapshot()
        assert "counters" in snap
        assert "gauges" in snap
        assert "histograms" in snap
        assert snap["counters"]["counter_a"] == 1.0
        assert snap["gauges"]["gauge_b"] == 42.0
        assert snap["histograms"]["hist_c"]["count"] == 1.0

    def test_get_history_all(self):
        mc = MetricsCollector()
        mc.increment("a")
        mc.set_gauge("b", 5.0)
        history = mc.get_history()
        assert len(history) == 2

    def test_get_history_by_name(self):
        mc = MetricsCollector()
        mc.increment("a")
        mc.increment("a")
        mc.set_gauge("b", 5.0)
        history = mc.get_history(name="a")
        assert len(history) == 2
        assert all(p.name == "a" for p in history)

    def test_get_history_newest_first(self):
        mc = MetricsCollector()
        mc.increment("a")
        mc.increment("a")
        history = mc.get_history(name="a")
        assert history[0].timestamp >= history[1].timestamp

    def test_get_history_limit(self):
        mc = MetricsCollector()
        for _ in range(20):
            mc.increment("a")
        history = mc.get_history(name="a", limit=5)
        assert len(history) == 5

    def test_reset_clears_all(self):
        mc = MetricsCollector()
        mc.increment("a")
        mc.set_gauge("b", 42.0)
        mc.record_histogram("c", 10.0)
        mc.reset()
        assert mc.get_counter("a") == 0.0
        assert mc.get_gauge("b") == 0.0
        assert mc.get_histogram_stats("c")["count"] == 0.0
        assert mc.get_history() == []

    def test_max_history_enforced(self):
        mc = MetricsCollector(max_history=10)
        for i in range(20):
            mc.increment("a")
        history = mc.get_history()
        assert len(history) <= 10

    def test_tags_recorded(self):
        mc = MetricsCollector()
        mc.increment("orders_placed", tags={"strategy": "iron_condor"})
        history = mc.get_history(name="orders_placed")
        assert history[0].tags == {"strategy": "iron_condor"}

    def test_metric_point_to_dict(self):
        mp = MetricPoint(name="test", value=42.0, tags={"k": "v"})
        d = mp.to_dict()
        assert d["name"] == "test"
        assert d["value"] == 42.0
        assert "timestamp" in d
        assert d["tags"] == {"k": "v"}

    def test_percentile_helper(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile(values, 50) == 3.0
        assert _percentile(values, 0) == 1.0
        assert _percentile(values, 100) == 5.0

    def test_percentile_empty(self):
        assert _percentile([], 50) == 0.0

    def test_percentile_single_value(self):
        assert _percentile([42.0], 50) == 42.0
        assert _percentile([42.0], 99) == 42.0
