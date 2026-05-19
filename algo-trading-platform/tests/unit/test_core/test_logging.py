"""
Comprehensive unit tests for core/logging.py — structured logging system.

Covers JSONFormatter, ColoredConsoleFormatter, LogContext, new_correlation_id,
TimingLogger, and setup_logging.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.logging import (
    ColoredConsoleFormatter,
    JSONFormatter,
    LogContext,
    TimingLogger,
    component_var,
    correlation_id_var,
    get_logger,
    new_correlation_id,
    setup_logging,
    strategy_id_var,
)

# We need to reset the module-level _logging_initialized flag between tests.
import core.logging as _logging_module


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Reset global logging state and context vars between tests."""
    _logging_module._logging_initialized = False
    # Reset context vars to defaults
    correlation_id_var.set("")
    strategy_id_var.set("")
    component_var.set("")
    yield
    _logging_module._logging_initialized = False
    correlation_id_var.set("")
    strategy_id_var.set("")
    component_var.set("")


# ===================================================================
# 1. JSONFormatter
# ===================================================================


class TestJSONFormatter:
    def _make_record(self, msg="test message", level=logging.INFO, **extras):
        logger = logging.getLogger("test_json_fmt")
        record = logger.makeRecord(
            name="test_json_fmt",
            level=level,
            fn="test_file.py",
            lno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in extras.items():
            setattr(record, k, v)
        return record

    def test_produces_valid_json(self):
        fmt = JSONFormatter()
        record = self._make_record()
        output = fmt.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_includes_required_fields(self):
        fmt = JSONFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert "timestamp" in parsed
        assert "level" in parsed
        assert "logger" in parsed
        assert "message" in parsed
        assert "source" in parsed

    def test_includes_correlation_id_from_context(self):
        correlation_id_var.set("test-corr-123")
        fmt = JSONFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert parsed["correlation_id"] == "test-corr-123"

    def test_includes_strategy_id_from_context(self):
        strategy_id_var.set("iron_condor_1")
        fmt = JSONFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert parsed["strategy_id"] == "iron_condor_1"

    def test_includes_component_from_context(self):
        component_var.set("broker_gateway")
        fmt = JSONFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert parsed["component"] == "broker_gateway"

    def test_includes_component_from_record_attr(self):
        fmt = JSONFormatter()
        record = self._make_record(component="order_manager")
        parsed = json.loads(fmt.format(record))
        assert parsed["component"] == "order_manager"

    def test_extra_fields_included(self):
        fmt = JSONFormatter()
        record = self._make_record(order_id="ORD-001")
        parsed = json.loads(fmt.format(record))
        assert parsed["extra"]["order_id"] == "ORD-001"

    def test_exception_info_included(self):
        fmt = JSONFormatter()
        logger = logging.getLogger("test_exc")
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logger.makeRecord(
                name="test_exc", level=logging.ERROR,
                fn="f.py", lno=1, msg="error occurred",
                args=(), exc_info=sys.exc_info(),
            )
        parsed = json.loads(fmt.format(record))
        assert "exception" in parsed
        assert parsed["exception"]["type"] == "ValueError"
        assert "boom" in parsed["exception"]["message"]

    def test_message_formatting(self):
        fmt = JSONFormatter()
        record = self._make_record(msg="test message")
        parsed = json.loads(fmt.format(record))
        assert parsed["message"] == "test message"

    def test_source_location(self):
        fmt = JSONFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert "file" in parsed["source"]
        assert "line" in parsed["source"]
        assert "function" in parsed["source"]


# ===================================================================
# 2. ColoredConsoleFormatter
# ===================================================================


class TestColoredConsoleFormatter:
    def _make_record(self, msg="hello", level=logging.INFO):
        logger = logging.getLogger("test_colored")
        return logger.makeRecord(
            name="test_colored", level=level,
            fn="test.py", lno=10, msg=msg,
            args=(), exc_info=None,
        )

    def test_output_contains_message(self):
        fmt = ColoredConsoleFormatter()
        record = self._make_record("important message")
        output = fmt.format(record)
        assert "important message" in output

    def test_output_contains_level(self):
        fmt = ColoredConsoleFormatter()
        record = self._make_record(level=logging.WARNING)
        output = fmt.format(record)
        assert "WARNING" in output

    def test_output_contains_timestamp(self):
        fmt = ColoredConsoleFormatter()
        record = self._make_record()
        output = fmt.format(record)
        # Timestamp format: YYYY-MM-DDTHH:MM:SS
        assert "T" in output  # ISO format separator

    def test_includes_correlation_id(self):
        correlation_id_var.set("xyz-999")
        fmt = ColoredConsoleFormatter()
        record = self._make_record()
        output = fmt.format(record)
        assert "corr=xyz-999" in output

    def test_includes_strategy_id(self):
        strategy_id_var.set("strat-A")
        fmt = ColoredConsoleFormatter()
        record = self._make_record()
        output = fmt.format(record)
        assert "strat=strat-A" in output

    def test_includes_component(self):
        component_var.set("risk_engine")
        fmt = ColoredConsoleFormatter()
        record = self._make_record()
        output = fmt.format(record)
        assert "[risk_engine]" in output

    def test_ansi_colors_present(self):
        fmt = ColoredConsoleFormatter()
        record = self._make_record(level=logging.ERROR)
        output = fmt.format(record)
        assert "\033[" in output  # ANSI escape code present

    def test_different_levels_different_colors(self):
        fmt = ColoredConsoleFormatter()
        info_output = fmt.format(self._make_record(level=logging.INFO))
        error_output = fmt.format(self._make_record(level=logging.ERROR))
        # Extract color code (first escape sequence)
        info_color = info_output.split("\033[")[1].split("m")[0] if "\033[" in info_output else ""
        error_color = error_output.split("\033[")[1].split("m")[0] if "\033[" in error_output else ""
        assert info_color != error_color


# ===================================================================
# 3. LogContext
# ===================================================================


class TestLogContext:
    def test_sets_correlation_id(self):
        with LogContext(correlation_id="abc-123"):
            assert correlation_id_var.get() == "abc-123"
        assert correlation_id_var.get() == ""  # reset

    def test_sets_strategy_id(self):
        with LogContext(strategy_id="my_strat"):
            assert strategy_id_var.get() == "my_strat"
        assert strategy_id_var.get() == ""

    def test_sets_component(self):
        with LogContext(component="broker"):
            assert component_var.get() == "broker"
        assert component_var.get() == ""

    def test_sets_multiple_vars(self):
        with LogContext(correlation_id="c1", strategy_id="s1", component="comp"):
            assert correlation_id_var.get() == "c1"
            assert strategy_id_var.get() == "s1"
            assert component_var.get() == "comp"
        assert correlation_id_var.get() == ""
        assert strategy_id_var.get() == ""
        assert component_var.get() == ""

    def test_resets_on_exception(self):
        with pytest.raises(RuntimeError):
            with LogContext(correlation_id="err"):
                assert correlation_id_var.get() == "err"
                raise RuntimeError("boom")
        assert correlation_id_var.get() == ""

    def test_nested_contexts(self):
        with LogContext(correlation_id="outer"):
            assert correlation_id_var.get() == "outer"
            with LogContext(correlation_id="inner"):
                assert correlation_id_var.get() == "inner"
            assert correlation_id_var.get() == "outer"
        assert correlation_id_var.get() == ""

    @pytest.mark.asyncio
    async def test_async_context(self):
        async with LogContext(correlation_id="async-1", strategy_id="async-s"):
            assert correlation_id_var.get() == "async-1"
            assert strategy_id_var.get() == "async-s"
        assert correlation_id_var.get() == ""
        assert strategy_id_var.get() == ""

    def test_partial_context_only_resets_what_was_set(self):
        correlation_id_var.set("preexisting")
        with LogContext(strategy_id="only-strat"):
            assert correlation_id_var.get() == "preexisting"
            assert strategy_id_var.get() == "only-strat"
        assert strategy_id_var.get() == ""
        assert correlation_id_var.get() == "preexisting"
        # cleanup
        correlation_id_var.set("")


# ===================================================================
# 4. new_correlation_id
# ===================================================================


class TestNewCorrelationId:
    def test_returns_12_char_hex(self):
        cid = new_correlation_id()
        assert len(cid) == 12
        int(cid, 16)  # must be valid hex

    def test_unique(self):
        ids = {new_correlation_id() for _ in range(100)}
        assert len(ids) == 100


# ===================================================================
# 5. TimingLogger
# ===================================================================


class TestTimingLogger:
    def test_measures_elapsed_time(self):
        mock_logger = MagicMock(spec=logging.Logger)
        with TimingLogger(mock_logger, "test_op") as tl:
            time.sleep(0.05)
        assert tl.elapsed_ms >= 40  # at least ~40ms
        mock_logger.log.assert_called_once()
        call_args = mock_logger.log.call_args
        # Format string is "%s completed in %.2fms", args are ("test_op", elapsed)
        assert "completed" in call_args[0][1]
        assert call_args[0][2] == "test_op"  # first format arg

    def test_logs_failure_on_exception(self):
        mock_logger = MagicMock(spec=logging.Logger)
        with pytest.raises(ValueError):
            with TimingLogger(mock_logger, "fail_op"):
                raise ValueError("oops")
        mock_logger.log.assert_called_once()
        call_args = mock_logger.log.call_args
        assert call_args[0][0] == logging.ERROR
        assert "failed" in call_args[0][1]

    def test_elapsed_ms_attribute(self):
        mock_logger = MagicMock(spec=logging.Logger)
        with TimingLogger(mock_logger, "op") as tl:
            time.sleep(0.01)
        assert tl.elapsed_ms > 0

    def test_extra_passed_to_log(self):
        mock_logger = MagicMock(spec=logging.Logger)
        with TimingLogger(mock_logger, "op", extra={"key": "val"}):
            pass
        call_kwargs = mock_logger.log.call_args[1]
        assert call_kwargs["extra"]["key"] == "val"
        assert "elapsed_ms" in call_kwargs["extra"]

    @pytest.mark.asyncio
    async def test_async_context(self):
        mock_logger = MagicMock(spec=logging.Logger)
        async with TimingLogger(mock_logger, "async_op") as tl:
            pass
        assert tl.elapsed_ms >= 0
        mock_logger.log.assert_called_once()

    def test_decorator_sync(self):
        mock_logger = MagicMock(spec=logging.Logger)

        @TimingLogger.decorator(mock_logger, "sync_decorated")
        def my_func(x):
            return x * 2

        result = my_func(5)
        assert result == 10
        mock_logger.log.assert_called_once()

    @pytest.mark.asyncio
    async def test_decorator_async(self):
        mock_logger = MagicMock(spec=logging.Logger)

        @TimingLogger.decorator(mock_logger, "async_decorated")
        async def my_func(x):
            return x + 1

        result = await my_func(10)
        assert result == 11
        mock_logger.log.assert_called_once()


# ===================================================================
# 6. setup_logging
# ===================================================================


class TestSetupLogging:
    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "test_logs"
        setup_logging(log_dir=str(log_dir))
        assert log_dir.exists()
        assert log_dir.is_dir()

    def test_creates_handlers(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=str(log_dir))
        root = logging.getLogger()
        # Should have at least console + file + error_file = 3 handlers
        assert len(root.handlers) >= 3

    def test_json_format_uses_json_formatter(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=str(log_dir), log_format="json")
        root = logging.getLogger()
        console = root.handlers[0]
        assert isinstance(console.formatter, JSONFormatter)

    def test_console_format_uses_colored_formatter(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=str(log_dir), log_format="console")
        root = logging.getLogger()
        console = root.handlers[0]
        assert isinstance(console.formatter, ColoredConsoleFormatter)

    def test_idempotent_no_duplicate_handlers(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=str(log_dir))
        root = logging.getLogger()
        count1 = len(root.handlers)
        # Call again — should be a no-op due to _logging_initialized flag
        setup_logging(log_dir=str(log_dir))
        assert len(root.handlers) == count1

    def test_sets_component_context_var(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=str(log_dir), component="my_platform")
        assert component_var.get() == "my_platform"

    def test_sets_log_level(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(level="DEBUG", log_dir=str(log_dir))
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_log_files_created(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(log_dir=str(log_dir))
        # Emit a log to force file creation
        logging.getLogger().info("test")
        assert (log_dir / "platform.json.log").exists()
        assert (log_dir / "platform.error.log").exists()


# ===================================================================
# 7. get_logger
# ===================================================================


class TestGetLogger:
    def test_returns_logger_with_name(self):
        lg = get_logger("broker_gateway")
        assert lg.name == "broker_gateway"

    def test_injects_component_filter(self):
        lg = get_logger("risk_engine")
        filter_types = [type(f).__name__ for f in lg.filters]
        assert "_ComponentFilter" in filter_types

    def test_no_duplicate_filters(self):
        lg = get_logger("unique_comp")
        initial = len(lg.filters)
        get_logger("unique_comp")  # call again
        assert len(lg.filters) == initial
