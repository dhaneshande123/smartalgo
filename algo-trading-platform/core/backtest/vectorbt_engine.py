"""
VectorBT-powered backtesting engine.

Vectorized backtesting using vectorbt for 100x faster parameter sweeps
compared to the event-driven engine. Integrates with:
- SQLite candle cache (state_store)
- TA-Lib indicators (core.indicators)
- Indian F&O charges model (core.charges)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

try:
    import vectorbt as vbt
    HAS_VBT = True
except ImportError:
    HAS_VBT = False

try:
    import talib
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False

from core.charges import (
    BROKERAGE_PER_ORDER,
    STT_OPTIONS_SELL_PCT,
    EXCHANGE_TXN_OPTIONS_PCT,
    GST_PCT,
    SEBI_CHARGES_PER_CRORE,
    STAMP_DUTY_OPTIONS_PCT,
)

logger = logging.getLogger(__name__)

NIFTY_LOT_SIZE = 75
BANKNIFTY_LOT_SIZE = 30

# ---------------------------------------------------------------------------
# Built-in signal strategies for vectorbt
# ---------------------------------------------------------------------------

def _rsi_signals(close: pd.Series, params: dict) -> tuple[pd.Series, pd.Series]:
    period = params.get("rsi_period", 14)
    oversold = params.get("rsi_oversold", 30)
    overbought = params.get("rsi_overbought", 70)
    if HAS_TALIB:
        rsi_vals = pd.Series(talib.RSI(close.values, timeperiod=period), index=close.index)
    else:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_vals = 100.0 - 100.0 / (1.0 + rs)
    entries = rsi_vals < oversold
    exits = rsi_vals > overbought
    return entries.fillna(False), exits.fillna(False)


def _macd_signals(close: pd.Series, params: dict) -> tuple[pd.Series, pd.Series]:
    fast = params.get("macd_fast", 12)
    slow = params.get("macd_slow", 26)
    signal = params.get("macd_signal", 9)
    if HAS_TALIB:
        m, s, h = talib.MACD(close.values, fastperiod=fast, slowperiod=slow, signalperiod=signal)
        macd_line = pd.Series(m, index=close.index)
        signal_line = pd.Series(s, index=close.index)
    else:
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    entries = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    exits = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))
    return entries.fillna(False), exits.fillna(False)


def _bollinger_signals(close: pd.Series, params: dict) -> tuple[pd.Series, pd.Series]:
    period = params.get("bb_period", 20)
    std_dev = params.get("bb_std", 2.0)
    if HAS_TALIB:
        upper, mid, lower = talib.BBANDS(close.values, timeperiod=period,
                                          nbdevup=std_dev, nbdevdn=std_dev)
        upper = pd.Series(upper, index=close.index)
        lower = pd.Series(lower, index=close.index)
    else:
        mid = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std
    entries = close < lower
    exits = close > upper
    return entries.fillna(False), exits.fillna(False)


def _supertrend_signals(close: pd.Series, high: pd.Series, low: pd.Series,
                         params: dict) -> tuple[pd.Series, pd.Series]:
    period = params.get("st_period", 10)
    multiplier = params.get("st_multiplier", 3.0)
    if HAS_TALIB:
        atr_vals = pd.Series(talib.ATR(high.values, low.values, close.values,
                                        timeperiod=period), index=close.index)
    else:
        prev_close = close.shift(1).fillna(close.iloc[0])
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr_vals = tr.rolling(period).mean()

    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr_vals
    lower_band = hl2 - multiplier * atr_vals

    direction = pd.Series(1, index=close.index)
    for i in range(1, len(close)):
        if close.iloc[i] > upper_band.iloc[i - 1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < lower_band.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

    entries = (direction == 1) & (direction.shift(1) == -1)
    exits = (direction == -1) & (direction.shift(1) == 1)
    return entries.fillna(False), exits.fillna(False)


def _ema_crossover_signals(close: pd.Series, params: dict) -> tuple[pd.Series, pd.Series]:
    fast = params.get("ema_fast", 9)
    slow = params.get("ema_slow", 21)
    if HAS_TALIB:
        ema_f = pd.Series(talib.EMA(close.values, timeperiod=fast), index=close.index)
        ema_s = pd.Series(talib.EMA(close.values, timeperiod=slow), index=close.index)
    else:
        ema_f = close.ewm(span=fast, adjust=False).mean()
        ema_s = close.ewm(span=slow, adjust=False).mean()
    entries = (ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))
    exits = (ema_f < ema_s) & (ema_f.shift(1) >= ema_s.shift(1))
    return entries.fillna(False), exits.fillna(False)


STRATEGY_REGISTRY: dict[str, Any] = {
    "rsi_reversal": _rsi_signals,
    "macd_crossover": _macd_signals,
    "bollinger_breakout": _bollinger_signals,
    "supertrend": _supertrend_signals,
    "ema_crossover": _ema_crossover_signals,
}


# ---------------------------------------------------------------------------
# Config & Result
# ---------------------------------------------------------------------------

@dataclass
class VBTBacktestConfig:
    strategy: str = "rsi_reversal"
    symbol: str = "NIFTY"
    resolution: str = "5"
    start_date: str | None = None
    end_date: str | None = None
    initial_capital: float = 10_000_000
    lot_size: int = 75
    params: dict = field(default_factory=dict)
    instrument_type: str = "options"
    sl_pct: float | None = None
    tp_pct: float | None = None


@dataclass
class VBTBacktestResult:
    total_return_pct: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    avg_trade_pnl: float = 0.0
    total_charges: float = 0.0
    net_return_pct: float = 0.0
    calmar_ratio: float = 0.0
    avg_holding_bars: float = 0.0
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    monthly_returns: dict[str, float] = field(default_factory=dict)
    params_used: dict = field(default_factory=dict)
    strategy: str = ""
    candles_count: int = 0

    def to_dict(self) -> dict:
        return {
            "total_return_pct": round(self.total_return_pct, 2),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "profit_factor": round(self.profit_factor, 3),
            "avg_trade_pnl": round(self.avg_trade_pnl, 2),
            "total_charges": round(self.total_charges, 2),
            "net_return_pct": round(self.net_return_pct, 2),
            "calmar_ratio": round(self.calmar_ratio, 3),
            "avg_holding_bars": round(self.avg_holding_bars, 1),
            "equity_curve": self.equity_curve,
            "monthly_returns": {k: round(v, 2) for k, v in self.monthly_returns.items()},
            "params_used": self.params_used,
            "strategy": self.strategy,
            "candles_count": self.candles_count,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class VectorBTEngine:
    """Vectorized backtesting engine using vectorbt."""

    def __init__(self, state_store=None):
        self._store = state_store

    def _load_candles_from_cache(self, config: VBTBacktestConfig) -> pd.DataFrame:
        if self._store is None:
            raise ValueError("No state_store provided — cannot load candle data")

        from_ts = None
        to_ts = None
        if config.start_date:
            from_ts = int(datetime.strptime(config.start_date, "%Y-%m-%d").timestamp())
        if config.end_date:
            to_ts = int(datetime.strptime(config.end_date, "%Y-%m-%d").timestamp()) + 86400

        fyers_symbol = f"NSE:{config.symbol}50-INDEX" if config.symbol in ("NIFTY", "BANKNIFTY") else config.symbol
        candles = self._store.get_candles(
            fyers_symbol, config.resolution, from_ts=from_ts, to_ts=to_ts, limit=100_000
        )
        if not candles:
            candles = self._store.get_candles(
                config.symbol, config.resolution, from_ts=from_ts, to_ts=to_ts, limit=100_000
            )
        if not candles:
            raise ValueError(
                f"No cached candles for {config.symbol} ({config.resolution}). "
                f"Use POST /api/backtest/fetch-history first."
            )

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df.set_index("datetime", inplace=True)
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                 "close": "Close", "volume": "Volume"})
        df = df[["Open", "High", "Low", "Close", "Volume"]].sort_index()
        df = df[~df.index.duplicated(keep="first")]
        return df

    def _estimate_charges(self, num_trades: int, avg_premium: float,
                          lot_size: int, instrument_type: str) -> float:
        if num_trades == 0:
            return 0.0
        turnover = num_trades * avg_premium * lot_size
        brokerage = num_trades * BROKERAGE_PER_ORDER
        stt = turnover * STT_OPTIONS_SELL_PCT * 0.5 if instrument_type == "options" else 0
        exchange_fee = turnover * EXCHANGE_TXN_OPTIONS_PCT
        gst = (brokerage + exchange_fee) * GST_PCT
        sebi = turnover / 1e7 * SEBI_CHARGES_PER_CRORE
        stamp = turnover * STAMP_DUTY_OPTIONS_PCT * 0.5
        return brokerage + stt + exchange_fee + gst + sebi + stamp

    def run(self, config: VBTBacktestConfig) -> VBTBacktestResult:
        if not HAS_VBT:
            raise RuntimeError("vectorbt is not installed")

        df = self._load_candles_from_cache(config)
        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        strategy_fn = STRATEGY_REGISTRY.get(config.strategy)
        if strategy_fn is None:
            raise ValueError(f"Unknown strategy: {config.strategy}. Available: {list(STRATEGY_REGISTRY.keys())}")

        if config.strategy == "supertrend":
            entries, exits = strategy_fn(close, high, low, config.params)
        else:
            entries, exits = strategy_fn(close, config.params)

        pf_kwargs = {
            "close": close,
            "entries": entries,
            "exits": exits,
            "init_cash": config.initial_capital,
            "size": config.lot_size,
            "fees": BROKERAGE_PER_ORDER / (close.mean() * config.lot_size) if close.mean() > 0 else 0,
            "freq": "5min" if config.resolution in ("1", "5", "15", "30") else "1D",
        }

        if config.sl_pct is not None:
            pf_kwargs["sl_stop"] = config.sl_pct / 100.0
        if config.tp_pct is not None:
            pf_kwargs["tp_stop"] = config.tp_pct / 100.0

        pf = vbt.Portfolio.from_signals(**pf_kwargs)

        stats = pf.stats()
        total_trades = int(stats.get("Total Trades", 0))
        win_rate_val = float(stats.get("Win Rate [%]", 0))
        total_return = float(stats.get("Total Return [%]", 0))
        max_dd = float(stats.get("Max Drawdown [%]", 0))
        sharpe = float(stats.get("Sharpe Ratio", 0))
        sortino = float(stats.get("Sortino Ratio", 0))
        calmar = float(stats.get("Calmar Ratio", 0))

        trades_obj = pf.trades
        if total_trades > 0:
            pnl_arr = trades_obj.pnl.values
            winners = pnl_arr[pnl_arr > 0]
            losers = pnl_arr[pnl_arr < 0]
            profit_factor = float(np.sum(winners) / abs(np.sum(losers))) if len(losers) > 0 and np.sum(losers) != 0 else float("inf")
            avg_pnl = float(np.mean(pnl_arr))
            avg_holding = float(np.mean(trades_obj.duration.values.astype("timedelta64[m]").astype(float))) if hasattr(trades_obj, "duration") else 0.0
        else:
            profit_factor = 0.0
            avg_pnl = 0.0
            avg_holding = 0.0

        avg_premium = float(close.mean())
        total_charges = self._estimate_charges(
            total_trades * 2, avg_premium, config.lot_size, config.instrument_type
        )
        charges_pct = (total_charges / config.initial_capital) * 100 if config.initial_capital > 0 else 0
        net_return = total_return - charges_pct

        equity = pf.value()
        eq_curve = []
        step = max(1, len(equity) // 500)
        for i in range(0, len(equity), step):
            eq_curve.append((str(equity.index[i]), float(equity.iloc[i])))
        if len(equity) - 1 not in range(0, len(equity), step):
            eq_curve.append((str(equity.index[-1]), float(equity.iloc[-1])))

        monthly = {}
        if len(equity) > 1:
            monthly_ret = equity.resample("ME").last().pct_change().dropna()
            for dt, val in monthly_ret.items():
                monthly[dt.strftime("%Y-%m")] = float(val * 100)

        return VBTBacktestResult(
            total_return_pct=total_return,
            total_trades=total_trades,
            win_rate=win_rate_val,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown_pct=max_dd,
            profit_factor=profit_factor,
            avg_trade_pnl=avg_pnl,
            total_charges=total_charges,
            net_return_pct=net_return,
            calmar_ratio=calmar,
            avg_holding_bars=avg_holding,
            equity_curve=eq_curve,
            monthly_returns=monthly,
            params_used=config.params,
            strategy=config.strategy,
            candles_count=len(df),
        )

    def available_strategies(self) -> list[dict[str, str]]:
        return [
            {"id": "rsi_reversal", "name": "RSI Mean Reversion",
             "description": "Buy on RSI oversold, sell on overbought",
             "params": "rsi_period, rsi_oversold, rsi_overbought"},
            {"id": "macd_crossover", "name": "MACD Crossover",
             "description": "Buy on MACD bullish cross, sell on bearish",
             "params": "macd_fast, macd_slow, macd_signal"},
            {"id": "bollinger_breakout", "name": "Bollinger Band Reversal",
             "description": "Buy below lower band, sell above upper",
             "params": "bb_period, bb_std"},
            {"id": "supertrend", "name": "Supertrend Follower",
             "description": "Follow Supertrend direction changes",
             "params": "st_period, st_multiplier"},
            {"id": "ema_crossover", "name": "EMA Crossover",
             "description": "Buy on fast EMA crossing above slow, sell on cross below",
             "params": "ema_fast, ema_slow"},
        ]
