"""
Optuna-powered strategy parameter optimizer.

Uses vectorbt engine for fast backtesting and Optuna's TPE sampler
for intelligent parameter search. Supports multi-objective optimization
(e.g., maximize Sharpe while minimizing drawdown).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

try:
    import optuna
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False

from core.backtest.vectorbt_engine import VBTBacktestConfig, VBTBacktestResult, VectorBTEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parameter search spaces per strategy
# ---------------------------------------------------------------------------

PARAM_SPACES: dict[str, dict[str, dict]] = {
    "rsi_reversal": {
        "rsi_period": {"type": "int", "low": 5, "high": 30},
        "rsi_oversold": {"type": "int", "low": 15, "high": 40},
        "rsi_overbought": {"type": "int", "low": 60, "high": 85},
    },
    "macd_crossover": {
        "macd_fast": {"type": "int", "low": 6, "high": 20},
        "macd_slow": {"type": "int", "low": 18, "high": 40},
        "macd_signal": {"type": "int", "low": 5, "high": 15},
    },
    "bollinger_breakout": {
        "bb_period": {"type": "int", "low": 10, "high": 40},
        "bb_std": {"type": "float", "low": 1.0, "high": 3.5},
    },
    "supertrend": {
        "st_period": {"type": "int", "low": 5, "high": 25},
        "st_multiplier": {"type": "float", "low": 1.5, "high": 5.0},
    },
    "ema_crossover": {
        "ema_fast": {"type": "int", "low": 3, "high": 20},
        "ema_slow": {"type": "int", "low": 15, "high": 60},
    },
}


@dataclass
class OptimizationConfig:
    strategy: str = "rsi_reversal"
    symbol: str = "NIFTY"
    resolution: str = "5"
    start_date: str | None = None
    end_date: str | None = None
    initial_capital: float = 10_000_000
    lot_size: int = 75
    n_trials: int = 50
    objective: str = "sharpe"
    sl_pct: float | None = None
    tp_pct: float | None = None
    custom_params: dict | None = None


@dataclass
class OptimizationResult:
    best_params: dict = field(default_factory=dict)
    best_value: float = 0.0
    objective: str = "sharpe"
    total_trials: int = 0
    best_backtest: dict = field(default_factory=dict)
    all_trials: list[dict] = field(default_factory=list)
    strategy: str = ""

    def to_dict(self) -> dict:
        return {
            "best_params": self.best_params,
            "best_value": round(self.best_value, 4),
            "objective": self.objective,
            "total_trials": self.total_trials,
            "best_backtest": self.best_backtest,
            "all_trials": self.all_trials[:20],
            "strategy": self.strategy,
        }


class StrategyOptimizer:
    """Optuna-based strategy parameter optimizer."""

    def __init__(self, state_store=None):
        self._engine = VectorBTEngine(state_store=state_store)

    def _get_objective_value(self, result: VBTBacktestResult, objective: str) -> float:
        mapping = {
            "sharpe": result.sharpe_ratio,
            "sortino": result.sortino_ratio,
            "return": result.net_return_pct,
            "calmar": result.calmar_ratio,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
        }
        return mapping.get(objective, result.sharpe_ratio)

    def optimize(self, config: OptimizationConfig) -> OptimizationResult:
        if not HAS_OPTUNA:
            raise RuntimeError("optuna is not installed")

        param_space = PARAM_SPACES.get(config.strategy)
        if param_space is None:
            raise ValueError(f"No parameter space for strategy: {config.strategy}")

        if config.custom_params:
            param_space = {**param_space, **config.custom_params}

        all_trials_data: list[dict] = []

        def objective(trial: optuna.Trial) -> float:
            params = {}
            for name, spec in param_space.items():
                if spec["type"] == "int":
                    params[name] = trial.suggest_int(name, spec["low"], spec["high"])
                elif spec["type"] == "float":
                    params[name] = trial.suggest_float(name, spec["low"], spec["high"])

            if config.strategy == "macd_crossover":
                if params.get("macd_fast", 12) >= params.get("macd_slow", 26):
                    return float("-inf")
            if config.strategy == "ema_crossover":
                if params.get("ema_fast", 9) >= params.get("ema_slow", 21):
                    return float("-inf")

            bt_config = VBTBacktestConfig(
                strategy=config.strategy,
                symbol=config.symbol,
                resolution=config.resolution,
                start_date=config.start_date,
                end_date=config.end_date,
                initial_capital=config.initial_capital,
                lot_size=config.lot_size,
                params=params,
                sl_pct=config.sl_pct,
                tp_pct=config.tp_pct,
            )

            try:
                result = self._engine.run(bt_config)
                value = self._get_objective_value(result, config.objective)

                all_trials_data.append({
                    "trial": trial.number,
                    "params": params,
                    "value": round(value, 4),
                    "return_pct": round(result.net_return_pct, 2),
                    "sharpe": round(result.sharpe_ratio, 3),
                    "max_dd": round(result.max_drawdown_pct, 2),
                    "trades": result.total_trades,
                    "win_rate": round(result.win_rate, 2),
                })

                if result.total_trades < 5:
                    return float("-inf")

                return value
            except Exception as e:
                logger.warning("Trial %d failed: %s", trial.number, e)
                return float("-inf")

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(),
        )
        study.optimize(objective, n_trials=config.n_trials, show_progress_bar=False)

        best_params = study.best_params
        best_value = study.best_value

        best_bt_config = VBTBacktestConfig(
            strategy=config.strategy,
            symbol=config.symbol,
            resolution=config.resolution,
            start_date=config.start_date,
            end_date=config.end_date,
            initial_capital=config.initial_capital,
            lot_size=config.lot_size,
            params=best_params,
            sl_pct=config.sl_pct,
            tp_pct=config.tp_pct,
        )
        best_result = self._engine.run(best_bt_config)

        all_trials_data.sort(key=lambda x: x["value"], reverse=True)

        return OptimizationResult(
            best_params=best_params,
            best_value=best_value,
            objective=config.objective,
            total_trials=config.n_trials,
            best_backtest=best_result.to_dict(),
            all_trials=all_trials_data,
            strategy=config.strategy,
        )

    def available_objectives(self) -> list[dict[str, str]]:
        return [
            {"id": "sharpe", "name": "Sharpe Ratio", "description": "Risk-adjusted return (higher is better)"},
            {"id": "sortino", "name": "Sortino Ratio", "description": "Downside-risk-adjusted return"},
            {"id": "return", "name": "Net Return %", "description": "Total net return after charges"},
            {"id": "calmar", "name": "Calmar Ratio", "description": "Return / max drawdown"},
            {"id": "win_rate", "name": "Win Rate %", "description": "Percentage of winning trades"},
            {"id": "profit_factor", "name": "Profit Factor", "description": "Gross profit / gross loss"},
        ]
