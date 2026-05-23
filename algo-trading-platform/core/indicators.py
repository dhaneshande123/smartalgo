"""
Technical Indicators.

Pure numpy/Python implementations of the indicators an algo trading platform
needs to make entry/exit decisions:

- Trend / momentum:  RSI, MACD, ADX, Supertrend
- Volatility:        ATR, Bollinger Bands
- Volume / price:    VWAP, OBV
- Stats:             EMA, SMA

All functions take 1-D arrays (or lists) of OHLC values and return either:
- A single scalar (the latest value)
- A dict of named outputs (for multi-output indicators like MACD)

The implementations follow the standard Welles Wilder / J.P. Wilder formulas
where applicable. They are deliberately small and dependency-free so they can
also run inside backtests.

No external dependencies beyond numpy.
"""

from __future__ import annotations

from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_array(values: Any) -> np.ndarray:
    """Coerce a list/tuple/np.ndarray to a 1-D float64 numpy array."""
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1:
        arr = arr.flatten()
    return arr


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average (Welles Wilder smoothing variant)."""
    if len(values) == 0:
        return np.array([])
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _wilder_smoothed(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's RMA — used for RSI, ADX, ATR. Same recursion as EMA but
    alpha = 1/period instead of 2/(period+1)."""
    if len(values) == 0:
        return np.array([])
    alpha = 1.0 / period
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------

def sma(closes: Any, period: int = 20) -> float | None:
    """Simple Moving Average — returns latest value or None if not enough data."""
    a = _to_array(closes)
    if len(a) < period:
        return None
    return float(np.mean(a[-period:]))


def ema(closes: Any, period: int = 20) -> float | None:
    """Latest EMA value."""
    a = _to_array(closes)
    if len(a) < period:
        return None
    return float(_ema(a, period)[-1])


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

def rsi(closes: Any, period: int = 14) -> float | None:
    """Relative Strength Index (Wilder).

    Returns the latest RSI value in [0, 100], or None if not enough data.
    Conventional interpretation: > 70 overbought, < 30 oversold.
    """
    a = _to_array(closes)
    if len(a) < period + 1:
        return None

    deltas = np.diff(a)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = _wilder_smoothed(gains, period)[-1]
    avg_loss = _wilder_smoothed(losses, period)[-1]

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(
    closes: Any,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, float] | None:
    """Moving Average Convergence Divergence.

    Returns dict with keys ``macd``, ``signal``, ``histogram`` (all latest).
    Bullish when macd > signal (histogram > 0), bearish when macd < signal.
    """
    a = _to_array(closes)
    if len(a) < slow + signal:
        return None

    ema_fast = _ema(a, fast)
    ema_slow = _ema(a, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line

    return {
        "macd": float(macd_line[-1]),
        "signal": float(signal_line[-1]),
        "histogram": float(histogram[-1]),
    }


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def atr(highs: Any, lows: Any, closes: Any, period: int = 14) -> float | None:
    """Average True Range (Wilder).

    Returns latest ATR. Useful for sizing stops: SL = entry - 1.5 * ATR.
    """
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < period + 1:
        return None

    prev_close = np.roll(c, 1)
    prev_close[0] = c[0]
    tr = np.maximum.reduce([
        h - l,
        np.abs(h - prev_close),
        np.abs(l - prev_close),
    ])
    return float(_wilder_smoothed(tr, period)[-1])


# ---------------------------------------------------------------------------
# ADX (Wilder)
# ---------------------------------------------------------------------------

def adx(highs: Any, lows: Any, closes: Any, period: int = 14) -> dict[str, float] | None:
    """Average Directional Index — trend strength.

    Returns dict with ``adx`` (0..100), ``+di``, ``-di``.
    ADX > 25 = strong trend, ADX < 20 = weak trend / ranging.
    """
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < 2 * period:
        return None

    # Directional movements
    up_move = np.diff(h)
    down_move = -np.diff(l)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # True range (aligned to diff length)
    prev_close = c[:-1]
    tr = np.maximum.reduce([
        h[1:] - l[1:],
        np.abs(h[1:] - prev_close),
        np.abs(l[1:] - prev_close),
    ])

    atr_smoothed = _wilder_smoothed(tr, period)
    plus_di = 100.0 * _wilder_smoothed(plus_dm, period) / np.where(atr_smoothed == 0, 1, atr_smoothed)
    minus_di = 100.0 * _wilder_smoothed(minus_dm, period) / np.where(atr_smoothed == 0, 1, atr_smoothed)

    dx = 100.0 * np.abs(plus_di - minus_di) / np.where((plus_di + minus_di) == 0, 1, (plus_di + minus_di))
    adx_arr = _wilder_smoothed(dx, period)

    return {
        "adx": float(adx_arr[-1]),
        "+di": float(plus_di[-1]),
        "-di": float(minus_di[-1]),
    }


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(
    closes: Any,
    period: int = 20,
    num_std: float = 2.0,
) -> dict[str, float] | None:
    """Bollinger Bands.

    Returns dict with ``mid``, ``upper``, ``lower``, ``bandwidth`` (latest).
    Price near upper = potentially overbought; near lower = oversold.
    """
    a = _to_array(closes)
    if len(a) < period:
        return None

    window = a[-period:]
    mid = float(np.mean(window))
    std = float(np.std(window, ddof=0))
    upper = mid + num_std * std
    lower = mid - num_std * std
    bandwidth = (upper - lower) / mid if mid != 0 else 0.0

    return {
        "mid": mid,
        "upper": upper,
        "lower": lower,
        "bandwidth": float(bandwidth),
    }


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------

def vwap(
    highs: Any,
    lows: Any,
    closes: Any,
    volumes: Any,
) -> float | None:
    """Volume-Weighted Average Price (cumulative since first candle).

    Typical use: intraday, recomputed from market open. Pass intraday candles only.
    Returns latest VWAP.
    """
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    v = _to_array(volumes)
    if len(c) == 0 or len(v) == 0:
        return None

    typical = (h + l + c) / 3.0
    cum_pv = np.cumsum(typical * v)
    cum_v = np.cumsum(v)
    if cum_v[-1] == 0:
        return None
    return float(cum_pv[-1] / cum_v[-1])


# ---------------------------------------------------------------------------
# Supertrend
# ---------------------------------------------------------------------------

def supertrend(
    highs: Any,
    lows: Any,
    closes: Any,
    period: int = 10,
    multiplier: float = 3.0,
) -> dict[str, float | str] | None:
    """Supertrend indicator.

    Returns dict with ``value`` (current trail level), ``direction``
    ("UP" or "DOWN"), and ``flipped`` (True if the trend just flipped on
    the latest bar).
    """
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < period + 1:
        return None

    # ATR series
    prev_close = np.roll(c, 1)
    prev_close[0] = c[0]
    tr = np.maximum.reduce([
        h - l,
        np.abs(h - prev_close),
        np.abs(l - prev_close),
    ])
    atr_series = _wilder_smoothed(tr, period)

    hl2 = (h + l) / 2.0
    upper_band = hl2 + multiplier * atr_series
    lower_band = hl2 - multiplier * atr_series

    direction = np.ones(len(c), dtype=int)  # 1 = UP, -1 = DOWN
    trail = np.empty_like(c)
    trail[0] = lower_band[0]

    for i in range(1, len(c)):
        if c[i] > upper_band[i - 1]:
            direction[i] = 1
        elif c[i] < lower_band[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
            if direction[i] > 0 and lower_band[i] < trail[i - 1]:
                lower_band[i] = trail[i - 1]
            if direction[i] < 0 and upper_band[i] > trail[i - 1]:
                upper_band[i] = trail[i - 1]

        trail[i] = lower_band[i] if direction[i] > 0 else upper_band[i]

    flipped = direction[-1] != direction[-2] if len(direction) >= 2 else False
    return {
        "value": float(trail[-1]),
        "direction": "UP" if direction[-1] > 0 else "DOWN",
        "flipped": bool(flipped),
    }


# ---------------------------------------------------------------------------
# OBV (On-Balance Volume)
# ---------------------------------------------------------------------------

def obv(closes: Any, volumes: Any) -> float | None:
    """On-Balance Volume. Cumulative volume signed by close direction."""
    c = _to_array(closes)
    v = _to_array(volumes)
    if len(c) < 2:
        return None
    direction = np.sign(np.diff(c))
    signed_vol = direction * v[1:]
    return float(np.sum(signed_vol))


# ---------------------------------------------------------------------------
# Convenience: extract OHLCV arrays from a list of candle dicts
# ---------------------------------------------------------------------------

def split_ohlcv(candles: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    """Given Fyers-format candles (dicts with Open/High/Low/Close/Volume),
    return a dict of numpy arrays for each field."""
    if not candles:
        return {k: np.array([]) for k in ("Open", "High", "Low", "Close", "Volume")}

    # Accept either capitalised or lowercased keys
    def get(c: dict, *keys: str) -> float:
        for k in keys:
            if k in c:
                return float(c[k])
        return 0.0

    o = np.array([get(c, "Open", "open") for c in candles], dtype=np.float64)
    h = np.array([get(c, "High", "high") for c in candles], dtype=np.float64)
    l = np.array([get(c, "Low", "low") for c in candles], dtype=np.float64)
    cl = np.array([get(c, "Close", "close") for c in candles], dtype=np.float64)
    v = np.array([get(c, "Volume", "volume") for c in candles], dtype=np.float64)

    return {"Open": o, "High": h, "Low": l, "Close": cl, "Volume": v}
