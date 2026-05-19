"""
Comprehensive unit tests for core/config.py — configuration system.

Covers env-var substitution, deep merge, YAML loading, singleton behaviour,
reload, and error handling.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.config import (
    PlatformConfig,
    _deep_merge,
    _deep_substitute,
    _substitute_env_vars,
    get_config,
    load_config,
    reload_config,
)

# We need to reset the module-level singleton between tests.
import core.config as _config_module


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the global config singleton before and after each test."""
    _config_module._config = None
    _config_module._config_path = None
    _config_module._config_env = None
    yield
    _config_module._config = None
    _config_module._config_path = None
    _config_module._config_env = None


# ===================================================================
# 1. _substitute_env_vars
# ===================================================================


class TestSubstituteEnvVars:
    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert _substitute_env_vars("${MY_VAR}") == "hello"

    def test_var_with_surrounding_text(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        assert _substitute_env_vars("http://${HOST}:8080") == "http://localhost:8080"

    def test_default_value_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR", raising=False)
        assert _substitute_env_vars("${UNSET_VAR:-fallback}") == "fallback"

    def test_env_value_overrides_default(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "real")
        assert _substitute_env_vars("${MY_VAR:-fallback}") == "real"

    def test_empty_default(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR", raising=False)
        assert _substitute_env_vars("${UNSET_VAR:-}") == ""

    def test_missing_var_no_default_raises(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(ValueError, match="MISSING_VAR"):
            _substitute_env_vars("${MISSING_VAR}")

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _substitute_env_vars("${A}-${B}") == "1-2"

    def test_no_vars_returns_unchanged(self):
        assert _substitute_env_vars("plain string") == "plain string"

    def test_default_with_spaces(self, monkeypatch):
        monkeypatch.delenv("X", raising=False)
        result = _substitute_env_vars("${X:- spaced }")
        # The regex pattern strips leading spaces after :- so " spaced " -> "spaced "
        assert result == "spaced "


# ===================================================================
# 2. _deep_substitute
# ===================================================================


class TestDeepSubstitute:
    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "myhost")
        obj = {"database": {"host": "${DB_HOST}", "port": 5432}}
        result = _deep_substitute(obj)
        assert result == {"database": {"host": "myhost", "port": 5432}}

    def test_list(self, monkeypatch):
        monkeypatch.setenv("ITEM", "val")
        obj = ["${ITEM}", "static"]
        result = _deep_substitute(obj)
        assert result == ["val", "static"]

    def test_non_string_passthrough(self):
        assert _deep_substitute(42) == 42
        assert _deep_substitute(3.14) == 3.14
        assert _deep_substitute(True) is True
        assert _deep_substitute(None) is None

    def test_deeply_nested(self, monkeypatch):
        monkeypatch.setenv("V", "deep")
        obj = {"a": {"b": {"c": [{"d": "${V}"}]}}}
        result = _deep_substitute(obj)
        assert result["a"]["b"]["c"][0]["d"] == "deep"


# ===================================================================
# 3. _deep_merge
# ===================================================================


class TestDeepMerge:
    def test_simple_overlay(self):
        base = {"a": 1, "b": 2}
        overlay = {"b": 3, "c": 4}
        result = _deep_merge(base, overlay)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"db": {"host": "localhost", "port": 5432}}
        overlay = {"db": {"host": "prod-server"}}
        result = _deep_merge(base, overlay)
        assert result == {"db": {"host": "prod-server", "port": 5432}}

    def test_overlay_replaces_list(self):
        base = {"items": [1, 2, 3]}
        overlay = {"items": [4, 5]}
        result = _deep_merge(base, overlay)
        assert result["items"] == [4, 5]

    def test_overlay_replaces_dict_with_scalar(self):
        base = {"x": {"nested": True}}
        overlay = {"x": "flat"}
        result = _deep_merge(base, overlay)
        assert result["x"] == "flat"

    def test_base_not_mutated(self):
        base = {"a": {"b": 1}}
        overlay = {"a": {"c": 2}}
        _deep_merge(base, overlay)
        assert "c" not in base["a"]

    def test_empty_overlay(self):
        base = {"a": 1}
        assert _deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self):
        overlay = {"a": 1}
        assert _deep_merge({}, overlay) == {"a": 1}


# ===================================================================
# 4. load_config with valid YAML
# ===================================================================


class TestLoadConfig:
    def _write_yaml(self, tmp_path: Path, filename: str, content: str) -> Path:
        p = tmp_path / filename
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_basic_load(self, tmp_path):
        cfg_path = self._write_yaml(tmp_path, "config.yaml", """\
            mode: paper
            timezone: "Asia/Kolkata"
            log_level: DEBUG
        """)
        cfg = load_config(cfg_path)
        assert isinstance(cfg, PlatformConfig)
        assert cfg.mode == "paper"
        assert cfg.log_level == "DEBUG"

    def test_load_with_env_overlay(self, tmp_path):
        self._write_yaml(tmp_path, "config.yaml", """\
            mode: paper
            log_level: INFO
        """)
        self._write_yaml(tmp_path, "config.production.yaml", """\
            mode: live
            log_level: WARNING
        """)
        cfg = load_config(tmp_path / "config.yaml", env_override="production")
        assert cfg.mode == "live"
        assert cfg.log_level == "WARNING"

    def test_env_overlay_not_found_uses_base(self, tmp_path):
        cfg_path = self._write_yaml(tmp_path, "config.yaml", """\
            mode: paper
        """)
        cfg = load_config(cfg_path, env_override="nonexistent")
        assert cfg.mode == "paper"

    def test_env_var_substitution_in_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADING_MODE", "backtest")
        cfg_path = self._write_yaml(tmp_path, "config.yaml", """\
            mode: "${TRADING_MODE}"
        """)
        cfg = load_config(cfg_path)
        assert cfg.mode == "backtest"

    def test_sets_singleton(self, tmp_path):
        cfg_path = self._write_yaml(tmp_path, "config.yaml", "mode: paper")
        load_config(cfg_path)
        assert get_config().mode == "paper"

    def test_empty_yaml_returns_defaults(self, tmp_path):
        cfg_path = self._write_yaml(tmp_path, "config.yaml", "")
        cfg = load_config(cfg_path)
        assert cfg.mode == "paper"  # default
        assert cfg.timezone == "Asia/Kolkata"


# ===================================================================
# 5. get_config raises before loading
# ===================================================================


class TestGetConfig:
    def test_raises_before_load(self):
        with pytest.raises(RuntimeError, match="Configuration has not been loaded"):
            get_config()

    def test_works_after_load(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("mode: paper", encoding="utf-8")
        load_config(p)
        cfg = get_config()
        assert cfg.mode == "paper"


# ===================================================================
# 6. reload_config
# ===================================================================


class TestReloadConfig:
    def test_reload_raises_before_load(self):
        with pytest.raises(RuntimeError, match="no configuration has been loaded"):
            reload_config()

    def test_reload_picks_up_changes(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("mode: paper", encoding="utf-8")
        load_config(cfg_path)
        assert get_config().mode == "paper"

        cfg_path.write_text("mode: live", encoding="utf-8")
        reload_config()
        assert get_config().mode == "live"


# ===================================================================
# 7. Error handling
# ===================================================================


class TestConfigErrors:
    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_malformed_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: [invalid\n  yaml: {", encoding="utf-8")
        with pytest.raises(ValueError, match="Malformed YAML"):
            load_config(bad)

    def test_yaml_not_a_mapping(self, tmp_path):
        bad = tmp_path / "list.yaml"
        bad.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Expected a YAML mapping"):
            load_config(bad)

    def test_missing_required_env_var_in_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv("REQUIRED_SECRET", raising=False)
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text('primary_broker: "${REQUIRED_SECRET}"', encoding="utf-8")
        with pytest.raises(ValueError, match="REQUIRED_SECRET"):
            load_config(cfg_path)

    def test_invalid_field_type(self, tmp_path):
        """Pydantic validation error for wrong type."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("risk:\n  max_daily_loss: not_a_number\n", encoding="utf-8")
        with pytest.raises(Exception):
            # Will raise pydantic.ValidationError
            load_config(cfg_path)
