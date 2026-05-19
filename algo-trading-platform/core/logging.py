"""
Structured logging system for institutional-grade algo trading platform.

Provides JSON structured logging with correlation ID propagation,
component-based loggers, multiple output targets, context injection,
and performance timing utilities.

Usage:
    from core.logging import setup_logging, get_logger, LogContext, TimingLogger

    setup_logging(level="INFO", log_format="json")
    logger = get_logger("broker_gateway")

    with LogContext(correlation_id="abc-123", strategy_id="iron_condor_1"):
        logger.info("Processing order", extra={"order_id": "ORD-001"})

    with TimingLogger(logger, "option_chain_fetch"):
        # ... timed operation ...
        pass
"""

import asyncio
import functools
import json
import logging
import sys
import time
import traceback
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = [
    "JSONFormatter",
    "ColoredConsoleFormatter",
    "setup_logging",
    "get_logger",
    "LogContext",
    "TimingLogger",
    "new_correlation_id",
    "correlation_id_var",
    "strategy_id_var",
    "component_var",
]

# ---------------------------------------------------------------------------
# Context variables for correlation — these propagate automatically across
# async boundaries thanks to contextvars.
# ---------------------------------------------------------------------------

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
strategy_id_var: ContextVar[str] = ContextVar("strategy_id", default="")
component_var: ContextVar[str] = ContextVar("component", default="")


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON with correlation context.

    Every line produced is valid JSON suitable for ingestion by log
    aggregation tools (ELK, Datadog, Splunk, etc.).
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "component": getattr(record, "component", "") or component_var.get(),
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "") or correlation_id_var.get(),
            "strategy_id": getattr(record, "strategy_id", "") or strategy_id_var.get(),
        }

        # Merge any extra fields the caller passed via `extra={...}`
        # Exclude standard LogRecord attributes to keep output clean.
        _standard = {
            "name", "msg", "args", "created", "relativeCreated",
            "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "pathname", "filename", "module", "levelno", "levelname",
            "msecs", "thread", "threadName", "process", "processName",
            "message", "taskName",
            # Our own injected keys — already handled above
            "component", "correlation_id", "strategy_id",
        }
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _standard and not k.startswith("_")
        }
        if extras:
            log_entry["extra"] = extras

        # Source location
        log_entry["source"] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName,
        }

        # Exception info
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        if record.stack_info:
            log_entry["stack_info"] = record.stack_info

        return json.dumps(log_entry, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Colored Console Formatter
# ---------------------------------------------------------------------------

class ColoredConsoleFormatter(logging.Formatter):
    """Colored, human-readable output for terminal use during development."""

    # ANSI colour codes
    RESET = "\033[0m"
    COLORS: dict[int, str] = {
        logging.DEBUG: "\033[36m",      # cyan
        logging.INFO: "\033[32m",       # green
        logging.WARNING: "\033[33m",    # yellow
        logging.ERROR: "\033[31m",      # red
        logging.CRITICAL: "\033[1;31m", # bold red
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, self.RESET)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        level = record.levelname.ljust(8)
        comp = getattr(record, "component", "") or component_var.get()
        corr = getattr(record, "correlation_id", "") or correlation_id_var.get()
        strat = getattr(record, "strategy_id", "") or strategy_id_var.get()

        comp_str = f"[{comp}]" if comp else ""
        ctx_parts: list[str] = []
        if corr:
            ctx_parts.append(f"corr={corr}")
        if strat:
            ctx_parts.append(f"strat={strat}")
        ctx_str = f" ({', '.join(ctx_parts)})" if ctx_parts else ""

        msg = record.getMessage()
        formatted = f"{color}{ts} {level}{self.RESET} {comp_str}{ctx_str} {msg}"

        if record.exc_info and record.exc_info[0] is not None:
            formatted += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            formatted += "\n" + record.stack_info

        return formatted


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_logging_initialized: bool = False


def setup_logging(
    level: str = "INFO",
    log_format: str = "json",
    log_dir: str | Path = "logs",
    max_bytes: int = 50_000_000,
    backup_count: int = 10,
    component: str = "platform",
) -> None:
    """Initialise the logging system.  Call once at application startup.

    Args:
        level: Root log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_format: ``"json"`` for production JSON output or ``"console"``
            for coloured human-readable output.
        log_dir: Directory for log files.  Created automatically.
        max_bytes: Maximum size per log file before rotation (default 50 MB).
        backup_count: Number of rotated log files to keep.
        component: Default component name injected into the root context.
    """
    global _logging_initialized
    if _logging_initialized:
        return
    _logging_initialized = True

    component_var.set(component)

    log_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(log_level)

    # Remove any pre-existing handlers on the root logger to avoid duplicates
    root.handlers.clear()

    # --- Console handler ---------------------------------------------------
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    if log_format == "console":
        console_handler.setFormatter(ColoredConsoleFormatter())
    else:
        console_handler.setFormatter(JSONFormatter())
    root.addHandler(console_handler)

    # --- Rotating JSON file handler ----------------------------------------
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    json_file = log_path / "platform.json.log"
    file_handler = RotatingFileHandler(
        str(json_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(JSONFormatter())
    root.addHandler(file_handler)

    # --- Rotating plain-text error-only file handler -----------------------
    error_file = log_path / "platform.error.log"
    error_handler = RotatingFileHandler(
        str(error_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(JSONFormatter())
    root.addHandler(error_handler)


# ---------------------------------------------------------------------------
# Component loggers
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given component name.

    The component name is automatically injected into every record
    produced by this logger via a lightweight filter.

    Args:
        name: Logical component name, e.g. ``"broker_gateway"``,
              ``"risk_engine"``, ``"order_manager"``.
    """
    logger = logging.getLogger(name)

    # Attach a filter that injects the component name if not already present.
    # Use a marker attribute to avoid duplicates (local class can't use isinstance).
    class _ComponentFilter(logging.Filter):
        _is_component_filter = True

        def filter(self, record: logging.LogRecord) -> bool:
            if not getattr(record, "component", ""):
                record.component = name  # type: ignore[attr-defined]
            return True

    # Avoid adding duplicate filters
    if not any(getattr(f, "_is_component_filter", False) for f in logger.filters):
        logger.addFilter(_ComponentFilter())

    return logger


# ---------------------------------------------------------------------------
# Context manager for correlation / strategy context
# ---------------------------------------------------------------------------

class LogContext:
    """Context manager that sets correlation and strategy context variables
    for the duration of a block.  All log messages emitted inside the block
    (including from called functions) automatically include the context.

    Usage::

        with LogContext(correlation_id="abc-123", strategy_id="iron_condor_1"):
            logger.info("Processing order")
            # => includes correlation_id="abc-123", strategy_id="iron_condor_1"

    Can also be used as an async context manager.
    """

    def __init__(
        self,
        correlation_id: str | None = None,
        strategy_id: str | None = None,
        component: str | None = None,
        **extra_context: str,
    ) -> None:
        self._correlation_id = correlation_id
        self._strategy_id = strategy_id
        self._component = component
        self._extra = extra_context
        self._tokens: list[Token[str]] = []

    # -- sync ---------------------------------------------------------------
    def __enter__(self) -> "LogContext":
        if self._correlation_id is not None:
            self._tokens.append(correlation_id_var.set(self._correlation_id))
        if self._strategy_id is not None:
            self._tokens.append(strategy_id_var.set(self._strategy_id))
        if self._component is not None:
            self._tokens.append(component_var.set(self._component))
        return self

    def __exit__(self, *exc: Any) -> None:
        for tok in reversed(self._tokens):
            tok.var.reset(tok)
        self._tokens.clear()

    # -- async --------------------------------------------------------------
    async def __aenter__(self) -> "LogContext":
        return self.__enter__()

    async def __aexit__(self, *exc: Any) -> None:
        self.__exit__(*exc)


# ---------------------------------------------------------------------------
# Correlation ID helper
# ---------------------------------------------------------------------------

def new_correlation_id() -> str:
    """Generate a short, unique correlation ID (first 12 hex chars of a UUID4)."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Performance / timing utilities
# ---------------------------------------------------------------------------

class TimingLogger:
    """Context manager **and** decorator for timing operations.

    As a context manager::

        with TimingLogger(logger, "option_chain_fetch"):
            fetch_chain()
        # logs: "option_chain_fetch completed in 45.2ms"

    As a decorator (sync or async)::

        @TimingLogger.decorator(logger, "greeks_calculation")
        async def calc_greeks():
            ...
    """

    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        level: int = logging.INFO,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.logger = logger
        self.operation = operation
        self.level = level
        self.extra = extra or {}
        self._start: float = 0.0
        self.elapsed_ms: float = 0.0

    # -- context manager (sync) ---------------------------------------------
    def __enter__(self) -> "TimingLogger":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000
        extra = {
            **self.extra,
            "operation": self.operation,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }
        if exc[0] is not None:
            self.logger.log(
                logging.ERROR,
                "%s failed after %.2fms",
                self.operation,
                self.elapsed_ms,
                extra=extra,
            )
        else:
            self.logger.log(
                self.level,
                "%s completed in %.2fms",
                self.operation,
                self.elapsed_ms,
                extra=extra,
            )

    # -- context manager (async) --------------------------------------------
    async def __aenter__(self) -> "TimingLogger":
        self._start = time.perf_counter()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.__exit__(*exc)

    # -- decorator ----------------------------------------------------------
    @staticmethod
    def decorator(
        logger: logging.Logger,
        operation: str,
        level: int = logging.INFO,
        extra: dict[str, Any] | None = None,
    ) -> Callable:
        """Return a decorator that times the wrapped function (sync or async).

        Usage::

            @TimingLogger.decorator(logger, "greeks_calculation")
            async def calc_greeks():
                ...

            @TimingLogger.decorator(logger, "portfolio_snapshot")
            def snapshot():
                ...
        """

        def wrapper(fn: Callable) -> Callable:
            if asyncio.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def async_inner(*args: Any, **kwargs: Any) -> Any:
                    async with TimingLogger(logger, operation, level, extra):
                        return await fn(*args, **kwargs)
                return async_inner
            else:
                @functools.wraps(fn)
                def sync_inner(*args: Any, **kwargs: Any) -> Any:
                    with TimingLogger(logger, operation, level, extra):
                        return fn(*args, **kwargs)
                return sync_inner

        return wrapper
