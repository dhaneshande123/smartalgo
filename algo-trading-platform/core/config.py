"""
Configuration system for the algo trading platform.

Loads YAML configuration files with environment variable substitution,
validates via Pydantic v2 models, supports config overlays (base + env-specific),
singleton access, and hot-reloading.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variable substitution
# ---------------------------------------------------------------------------

_ENV_VAR_PATTERN = re.compile(
    r"\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?::- *(?P<default>[^}]*))?\}"
)


def _substitute_env_vars(value: str) -> str:
    """Replace ``${VAR}`` and ``${VAR:-default}`` patterns in *value*.

    Raises:
        ValueError: If an environment variable has no default and is not set.
    """

    def _replacer(match: re.Match[str]) -> str:
        var_name: str = match.group("var")
        default: str | None = match.group("default")
        env_value = os.environ.get(var_name)

        if env_value is not None:
            return env_value
        if default is not None:
            return default

        raise ValueError(
            f"Environment variable '${{{var_name}}}' is not set and has no default value"
        )

    return _ENV_VAR_PATTERN.sub(_replacer, value)


def _deep_substitute(obj: Any) -> Any:
    """Recursively walk a nested dict/list and substitute env vars in strings."""
    if isinstance(obj, str):
        return _substitute_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _deep_substitute(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_substitute(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Pydantic configuration models
# ---------------------------------------------------------------------------


class BrokerConfig(BaseModel):
    """Configuration for a single broker connection."""

    name: str
    api_key: str = ""
    api_secret: str = ""
    access_token: str = ""
    totp_secret: str = ""
    enabled: bool = True
    rate_limit: float = Field(default=3.0, description="Requests per second")


class MarketDataConfig(BaseModel):
    """Market data feed settings."""

    feed: str = "zerodha_websocket"
    subscriptions: list[str] = []
    option_chains: list[dict] = []  # type: ignore[type-arg]
    tick_storage: bool = True
    candle_intervals: list[str] = ["1m", "5m", "15m", "1h", "1d"]


class RiskConfig(BaseModel):
    """Risk management thresholds."""

    max_daily_loss: float = 50000
    max_portfolio_delta: float = 500
    max_portfolio_gamma: float = 100
    max_portfolio_vega: float = 1000
    max_margin_utilization: float = 0.85
    kill_switch_loss: float = 100000
    max_order_value: float = 5000000
    max_position_per_instrument: int = 50
    fat_finger_threshold_pct: float = 5.0
    stale_feed_timeout_sec: float = 5.0


class DatabaseConfig(BaseModel):
    """Database connection settings (TimescaleDB + Redis)."""

    timescaledb_host: str = "localhost"
    timescaledb_port: int = 5432
    timescaledb_database: str = "trading"
    timescaledb_user: str = "postgres"
    timescaledb_password: str = ""
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0


class AlertConfig(BaseModel):
    """Alert / notification channel settings."""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_from: str = ""
    email_to: list[str] = []


class DashboardConfig(BaseModel):
    """Web dashboard settings."""

    host: str = "0.0.0.0"
    port: int = 8080
    auth_enabled: bool = True
    secret_key: str = ""
    cors_origins: list[str] = ["http://localhost:3000"]


class PlatformConfig(BaseModel):
    """Top-level platform configuration aggregating all sub-configs."""

    mode: str = Field(
        default="paper",
        description="Trading mode: 'live', 'paper', or 'backtest'",
    )
    timezone: str = "Asia/Kolkata"
    brokers: dict[str, BrokerConfig] = {}
    primary_broker: str = "zerodha"
    backup_broker: str = ""
    market_data: MarketDataConfig = MarketDataConfig()
    risk: RiskConfig = RiskConfig()
    database: DatabaseConfig = DatabaseConfig()
    alerts: AlertConfig = AlertConfig()
    dashboard: DashboardConfig = DashboardConfig()
    strategies: list[dict] = []  # type: ignore[type-arg]
    log_level: str = "INFO"
    log_format: str = "json"


# ---------------------------------------------------------------------------
# YAML loading helpers
# ---------------------------------------------------------------------------


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Read and parse a single YAML file.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML content is malformed or not a mapping.
    """
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"Failed to read configuration file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed YAML in {path}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a YAML mapping at the top level of {path}, got {type(data).__name__}"
        )
    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *overlay* into *base*, returning a new dict.

    Lists and scalar values in *overlay* replace those in *base*.
    Nested dicts are merged recursively.
    """
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _flatten_yaml(data: dict[str, Any]) -> dict[str, Any]:
    """Map the nested YAML structure into the flat PlatformConfig shape.

    The YAML file uses a human-friendly nested layout::

        platform:
          mode: "paper"
        brokers:
          primary: "zerodha"
          zerodha: {name: ..., api_key: ...}
        market_data: {...}
        risk: {...}

    This function flattens it so that Pydantic receives::

        mode: "paper"
        primary_broker: "zerodha"
        brokers: {zerodha: BrokerConfig(...)}
        market_data: {...}
        ...
    """
    flat: dict[str, Any] = {}

    # Merge top-level `platform:` section into root
    if "platform" in data and isinstance(data["platform"], dict):
        flat.update(data["platform"])

    # Process brokers: extract primary/backup, keep only BrokerConfig dicts
    if "brokers" in data and isinstance(data["brokers"], dict):
        brokers_raw = dict(data["brokers"])
        flat["primary_broker"] = brokers_raw.pop("primary", "")
        flat["backup_broker"] = brokers_raw.pop("backup", "")
        flat["brokers"] = brokers_raw  # remaining keys are broker configs

    # Copy other sections directly
    for key in ("market_data", "risk", "database", "alerts", "dashboard",
                "strategies", "event_bus", "nse"):
        if key in data:
            flat[key] = data[key]

    # Also preserve any top-level keys not yet handled (forward-compatible)
    for key, value in data.items():
        if key not in ("platform", "brokers", "market_data", "risk", "database",
                       "alerts", "dashboard", "strategies", "event_bus", "nse"):
            if key not in flat:
                flat[key] = value

    return flat


# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------

_config: PlatformConfig | None = None
_config_lock = threading.Lock()
_config_path: Path | None = None
_config_env: str | None = None
_watcher_thread: threading.Thread | None = None
_watcher_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(
    path: str | Path,
    env_override: str | None = None,
) -> PlatformConfig:
    """Load a YAML configuration file and return a validated ``PlatformConfig``.

    The function performs these steps:

    1. Load the base YAML file at *path*.
    2. If *env_override* is given (e.g. ``"production"``), look for
       ``<stem>.<env_override>.yaml`` next to the base file and deep-merge it
       on top.
    3. Recursively substitute ``${ENV_VAR}`` / ``${ENV_VAR:-default}``
       patterns.
    4. Validate against the Pydantic models.
    5. Store as the global singleton for :func:`get_config`.

    Args:
        path: Path to the base YAML configuration file.
        env_override: Optional environment name whose overlay file will be
            merged on top of the base config.

    Returns:
        The validated ``PlatformConfig`` instance.

    Raises:
        FileNotFoundError: If the base config file does not exist.
        ValueError: On malformed YAML or unresolvable env vars.
        pydantic.ValidationError: If the final data fails validation.
    """
    global _config, _config_path, _config_env

    base_path = Path(path).resolve()
    data = _load_yaml_file(base_path)

    # --- overlay -----------------------------------------------------------
    if env_override:
        overlay_path = base_path.parent / f"{base_path.stem}.{env_override}.yaml"
        if overlay_path.exists():
            overlay_data = _load_yaml_file(overlay_path)
            data = _deep_merge(data, overlay_data)
            logger.info("Merged overlay config from %s", overlay_path)
        else:
            logger.warning(
                "Overlay config %s not found; using base config only",
                overlay_path,
            )

    # --- env var substitution ---------------------------------------------
    try:
        data = _deep_substitute(data)
    except ValueError as exc:
        raise ValueError(f"Environment variable substitution failed: {exc}") from exc

    # --- flatten nested YAML structure into PlatformConfig shape ----------
    data = _flatten_yaml(data)

    # --- validate ----------------------------------------------------------
    config = PlatformConfig.model_validate(data)

    with _config_lock:
        _config = config
        _config_path = base_path
        _config_env = env_override

    logger.info("Configuration loaded successfully from %s (env=%s)", base_path, env_override)
    return config


def get_config() -> PlatformConfig:
    """Return the global ``PlatformConfig`` singleton.

    Raises:
        RuntimeError: If :func:`load_config` has not been called yet.
    """
    if _config is None:
        raise RuntimeError(
            "Configuration has not been loaded. Call load_config() first."
        )
    return _config


def reload_config() -> PlatformConfig:
    """Re-read configuration from disk and update the singleton.

    Uses the same path and environment override that were passed to the
    most recent :func:`load_config` call.

    Raises:
        RuntimeError: If :func:`load_config` has never been called.
    """
    if _config_path is None:
        raise RuntimeError(
            "Cannot reload: no configuration has been loaded yet. "
            "Call load_config() first."
        )
    logger.info("Reloading configuration from %s", _config_path)
    return load_config(_config_path, _config_env)


# ---------------------------------------------------------------------------
# Hot-reload file watcher
# ---------------------------------------------------------------------------


def _watch_loop(path: Path, interval: float) -> None:
    """Background thread: poll *path* for mtime changes and reload."""
    last_mtime: float = 0.0
    try:
        last_mtime = path.stat().st_mtime
    except OSError:
        pass

    while not _watcher_stop_event.is_set():
        _watcher_stop_event.wait(timeout=interval)
        if _watcher_stop_event.is_set():
            break
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            continue

        if current_mtime != last_mtime:
            last_mtime = current_mtime
            logger.info("Config file change detected: %s", path)
            try:
                reload_config()
                logger.info("Configuration hot-reloaded successfully")
            except Exception:
                logger.exception("Failed to hot-reload configuration")


def start_config_watcher(interval: float = 2.0) -> None:
    """Start a background thread that watches for config file changes.

    Args:
        interval: Polling interval in seconds (default 2).

    Raises:
        RuntimeError: If no configuration has been loaded yet.
    """
    global _watcher_thread

    if _config_path is None:
        raise RuntimeError(
            "Cannot start watcher: no configuration has been loaded yet."
        )

    stop_config_watcher()  # ensure any previous watcher is stopped

    _watcher_stop_event.clear()
    _watcher_thread = threading.Thread(
        target=_watch_loop,
        args=(_config_path, interval),
        daemon=True,
        name="config-watcher",
    )
    _watcher_thread.start()
    logger.info(
        "Config file watcher started (interval=%.1fs, path=%s)",
        interval,
        _config_path,
    )


def stop_config_watcher() -> None:
    """Stop the background config-watcher thread, if running."""
    global _watcher_thread

    if _watcher_thread is not None and _watcher_thread.is_alive():
        _watcher_stop_event.set()
        _watcher_thread.join(timeout=5.0)
        _watcher_thread = None
        logger.info("Config file watcher stopped")
