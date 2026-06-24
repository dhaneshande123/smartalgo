"""
QuantStats-powered tearsheet and report generation.

Generates professional portfolio analytics reports from backtest results,
including Sharpe, Sortino, drawdown analysis, and benchmark comparison vs NIFTY.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

try:
    import quantstats as qs
    HAS_QS = True
except ImportError:
    HAS_QS = False

logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "reports",
)


def _ensure_reports_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)


def _equity_to_returns(equity_curve: list[tuple[str, float]]) -> pd.Series:
    if not equity_curve:
        return pd.Series(dtype=float)
    dates = [e[0] for e in equity_curve]
    values = [e[1] for e in equity_curve]
    idx = pd.to_datetime(dates)
    eq = pd.Series(values, index=idx, name="Strategy")
    eq = eq[~eq.index.duplicated(keep="first")]
    returns = eq.pct_change().dropna()
    returns = returns.replace([np.inf, -np.inf], 0.0)
    return returns


def generate_metrics(equity_curve: list[tuple[str, float]],
                     risk_free_rate: float = 0.07) -> dict[str, Any]:
    """Generate comprehensive metrics from an equity curve using QuantStats."""
    if not HAS_QS:
        return {"error": "quantstats not installed"}

    returns = _equity_to_returns(equity_curve)
    if returns.empty or len(returns) < 2:
        return {"error": "Not enough data points for analysis"}

    try:
        metrics: dict[str, Any] = {}

        metrics["total_return_pct"] = round(float(qs.stats.comp(returns) * 100), 2)
        metrics["cagr_pct"] = round(float(qs.stats.cagr(returns, rf=risk_free_rate) * 100), 2)
        metrics["sharpe"] = round(float(qs.stats.sharpe(returns, rf=risk_free_rate)), 3)
        metrics["sortino"] = round(float(qs.stats.sortino(returns, rf=risk_free_rate)), 3)
        metrics["max_drawdown_pct"] = round(float(qs.stats.max_drawdown(returns) * 100), 2)
        metrics["volatility_pct"] = round(float(qs.stats.volatility(returns) * 100), 2)
        metrics["calmar"] = round(float(qs.stats.calmar(returns)), 3)

        metrics["win_rate_pct"] = round(float(qs.stats.win_rate(returns) * 100), 2)
        metrics["avg_win_pct"] = round(float(qs.stats.avg_win(returns) * 100), 4)
        metrics["avg_loss_pct"] = round(float(qs.stats.avg_loss(returns) * 100), 4)
        metrics["best_day_pct"] = round(float(qs.stats.best(returns) * 100), 2)
        metrics["worst_day_pct"] = round(float(qs.stats.worst(returns) * 100), 2)
        metrics["profit_factor"] = round(float(qs.stats.profit_factor(returns)), 3)

        metrics["skew"] = round(float(qs.stats.skew(returns)), 3)
        metrics["kurtosis"] = round(float(qs.stats.kurtosis(returns)), 3)

        dd = qs.stats.to_drawdown_series(returns)
        if len(dd) > 0:
            metrics["avg_drawdown_pct"] = round(float(dd[dd < 0].mean() * 100) if (dd < 0).any() else 0.0, 2)
            metrics["avg_drawdown_days"] = int(qs.stats.avg_drawdown_days(returns)) if hasattr(qs.stats, "avg_drawdown_days") else 0

        monthly = qs.stats.monthly_returns(returns)
        if monthly is not None and not monthly.empty:
            monthly_dict = {}
            for year_idx in monthly.index:
                for month_col in monthly.columns:
                    val = monthly.loc[year_idx, month_col]
                    if not pd.isna(val):
                        monthly_dict[f"{int(year_idx)}-{str(month_col).zfill(2)}"] = round(float(val), 2)
            metrics["monthly_returns"] = monthly_dict

        return metrics
    except Exception as e:
        logger.error("Error generating metrics: %s", e)
        return {"error": str(e)}


def generate_html_tearsheet(
    equity_curve: list[tuple[str, float]],
    strategy_name: str = "Strategy",
    benchmark_curve: list[tuple[str, float]] | None = None,
) -> str | None:
    """Generate an HTML tearsheet and return the file path."""
    if not HAS_QS:
        return None

    _ensure_reports_dir()
    returns = _equity_to_returns(equity_curve)
    if returns.empty or len(returns) < 2:
        return None

    benchmark = None
    if benchmark_curve:
        benchmark = _equity_to_returns(benchmark_curve)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"tearsheet_{strategy_name.replace(' ', '_')}_{timestamp}.html"
    filepath = os.path.join(REPORTS_DIR, filename)

    try:
        qs.reports.html(
            returns,
            benchmark=benchmark,
            title=f"{strategy_name} — Performance Report",
            output=filepath,
        )
        return filepath
    except Exception as e:
        logger.error("Error generating tearsheet: %s", e)
        return None


def generate_snapshot(equity_curve: list[tuple[str, float]]) -> dict[str, Any]:
    """Quick performance snapshot — lightweight alternative to full tearsheet."""
    returns = _equity_to_returns(equity_curve)
    if returns.empty:
        return {}

    eq = (1 + returns).cumprod()
    peak = eq.cummax()
    dd = (eq - peak) / peak

    daily_rets = returns.values
    neg_rets = daily_rets[daily_rets < 0]
    pos_rets = daily_rets[daily_rets > 0]

    rf_daily = 0.07 / 252
    excess = daily_rets - rf_daily
    sharpe = float(np.mean(excess) / np.std(excess) * np.sqrt(252)) if np.std(excess) > 0 else 0.0
    downside_std = float(np.std(neg_rets)) if len(neg_rets) > 0 else 1e-10
    sortino = float(np.mean(excess) / downside_std * np.sqrt(252))

    return {
        "total_return_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown_pct": round(float(dd.min() * 100), 2),
        "volatility_pct": round(float(np.std(daily_rets) * np.sqrt(252) * 100), 2),
        "win_rate_pct": round(float(len(pos_rets) / len(daily_rets) * 100) if len(daily_rets) > 0 else 0.0, 2),
        "best_day_pct": round(float(np.max(daily_rets) * 100), 2) if len(daily_rets) > 0 else 0.0,
        "worst_day_pct": round(float(np.min(daily_rets) * 100), 2) if len(daily_rets) > 0 else 0.0,
        "total_days": len(daily_rets),
        "profit_factor": round(float(np.sum(pos_rets) / abs(np.sum(neg_rets))), 3) if len(neg_rets) > 0 and np.sum(neg_rets) != 0 else 0.0,
    }


def compare_strategies(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare multiple backtest results side by side."""
    if not results:
        return {"strategies": []}

    comparison = []
    for r in results:
        comparison.append({
            "strategy": r.get("strategy", "Unknown"),
            "params": r.get("params_used", {}),
            "return_pct": r.get("net_return_pct", r.get("total_return_pct", 0)),
            "sharpe": r.get("sharpe_ratio", 0),
            "sortino": r.get("sortino_ratio", 0),
            "max_dd": r.get("max_drawdown_pct", 0),
            "win_rate": r.get("win_rate", 0),
            "trades": r.get("total_trades", 0),
            "profit_factor": r.get("profit_factor", 0),
            "charges": r.get("total_charges", 0),
        })

    comparison.sort(key=lambda x: x["sharpe"], reverse=True)

    return {
        "strategies": comparison,
        "best_sharpe": comparison[0]["strategy"] if comparison else None,
        "best_return": max(comparison, key=lambda x: x["return_pct"])["strategy"] if comparison else None,
        "lowest_dd": min(comparison, key=lambda x: x["max_dd"])["strategy"] if comparison else None,
    }
