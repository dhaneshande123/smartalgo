"""
Performance Analyzer — computes institutional-grade trading metrics.

Given a list of trades and an equity curve, produces Sharpe, Sortino,
Calmar ratios, max drawdown, profit factor, monthly returns, and a
human-readable report.  All methods are static — no external dependencies
beyond the standard library.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from core.models import OrderSide, Trade


class PerformanceAnalyzer:
    """Computes trading performance metrics from trade history.

    All methods are static so callers can use them individually or through
    the aggregate ``compute_metrics`` entry point.

    Metrics produced:
    - Returns: total, annualised, monthly breakdown
    - Risk-adjusted: Sharpe, Sortino, Calmar
    - Trade statistics: win rate, profit factor, avg trade, payoff ratio
    - Drawdown: max drawdown (absolute and percentage), peak/trough times
    """

    # ------------------------------------------------------------------
    # Aggregate entry point
    # ------------------------------------------------------------------

    @staticmethod
    def compute_metrics(
        trades: list[Trade],
        equity_curve: list[tuple[datetime, float]],
        initial_capital: float,
        risk_free_rate: float = 0.07,
    ) -> dict[str, Any]:
        """Compute all performance metrics in one call.

        Args:
            trades: Chronologically sorted list of executed trades.
            equity_curve: List of ``(timestamp, equity)`` snapshots.
            initial_capital: Starting capital.
            risk_free_rate: Annualised risk-free rate (default 7 % for India).

        Returns:
            A dictionary containing all computed metrics.
        """
        PA = PerformanceAnalyzer

        # -- Basic P&L --
        final_capital = equity_curve[-1][1] if equity_curve else initial_capital
        total_return = final_capital - initial_capital
        total_return_pct = (
            (total_return / initial_capital * 100.0) if initial_capital else 0.0
        )

        # -- Round-trip trade matching --
        round_trips = PA._compute_round_trips(trades)
        winners = [rt for rt in round_trips if rt > 0]
        losers = [rt for rt in round_trips if rt < 0]

        total_trades = len(round_trips)
        winning_trades = len(winners)
        losing_trades = len(losers)
        win_rate = (winning_trades / total_trades * 100.0) if total_trades else 0.0

        gross_profit = sum(winners) if winners else 0.0
        gross_loss = abs(sum(losers)) if losers else 0.0
        profit_factor = (
            (gross_profit / gross_loss)
            if gross_loss > 0
            else (float("inf") if gross_profit > 0 else 0.0)
        )

        avg_trade_pnl = (sum(round_trips) / total_trades) if total_trades else 0.0
        avg_winner = (sum(winners) / winning_trades) if winning_trades else 0.0
        avg_loser = (sum(losers) / losing_trades) if losing_trades else 0.0
        largest_winner = max(winners) if winners else 0.0
        largest_loser = min(losers) if losers else 0.0

        payoff_ratio = (
            (avg_winner / abs(avg_loser))
            if avg_loser != 0
            else (float("inf") if avg_winner > 0 else 0.0)
        )

        # -- Daily returns --
        daily_returns = PA._compute_daily_returns(equity_curve)
        daily_return_values = [r for _, r in daily_returns]

        # -- Risk metrics --
        max_dd_abs, max_dd_pct, peak_time, trough_time = PA.compute_max_drawdown(
            equity_curve
        )
        sharpe = PA.compute_sharpe_ratio(daily_return_values, risk_free_rate)
        sortino = PA.compute_sortino_ratio(daily_return_values, risk_free_rate)

        # -- Annualised return for Calmar --
        if len(daily_returns) > 1:
            trading_days = len(daily_returns)
            total_ret_frac = total_return / initial_capital if initial_capital else 0.0
            annual_return = (
                ((1.0 + total_ret_frac) ** (252.0 / trading_days) - 1.0)
                if trading_days > 0
                else 0.0
            )
        else:
            annual_return = 0.0

        calmar = PA.compute_calmar_ratio(
            annual_return, max_dd_pct / 100.0 if max_dd_pct else 0.0
        )

        # -- Holding period --
        avg_holding = PA._compute_avg_holding_period(trades)

        # -- Monthly returns --
        monthly_returns = PA.compute_monthly_returns(equity_curve)

        return {
            "initial_capital": initial_capital,
            "final_capital": round(final_capital, 2),
            "total_return": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "annual_return_pct": round(annual_return * 100.0, 2),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 2),
            "profit_factor": (
                round(profit_factor, 4)
                if profit_factor != float("inf")
                else float("inf")
            ),
            "payoff_ratio": (
                round(payoff_ratio, 4)
                if payoff_ratio != float("inf")
                else float("inf")
            ),
            "avg_trade_pnl": round(avg_trade_pnl, 2),
            "avg_winner": round(avg_winner, 2),
            "avg_loser": round(avg_loser, 2),
            "largest_winner": round(largest_winner, 2),
            "largest_loser": round(largest_loser, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "max_drawdown": round(max_dd_abs, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "drawdown_peak_time": peak_time,
            "drawdown_trough_time": trough_time,
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "calmar_ratio": round(calmar, 4),
            "avg_holding_period_minutes": round(avg_holding, 2),
            "daily_returns": daily_returns,
            "monthly_returns": monthly_returns,
            "equity_curve": equity_curve,
        }

    # ------------------------------------------------------------------
    # Individual metrics
    # ------------------------------------------------------------------

    @staticmethod
    def compute_sharpe_ratio(
        daily_returns: list[float],
        risk_free_rate: float = 0.07,
    ) -> float:
        """Compute annualised Sharpe ratio from daily returns.

        Uses 252 trading days per year.  The risk-free rate is converted
        to a daily rate before subtracting.

        Args:
            daily_returns: List of daily fractional returns.
            risk_free_rate: Annualised risk-free rate.

        Returns:
            The Sharpe ratio, or 0.0 if insufficient data.
        """
        if len(daily_returns) < 2:
            return 0.0

        daily_rf = (1.0 + risk_free_rate) ** (1.0 / 252.0) - 1.0
        excess = [r - daily_rf for r in daily_returns]
        mean_excess = sum(excess) / len(excess)
        variance = sum((r - mean_excess) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std == 0.0:
            return 0.0

        return (mean_excess / std) * math.sqrt(252.0)

    @staticmethod
    def compute_sortino_ratio(
        daily_returns: list[float],
        risk_free_rate: float = 0.07,
    ) -> float:
        """Compute annualised Sortino ratio from daily returns.

        Only downside deviation (negative excess returns) is used in the
        denominator.

        Args:
            daily_returns: List of daily fractional returns.
            risk_free_rate: Annualised risk-free rate.

        Returns:
            The Sortino ratio, or 0.0 if insufficient data.
        """
        if len(daily_returns) < 2:
            return 0.0

        daily_rf = (1.0 + risk_free_rate) ** (1.0 / 252.0) - 1.0
        excess = [r - daily_rf for r in daily_returns]
        mean_excess = sum(excess) / len(excess)

        downside_sq = [min(r, 0.0) ** 2 for r in excess]
        downside_dev = (
            math.sqrt(sum(downside_sq) / len(downside_sq)) if downside_sq else 0.0
        )

        if downside_dev == 0.0:
            return 0.0

        return (mean_excess / downside_dev) * math.sqrt(252.0)

    @staticmethod
    def compute_calmar_ratio(
        annual_return: float, max_drawdown_frac: float
    ) -> float:
        """Compute Calmar ratio (annual return / max drawdown).

        Args:
            annual_return: Annualised return as a fraction (e.g. 0.15 = 15 %).
            max_drawdown_frac: Max drawdown as a positive fraction (0.10 = 10 %).

        Returns:
            The Calmar ratio, or 0.0 if drawdown is zero.
        """
        if max_drawdown_frac <= 0:
            return 0.0
        return annual_return / max_drawdown_frac

    @staticmethod
    def compute_max_drawdown(
        equity_curve: list[tuple[datetime, float]],
    ) -> tuple[float, float, datetime | None, datetime | None]:
        """Compute maximum drawdown from an equity curve.

        Args:
            equity_curve: List of ``(timestamp, equity)`` snapshots.

        Returns:
            A tuple of ``(max_dd_abs, max_dd_pct, peak_time, trough_time)``.
            If the curve is empty, all values are zero/None.
        """
        if not equity_curve:
            return 0.0, 0.0, None, None

        peak = equity_curve[0][1]
        peak_time = equity_curve[0][0]
        max_dd_abs = 0.0
        max_dd_pct = 0.0
        trough_time: datetime | None = None
        best_peak_time: datetime | None = peak_time

        for ts, equity in equity_curve:
            if equity > peak:
                peak = equity
                peak_time = ts

            dd_abs = peak - equity
            dd_pct = (dd_abs / peak * 100.0) if peak > 0 else 0.0

            if dd_abs > max_dd_abs:
                max_dd_abs = dd_abs
                max_dd_pct = dd_pct
                trough_time = ts
                best_peak_time = peak_time

        return max_dd_abs, max_dd_pct, best_peak_time, trough_time

    @staticmethod
    def compute_profit_factor(trades: list[Trade]) -> float:
        """Compute profit factor from raw trades.

        This is a convenience wrapper that does round-trip matching
        internally.

        Args:
            trades: List of executed trades.

        Returns:
            Profit factor (gross profit / gross loss).
        """
        round_trips = PerformanceAnalyzer._compute_round_trips(trades)
        gross_profit = sum(r for r in round_trips if r > 0)
        gross_loss = abs(sum(r for r in round_trips if r < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @staticmethod
    def compute_monthly_returns(
        equity_curve: list[tuple[datetime, float]],
    ) -> dict[str, float]:
        """Compute monthly percentage returns from an equity curve.

        Groups equity snapshots by ``YYYY-MM`` and computes the return
        from the last snapshot of the prior month to the last snapshot
        of the current month.

        Args:
            equity_curve: List of ``(timestamp, equity)`` snapshots.

        Returns:
            A dictionary mapping ``"YYYY-MM"`` to percentage return.
        """
        if len(equity_curve) < 2:
            return {}

        # Group by month
        monthly: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        for ts, eq in equity_curve:
            key = ts.strftime("%Y-%m")
            monthly[key].append((ts, eq))

        result: dict[str, float] = {}
        sorted_months = sorted(monthly.keys())

        prev_equity: float | None = None
        for month_key in sorted_months:
            points = monthly[month_key]
            start_eq = prev_equity if prev_equity is not None else points[0][1]
            end_eq = points[-1][1]

            if start_eq != 0:
                ret_pct = (end_eq - start_eq) / abs(start_eq) * 100.0
            else:
                ret_pct = 0.0

            result[month_key] = round(ret_pct, 4)
            prev_equity = end_eq

        return result

    @staticmethod
    def generate_report(metrics: dict[str, Any]) -> str:
        """Generate a human-readable performance report.

        Args:
            metrics: The dictionary returned by ``compute_metrics``.

        Returns:
            A formatted multi-line string.
        """
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("       BACKTEST PERFORMANCE REPORT")
        lines.append("=" * 60)
        lines.append("")

        # -- Capital & Returns --
        lines.append("--- Capital & Returns ---")
        lines.append(
            f"  Initial Capital     : {metrics.get('initial_capital', 0):>15,.2f}"
        )
        lines.append(
            f"  Final Capital       : {metrics.get('final_capital', 0):>15,.2f}"
        )
        lines.append(
            f"  Total Return        : {metrics.get('total_return', 0):>15,.2f}"
        )
        lines.append(
            f"  Total Return %      : {metrics.get('total_return_pct', 0):>14.2f}%"
        )
        lines.append(
            f"  Annualised Return % : {metrics.get('annual_return_pct', 0):>14.2f}%"
        )
        lines.append("")

        # -- Trade Statistics --
        lines.append("--- Trade Statistics ---")
        lines.append(
            f"  Total Trades        : {metrics.get('total_trades', 0):>15d}"
        )
        lines.append(
            f"  Winning Trades      : {metrics.get('winning_trades', 0):>15d}"
        )
        lines.append(
            f"  Losing Trades       : {metrics.get('losing_trades', 0):>15d}"
        )
        lines.append(
            f"  Win Rate            : {metrics.get('win_rate', 0):>14.2f}%"
        )
        pf = metrics.get("profit_factor", 0)
        pf_str = f"{pf:>15.4f}" if pf != float("inf") else "            inf"
        lines.append(f"  Profit Factor       : {pf_str}")
        lines.append(
            f"  Avg Trade P&L       : {metrics.get('avg_trade_pnl', 0):>15,.2f}"
        )
        lines.append(
            f"  Avg Winner          : {metrics.get('avg_winner', 0):>15,.2f}"
        )
        lines.append(
            f"  Avg Loser           : {metrics.get('avg_loser', 0):>15,.2f}"
        )
        lines.append(
            f"  Largest Winner      : {metrics.get('largest_winner', 0):>15,.2f}"
        )
        lines.append(
            f"  Largest Loser       : {metrics.get('largest_loser', 0):>15,.2f}"
        )
        lines.append(
            f"  Avg Holding (min)   : {metrics.get('avg_holding_period_minutes', 0):>15.1f}"
        )
        lines.append("")

        # -- Risk Metrics --
        lines.append("--- Risk Metrics ---")
        lines.append(
            f"  Max Drawdown        : {metrics.get('max_drawdown', 0):>15,.2f}"
        )
        lines.append(
            f"  Max Drawdown %      : {metrics.get('max_drawdown_pct', 0):>14.2f}%"
        )
        lines.append(
            f"  Sharpe Ratio        : {metrics.get('sharpe_ratio', 0):>15.4f}"
        )
        lines.append(
            f"  Sortino Ratio       : {metrics.get('sortino_ratio', 0):>15.4f}"
        )
        lines.append(
            f"  Calmar Ratio        : {metrics.get('calmar_ratio', 0):>15.4f}"
        )
        lines.append("")

        # -- Monthly Returns --
        monthly = metrics.get("monthly_returns", {})
        if monthly:
            lines.append("--- Monthly Returns ---")
            for month, ret in sorted(monthly.items()):
                lines.append(f"  {month} : {ret:>8.2f}%")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_round_trips(trades: list[Trade]) -> list[float]:
        """Match trades into round-trip P&L values using FIFO.

        Groups trades by symbol, then matches buys against sells to
        compute per-round-trip P&L.

        Args:
            trades: All executed trades (chronological order).

        Returns:
            A list of P&L values, one per completed round trip.
        """
        by_symbol: dict[str, list[Trade]] = defaultdict(list)
        for t in trades:
            by_symbol[t.instrument.symbol].append(t)

        round_trip_pnls: list[float] = []

        for _symbol, sym_trades in by_symbol.items():
            # Sort by timestamp within each symbol
            sym_trades = sorted(sym_trades, key=lambda t: t.timestamp)

            # Track open inventory as (qty, price) entries
            longs: list[tuple[int, float]] = []
            shorts: list[tuple[int, float]] = []

            for trade in sym_trades:
                qty = trade.quantity
                price = float(trade.price)

                if trade.side == OrderSide.BUY:
                    # Try to close short positions first (FIFO)
                    remaining = qty
                    while remaining > 0 and shorts:
                        short_qty, short_price = shorts[0]
                        close_qty = min(remaining, short_qty)
                        pnl = (short_price - price) * close_qty
                        round_trip_pnls.append(pnl)
                        remaining -= close_qty
                        if close_qty == short_qty:
                            shorts.pop(0)
                        else:
                            shorts[0] = (short_qty - close_qty, short_price)

                    if remaining > 0:
                        longs.append((remaining, price))

                else:  # SELL
                    # Try to close long positions first (FIFO)
                    remaining = qty
                    while remaining > 0 and longs:
                        long_qty, long_price = longs[0]
                        close_qty = min(remaining, long_qty)
                        pnl = (price - long_price) * close_qty
                        round_trip_pnls.append(pnl)
                        remaining -= close_qty
                        if close_qty == long_qty:
                            longs.pop(0)
                        else:
                            longs[0] = (long_qty - close_qty, long_price)

                    if remaining > 0:
                        shorts.append((remaining, price))

        return round_trip_pnls

    @staticmethod
    def _compute_daily_returns(
        equity_curve: list[tuple[datetime, float]],
    ) -> list[tuple[date, float]]:
        """Compute daily fractional returns from an equity curve.

        Groups equity snapshots by date and takes the last snapshot
        of each day to compute close-to-close returns.

        Args:
            equity_curve: List of ``(timestamp, equity)`` snapshots.

        Returns:
            List of ``(date, fractional_return)`` tuples.
        """
        if len(equity_curve) < 2:
            return []

        # Get end-of-day equity for each date
        daily_equity: dict[date, float] = {}
        for ts, eq in equity_curve:
            d = ts.date() if isinstance(ts, datetime) else ts
            daily_equity[d] = eq  # last value wins

        sorted_dates = sorted(daily_equity.keys())
        if len(sorted_dates) < 2:
            return []

        returns: list[tuple[date, float]] = []
        for i in range(1, len(sorted_dates)):
            prev_eq = daily_equity[sorted_dates[i - 1]]
            curr_eq = daily_equity[sorted_dates[i]]
            if prev_eq != 0:
                ret = (curr_eq - prev_eq) / abs(prev_eq)
            else:
                ret = 0.0
            returns.append((sorted_dates[i], ret))

        return returns

    @staticmethod
    def _compute_avg_holding_period(trades: list[Trade]) -> float:
        """Estimate average holding period in minutes from trade timestamps.

        Pairs entry and exit trades by symbol using FIFO matching and
        computes the average time between entry and exit.

        Args:
            trades: All executed trades.

        Returns:
            Average holding period in minutes, or 0.0 if no round trips.
        """
        by_symbol: dict[str, list[Trade]] = defaultdict(list)
        for t in trades:
            by_symbol[t.instrument.symbol].append(t)

        holding_times: list[float] = []

        for _symbol, sym_trades in by_symbol.items():
            sym_trades = sorted(sym_trades, key=lambda t: t.timestamp)
            entries: list[tuple[int, datetime, OrderSide]] = []

            for trade in sym_trades:
                qty = trade.quantity
                ts = trade.timestamp

                if not entries or entries[0][2] == trade.side:
                    # Same direction — accumulate
                    entries.append((qty, ts, trade.side))
                else:
                    # Opposite direction — close FIFO
                    remaining = qty
                    while remaining > 0 and entries:
                        entry_qty, entry_ts, entry_side = entries[0]
                        close_qty = min(remaining, entry_qty)
                        delta_minutes = (ts - entry_ts).total_seconds() / 60.0
                        holding_times.append(delta_minutes)
                        remaining -= close_qty
                        if close_qty == entry_qty:
                            entries.pop(0)
                        else:
                            entries[0] = (
                                entry_qty - close_qty,
                                entry_ts,
                                entry_side,
                            )

                    if remaining > 0:
                        entries.append((remaining, ts, trade.side))

        if not holding_times:
            return 0.0
        return sum(holding_times) / len(holding_times)
