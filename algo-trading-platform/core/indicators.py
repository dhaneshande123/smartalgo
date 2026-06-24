"""
Technical Indicators.

Hybrid TA-Lib / numpy indicator engine. Uses TA-Lib's battle-tested C
implementations when available (150+ indicators); falls back to pure
numpy/Python implementations when TA-Lib is not installed.

- Trend / momentum:  RSI, MACD, ADX, Supertrend, Stochastic, CCI, Williams %R, Aroon, MFI
- Volatility:        ATR, Bollinger Bands, Keltner Channel, Donchian Channel
- Volume / price:    VWAP, OBV, AD (Chaikin), CMF
- Pattern:           Candlestick pattern detection (TA-Lib only)
- Stats:             EMA, SMA, WMA, DEMA, TEMA, KAMA

All functions take 1-D arrays (or lists) of OHLC values and return either:
- A single scalar (the latest value)
- A dict of named outputs (for multi-output indicators like MACD)
"""

from __future__ import annotations

from typing import Any

import numpy as np

try:
    import talib
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False


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
    if HAS_TALIB:
        result = talib.SMA(a, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    return float(np.mean(a[-period:]))


def ema(closes: Any, period: int = 20) -> float | None:
    """Latest EMA value."""
    a = _to_array(closes)
    if len(a) < period:
        return None
    if HAS_TALIB:
        result = talib.EMA(a, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    return float(_ema(a, period)[-1])


def wma(closes: Any, period: int = 20) -> float | None:
    """Weighted Moving Average — recent prices weighted more heavily."""
    a = _to_array(closes)
    if len(a) < period:
        return None
    if HAS_TALIB:
        result = talib.WMA(a, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    weights = np.arange(1, period + 1, dtype=np.float64)
    return float(np.dot(a[-period:], weights) / weights.sum())


def dema(closes: Any, period: int = 20) -> float | None:
    """Double Exponential Moving Average — less lag than EMA."""
    a = _to_array(closes)
    if len(a) < period:
        return None
    if HAS_TALIB:
        result = talib.DEMA(a, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    e = _ema(a, period)
    e2 = _ema(e, period)
    val = 2 * e[-1] - e2[-1]
    return float(val)


def tema(closes: Any, period: int = 20) -> float | None:
    """Triple Exponential Moving Average — even less lag than DEMA."""
    a = _to_array(closes)
    if len(a) < period:
        return None
    if HAS_TALIB:
        result = talib.TEMA(a, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    e = _ema(a, period)
    e2 = _ema(e, period)
    e3 = _ema(e2, period)
    val = 3 * e[-1] - 3 * e2[-1] + e3[-1]
    return float(val)


def kama(closes: Any, period: int = 30) -> float | None:
    """Kaufman Adaptive Moving Average — adapts speed to volatility."""
    a = _to_array(closes)
    if len(a) < period:
        return None
    if HAS_TALIB:
        result = talib.KAMA(a, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    return ema(closes, period)


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

    if HAS_TALIB:
        result = talib.RSI(a, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None

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

    if HAS_TALIB:
        m, s, h = talib.MACD(a, fastperiod=fast, slowperiod=slow, signalperiod=signal)
        if np.isnan(m[-1]):
            return None
        return {"macd": float(m[-1]), "signal": float(s[-1]), "histogram": float(h[-1])}

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

    if HAS_TALIB:
        result = talib.ATR(h, l, c, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None

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

    if HAS_TALIB:
        adx_arr = talib.ADX(h, l, c, timeperiod=period)
        plus_di_arr = talib.PLUS_DI(h, l, c, timeperiod=period)
        minus_di_arr = talib.MINUS_DI(h, l, c, timeperiod=period)
        if np.isnan(adx_arr[-1]):
            return None
        return {
            "adx": float(adx_arr[-1]),
            "+di": float(plus_di_arr[-1]),
            "-di": float(minus_di_arr[-1]),
        }

    up_move = np.diff(h)
    down_move = -np.diff(l)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

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
    adx_result = _wilder_smoothed(dx, period)

    return {
        "adx": float(adx_result[-1]),
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

    if HAS_TALIB:
        u, m, lo = talib.BBANDS(a, timeperiod=period, nbdevup=num_std, nbdevdn=num_std)
        if np.isnan(m[-1]):
            return None
        bw = (u[-1] - lo[-1]) / m[-1] if m[-1] != 0 else 0.0
        return {"mid": float(m[-1]), "upper": float(u[-1]), "lower": float(lo[-1]), "bandwidth": float(bw)}

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


# ---------------------------------------------------------------------------
# New indicators (TA-Lib powered with numpy fallbacks)
# ---------------------------------------------------------------------------

def stochastic(
    highs: Any, lows: Any, closes: Any,
    fastk_period: int = 14, slowk_period: int = 3, slowd_period: int = 3,
) -> dict[str, float] | None:
    """Stochastic Oscillator (%K, %D). Oversold < 20, overbought > 80."""
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < fastk_period + slowk_period:
        return None
    if HAS_TALIB:
        k, d = talib.STOCH(h, l, c, fastk_period=fastk_period,
                           slowk_period=slowk_period, slowd_period=slowd_period)
        if np.isnan(k[-1]):
            return None
        return {"%K": float(k[-1]), "%D": float(d[-1])}
    lowest = np.array([np.min(l[max(0, i - fastk_period + 1):i + 1]) for i in range(len(l))])
    highest = np.array([np.max(h[max(0, i - fastk_period + 1):i + 1]) for i in range(len(h))])
    denom = highest - lowest
    raw_k = np.where(denom != 0, 100.0 * (c - lowest) / denom, 50.0)
    slow_k = _ema(raw_k, slowk_period)
    slow_d = _ema(slow_k, slowd_period)
    return {"%K": float(slow_k[-1]), "%D": float(slow_d[-1])}


def cci(highs: Any, lows: Any, closes: Any, period: int = 20) -> float | None:
    """Commodity Channel Index. > +100 overbought, < -100 oversold."""
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < period:
        return None
    if HAS_TALIB:
        result = talib.CCI(h, l, c, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    tp = (h + l + c) / 3.0
    tp_sma = np.mean(tp[-period:])
    mean_dev = np.mean(np.abs(tp[-period:] - tp_sma))
    if mean_dev == 0:
        return 0.0
    return float((tp[-1] - tp_sma) / (0.015 * mean_dev))


def williams_r(highs: Any, lows: Any, closes: Any, period: int = 14) -> float | None:
    """Williams %R. Ranges -100 to 0. < -80 oversold, > -20 overbought."""
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < period:
        return None
    if HAS_TALIB:
        result = talib.WILLR(h, l, c, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    hh = np.max(h[-period:])
    ll = np.min(l[-period:])
    if hh == ll:
        return -50.0
    return float(-100.0 * (hh - c[-1]) / (hh - ll))


def mfi(highs: Any, lows: Any, closes: Any, volumes: Any, period: int = 14) -> float | None:
    """Money Flow Index — volume-weighted RSI. > 80 overbought, < 20 oversold."""
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    v = _to_array(volumes)
    if len(c) < period + 1:
        return None
    if HAS_TALIB:
        result = talib.MFI(h, l, c, v, timeperiod=period)
        return float(result[-1]) if not np.isnan(result[-1]) else None
    tp = (h + l + c) / 3.0
    mf = tp * v
    pos_mf = np.where(np.diff(tp) > 0, mf[1:], 0.0)
    neg_mf = np.where(np.diff(tp) < 0, mf[1:], 0.0)
    pos_sum = np.sum(pos_mf[-period:])
    neg_sum = np.sum(neg_mf[-period:])
    if neg_sum == 0:
        return 100.0
    return float(100.0 - 100.0 / (1.0 + pos_sum / neg_sum))


def aroon(highs: Any, lows: Any, period: int = 25) -> dict[str, float] | None:
    """Aroon Up/Down — trend identification. Returns aroon_up, aroon_down, oscillator."""
    h = _to_array(highs)
    l = _to_array(lows)
    if len(h) < period + 1:
        return None
    if HAS_TALIB:
        down, up = talib.AROON(h, l, timeperiod=period)
        if np.isnan(up[-1]):
            return None
        return {"aroon_up": float(up[-1]), "aroon_down": float(down[-1]),
                "oscillator": float(up[-1] - down[-1])}
    window_h = h[-(period + 1):]
    window_l = l[-(period + 1):]
    days_since_high = period - int(np.argmax(window_h))
    days_since_low = period - int(np.argmin(window_l))
    up = 100.0 * (period - days_since_high) / period
    down = 100.0 * (period - days_since_low) / period
    return {"aroon_up": float(up), "aroon_down": float(down), "oscillator": float(up - down)}


def keltner_channel(
    highs: Any, lows: Any, closes: Any,
    ema_period: int = 20, atr_period: int = 14, multiplier: float = 2.0,
) -> dict[str, float] | None:
    """Keltner Channel — EMA-based volatility channel."""
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < max(ema_period, atr_period + 1):
        return None
    mid_val = ema(c, ema_period)
    atr_val = atr(h, l, c, atr_period)
    if mid_val is None or atr_val is None:
        return None
    return {
        "mid": mid_val,
        "upper": mid_val + multiplier * atr_val,
        "lower": mid_val - multiplier * atr_val,
    }


def donchian_channel(highs: Any, lows: Any, period: int = 20) -> dict[str, float] | None:
    """Donchian Channel — highest high / lowest low over period."""
    h = _to_array(highs)
    l = _to_array(lows)
    if len(h) < period:
        return None
    upper = float(np.max(h[-period:]))
    lower = float(np.min(l[-period:]))
    return {"upper": upper, "lower": lower, "mid": (upper + lower) / 2.0}


def ad_line(highs: Any, lows: Any, closes: Any, volumes: Any) -> float | None:
    """Chaikin Accumulation/Distribution Line."""
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    v = _to_array(volumes)
    if len(c) < 2:
        return None
    if HAS_TALIB:
        result = talib.AD(h, l, c, v)
        return float(result[-1])
    hl_range = h - l
    clv = np.where(hl_range != 0, ((c - l) - (h - c)) / hl_range, 0.0)
    return float(np.sum(clv * v))


def cmf(highs: Any, lows: Any, closes: Any, volumes: Any, period: int = 20) -> float | None:
    """Chaikin Money Flow — accumulation vs distribution pressure."""
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    v = _to_array(volumes)
    if len(c) < period:
        return None
    hl_range = h[-period:] - l[-period:]
    clv = np.where(hl_range != 0,
                   ((c[-period:] - l[-period:]) - (h[-period:] - c[-period:])) / hl_range, 0.0)
    vol_sum = np.sum(v[-period:])
    if vol_sum == 0:
        return 0.0
    return float(np.sum(clv * v[-period:]) / vol_sum)


def ichimoku(
    highs: Any, lows: Any, closes: Any,
    tenkan: int = 9, kijun: int = 26, senkou_b: int = 52,
) -> dict[str, float] | None:
    """Ichimoku Cloud — returns tenkan_sen, kijun_sen, senkou_a, senkou_b, chikou."""
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < senkou_b:
        return None
    tenkan_val = (np.max(h[-tenkan:]) + np.min(l[-tenkan:])) / 2.0
    kijun_val = (np.max(h[-kijun:]) + np.min(l[-kijun:])) / 2.0
    senkou_a_val = (tenkan_val + kijun_val) / 2.0
    senkou_b_val = (np.max(h[-senkou_b:]) + np.min(l[-senkou_b:])) / 2.0
    chikou_val = float(c[-1])
    return {
        "tenkan_sen": float(tenkan_val),
        "kijun_sen": float(kijun_val),
        "senkou_a": float(senkou_a_val),
        "senkou_b": float(senkou_b_val),
        "chikou_span": chikou_val,
    }


def pivot_points(high: float, low: float, close: float) -> dict[str, float]:
    """Standard pivot points from previous day's HLC. Returns PP, R1-R3, S1-S3."""
    pp = (high + low + close) / 3.0
    return {
        "PP": pp,
        "R1": 2 * pp - low, "S1": 2 * pp - high,
        "R2": pp + (high - low), "S2": pp - (high - low),
        "R3": high + 2 * (pp - low), "S3": low - 2 * (high - pp),
    }


# ---------------------------------------------------------------------------
# TA-Lib candlestick pattern detection
# ---------------------------------------------------------------------------

def detect_candlestick_patterns(
    opens: Any, highs: Any, lows: Any, closes: Any,
) -> list[dict[str, Any]]:
    """Detect candlestick patterns using TA-Lib. Returns list of detected patterns."""
    if not HAS_TALIB:
        return []
    o = _to_array(opens)
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    if len(c) < 5:
        return []

    patterns = [
        ("DOJI", talib.CDLDOJI), ("HAMMER", talib.CDLHAMMER),
        ("ENGULFING", talib.CDLENGULFING), ("MORNINGSTAR", talib.CDLMORNINGSTAR),
        ("EVENINGSTAR", talib.CDLEVENINGSTAR), ("HARAMI", talib.CDLHARAMI),
        ("SHOOTINGSTAR", talib.CDLSHOOTINGSTAR), ("SPINNINGTOP", talib.CDLSPINNINGTOP),
        ("MARUBOZU", talib.CDLMARUBOZU), ("DRAGONFLYDOJI", talib.CDLDRAGONFLYDOJI),
        ("GRAVESTONEDOJI", talib.CDLGRAVESTONEDOJI), ("HANGINGMAN", talib.CDLHANGINGMAN),
        ("INVERTEDHAMMER", talib.CDLINVERTEDHAMMER), ("PIERCING", talib.CDLPIERCING),
        ("DARKCLOUDCOVER", talib.CDLDARKCLOUDCOVER),
        ("THREEWHITESOLDIERS", talib.CDL3WHITESOLDIERS),
        ("THREEBLACKCROWS", talib.CDL3BLACKCROWS),
    ]

    detected = []
    for name, func in patterns:
        result = func(o, h, l, c)
        val = int(result[-1])
        if val != 0:
            detected.append({
                "pattern": name,
                "signal": "BULLISH" if val > 0 else "BEARISH",
                "strength": abs(val),
            })
    return detected


# ---------------------------------------------------------------------------
# Batch indicator computation (for backtesting / vectorbt)
# ---------------------------------------------------------------------------

def compute_all_series(
    opens: Any, highs: Any, lows: Any, closes: Any, volumes: Any,
) -> dict[str, np.ndarray]:
    """Compute full indicator series (not just latest) for vectorized backtesting.
    Returns dict of numpy arrays, each the same length as input."""
    o = _to_array(opens)
    h = _to_array(highs)
    l = _to_array(lows)
    c = _to_array(closes)
    v = _to_array(volumes)
    n = len(c)
    result: dict[str, np.ndarray] = {}

    if HAS_TALIB:
        result["rsi_14"] = talib.RSI(c, timeperiod=14)
        result["ema_9"] = talib.EMA(c, timeperiod=9)
        result["ema_21"] = talib.EMA(c, timeperiod=21)
        result["sma_50"] = talib.SMA(c, timeperiod=50)
        result["sma_200"] = talib.SMA(c, timeperiod=200)
        result["atr_14"] = talib.ATR(h, l, c, timeperiod=14)
        result["adx_14"] = talib.ADX(h, l, c, timeperiod=14)
        result["plus_di"] = talib.PLUS_DI(h, l, c, timeperiod=14)
        result["minus_di"] = talib.MINUS_DI(h, l, c, timeperiod=14)
        result["cci_20"] = talib.CCI(h, l, c, timeperiod=20)
        result["willr_14"] = talib.WILLR(h, l, c, timeperiod=14)
        result["mfi_14"] = talib.MFI(h, l, c, v, timeperiod=14)
        m, s, hist = talib.MACD(c)
        result["macd"] = m
        result["macd_signal"] = s
        result["macd_hist"] = hist
        bb_u, bb_m, bb_l = talib.BBANDS(c, timeperiod=20)
        result["bb_upper"] = bb_u
        result["bb_mid"] = bb_m
        result["bb_lower"] = bb_l
        result["obv"] = talib.OBV(c, v)
        result["ad"] = talib.AD(h, l, c, v)
        stoch_k, stoch_d = talib.STOCH(h, l, c)
        result["stoch_k"] = stoch_k
        result["stoch_d"] = stoch_d
    else:
        result["rsi_14"] = np.full(n, np.nan)
        result["ema_9"] = _ema(c, 9) if n >= 9 else np.full(n, np.nan)
        result["ema_21"] = _ema(c, 21) if n >= 21 else np.full(n, np.nan)
        result["atr_14"] = np.full(n, np.nan)
        result["adx_14"] = np.full(n, np.nan)
        if n >= 15:
            deltas = np.diff(c)
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)
            avg_g = _wilder_smoothed(gains, 14)
            avg_l = _wilder_smoothed(losses, 14)
            rs = np.where(avg_l != 0, avg_g / avg_l, 100.0)
            rsi_vals = 100.0 - 100.0 / (1.0 + rs)
            result["rsi_14"] = np.concatenate([[np.nan], rsi_vals])

    return result
