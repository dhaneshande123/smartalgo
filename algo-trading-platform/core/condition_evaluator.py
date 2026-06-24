"""
Condition Evaluator — turns strategy ``entry_conditions`` specs into bool().

A condition is a dict like::

    {"indicator": "RSI", "period": 14, "operator": "<", "value": 30}
    {"indicator": "ADX", "period": 14, "operator": ">", "value": 25}
    {"indicator": "IV_RANK", "operator": ">", "value": 70}
    {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"}
    {"indicator": "VWAP_CROSS", "operator": ">", "value": 0}
    {"indicator": "SUPERTREND_DIR", "operator": "==", "value": "UP"}

A strategy's ``entry_conditions`` is a list of these, combined with
``entry_trigger``: ``"ALL"`` (AND) or ``"ANY"`` (OR).

The evaluator is **stateless across strategies**: each call to
:py:func:`evaluate` fetches the indicator values for the given symbol once,
caches them in a per-call dict, and applies the operator. So evaluating 5
conditions on the same symbol only does one candles fetch.

For now the evaluator is **async** and uses the live Fyers feed; in
backtests we'd swap in a historical-candle provider.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Awaitable

from core import indicators
from core.market_regime import classify_regime, get_iv_tracker

logger = logging.getLogger(__name__)


OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    ">":  lambda a, b: float(a) > float(b),
    "<":  lambda a, b: float(a) < float(b),
    ">=": lambda a, b: float(a) >= float(b),
    "<=": lambda a, b: float(a) <= float(b),
    "==": lambda a, b: str(a).strip().upper() == str(b).strip().upper() if isinstance(a, str) or isinstance(b, str) else float(a) == float(b),
    "!=": lambda a, b: str(a).strip().upper() != str(b).strip().upper() if isinstance(a, str) or isinstance(b, str) else float(a) != float(b),
}


# Indicators the user can reference in entry_conditions
SUPPORTED_INDICATORS = {
    "RSI": {"params": ["period"], "default_period": 14, "type": "number"},
    "MACD": {"params": ["fast", "slow", "signal"], "type": "number", "field": "histogram"},
    "MACD_HISTOGRAM": {"params": ["fast", "slow", "signal"], "type": "number"},
    "ATR": {"params": ["period"], "default_period": 14, "type": "number"},
    "ADX": {"params": ["period"], "default_period": 14, "type": "number"},
    "BB_UPPER": {"params": ["period", "std"], "type": "number"},
    "BB_LOWER": {"params": ["period", "std"], "type": "number"},
    "BB_BANDWIDTH": {"params": ["period", "std"], "type": "number"},
    "BB_WIDTH": {"params": ["period", "std"], "type": "number", "desc": "Alias for BB_BANDWIDTH"},
    "BB_POSITION": {"params": ["period", "std"], "type": "number", "desc": "%B: 0=lower band, 1=upper band"},
    "IS_EXPIRY_DAY": {"params": [], "type": "bool", "desc": "True if today is the nearest weekly expiry"},
    "VWAP": {"params": [], "type": "number"},
    "VWAP_CROSS": {"params": [], "type": "number", "desc": "Price - VWAP (positive=above)"},
    "SUPERTREND_DIR": {"params": ["period", "multiplier"], "type": "string", "values": ["UP", "DOWN"]},
    "EMA": {"params": ["period"], "default_period": 20, "type": "number"},
    "SMA": {"params": ["period"], "default_period": 20, "type": "number"},
    "PRICE_VS_SMA": {"params": ["period"], "type": "number", "desc": "Close - SMA (positive=above)"},
    "PRICE_VS_EMA": {"params": ["period"], "type": "number", "desc": "Close - EMA"},
    "IV_RANK": {"params": [], "type": "number", "desc": "0-100, requires IV history"},
    "IV_PERCENTILE": {"params": [], "type": "number"},
    "REGIME": {"params": [], "type": "string", "values": ["HIGH_VOL", "LOW_VOL", "TRENDING_UP", "TRENDING_DOWN", "RANGE_BOUND", "UNKNOWN"]},
    "VIX": {"params": [], "type": "number"},
}


class IndicatorContext:
    """Per-evaluation cache: fetches candles once, computes indicators on demand.

    Lives only for the duration of one ``evaluate(...)`` call.
    """

    def __init__(
        self,
        symbol: str,
        live_feed,
        timeframe: str = "M5",
        candle_count: int = 200,
    ) -> None:
        self.symbol = symbol.upper()
        self.live_feed = live_feed
        self.timeframe = timeframe
        self.candle_count = candle_count
        self._candles: list[dict] | None = None
        self._arrays: dict | None = None
        self._vix: float | None = None
        self._spot: float | None = None
        self._indicator_cache: dict[str, Any] = {}

    async def candles(self) -> list[dict]:
        if self._candles is None:
            try:
                if self.live_feed and self.live_feed.is_connected:
                    self._candles = await self.live_feed.get_candles(
                        self.symbol, self.timeframe, self.candle_count
                    )
                else:
                    self._candles = []
            except Exception as e:
                logger.warning(f"Candle fetch failed for {self.symbol}: {e}")
                self._candles = []
        return self._candles or []

    async def arrays(self) -> dict:
        if self._arrays is None:
            candles = await self.candles()
            self._arrays = indicators.split_ohlcv(candles)
        return self._arrays

    def vix(self) -> float | None:
        if self._vix is None and self.live_feed:
            tick = self.live_feed.get_cached_tick("INDIA VIX")
            if tick:
                self._vix = float(tick.get("ltp", 0)) or None
        return self._vix

    def spot(self) -> float | None:
        if self._spot is None and self.live_feed:
            tick = self.live_feed.get_cached_tick(self.symbol)
            if tick:
                self._spot = float(tick.get("ltp", 0)) or None
        return self._spot

    async def value(self, indicator: str, params: dict) -> Any:
        """Compute (or return cached) value for a named indicator + params."""
        cache_key = f"{indicator}:{sorted(params.items())}"
        if cache_key in self._indicator_cache:
            return self._indicator_cache[cache_key]

        result = await self._compute(indicator, params)
        self._indicator_cache[cache_key] = result
        return result

    async def _compute(self, indicator: str, params: dict) -> Any:
        ind = indicator.upper()

        if ind == "IV_RANK":
            return get_iv_tracker().iv_rank(self.symbol)
        if ind == "IV_PERCENTILE":
            return get_iv_tracker().iv_percentile(self.symbol)
        if ind == "VIX":
            return self.vix()
        if ind == "IS_EXPIRY_DAY":
            # Read the nearest expiry from the option-chain cache (late import
            # to avoid a circular dependency with core.api at module load).
            try:
                from core import api as _api
                from core.scalper_engine import is_expiry_day
                chain = (_api._fyers_chain_cache.get(self.symbol)
                         or _api._fyers_chain_cache.get(f"{self.symbol}:"))
                return is_expiry_day(chain or {}, self.symbol)
            except Exception as e:
                logger.debug(f"IS_EXPIRY_DAY check failed: {e}")
                return None
        if ind == "REGIME":
            candles = await self.candles()
            reg = classify_regime(
                symbol=self.symbol,
                candles=candles,
                vix=self.vix(),
                spot=self.spot(),
            )
            return reg["regime"]

        arr = await self.arrays()
        if len(arr["Close"]) == 0:
            return None

        period = int(params.get("period") or 14)

        if ind == "RSI":
            return indicators.rsi(arr["Close"], period)
        if ind == "ATR":
            return indicators.atr(arr["High"], arr["Low"], arr["Close"], period)
        if ind == "ADX":
            res = indicators.adx(arr["High"], arr["Low"], arr["Close"], period)
            return res["adx"] if res else None
        if ind == "EMA":
            return indicators.ema(arr["Close"], period)
        if ind == "SMA":
            return indicators.sma(arr["Close"], period)
        if ind == "PRICE_VS_SMA":
            sma_v = indicators.sma(arr["Close"], period)
            if sma_v is None:
                return None
            return float(arr["Close"][-1]) - sma_v
        if ind == "PRICE_VS_EMA":
            ema_v = indicators.ema(arr["Close"], period)
            if ema_v is None:
                return None
            return float(arr["Close"][-1]) - ema_v
        if ind == "VWAP":
            return indicators.vwap(arr["High"], arr["Low"], arr["Close"], arr["Volume"])
        if ind == "VWAP_CROSS":
            v = indicators.vwap(arr["High"], arr["Low"], arr["Close"], arr["Volume"])
            if v is None:
                return None
            return float(arr["Close"][-1]) - v
        if ind in ("MACD", "MACD_HISTOGRAM"):
            res = indicators.macd(
                arr["Close"],
                int(params.get("fast", 12)),
                int(params.get("slow", 26)),
                int(params.get("signal", 9)),
            )
            return res["histogram"] if res else None
        if ind in ("BB_UPPER", "BB_LOWER", "BB_BANDWIDTH", "BB_WIDTH", "BB_POSITION"):
            res = indicators.bollinger_bands(
                arr["Close"],
                int(params.get("period", 20)),
                float(params.get("std", 2.0)),
            )
            if not res:
                return None
            if ind == "BB_UPPER":
                return res["upper"]
            if ind == "BB_LOWER":
                return res["lower"]
            if ind in ("BB_BANDWIDTH", "BB_WIDTH"):
                return res["bandwidth"]
            # BB_POSITION = %B = (close - lower) / (upper - lower), clamped 0..1-ish
            upper, lower = res["upper"], res["lower"]
            if upper == lower:
                return 0.5
            return (float(arr["Close"][-1]) - lower) / (upper - lower)
        if ind == "SUPERTREND_DIR":
            res = indicators.supertrend(
                arr["High"], arr["Low"], arr["Close"],
                int(params.get("period", 10)),
                float(params.get("multiplier", 3.0)),
            )
            return res["direction"] if res else None

        logger.warning(f"Unknown indicator: {indicator}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def evaluate_conditions(
    *,
    symbol: str,
    conditions: list[dict],
    trigger: str = "ALL",
    live_feed,
    timeframe: str = "M5",
) -> dict[str, Any]:
    """Evaluate a list of entry conditions against current market data.

    Args:
        symbol: underlying symbol (e.g. NIFTY).
        conditions: list of dicts, each with keys
            ``indicator``, ``operator``, ``value``, and optional ``period``, etc.
        trigger: ``"ALL"`` (AND) or ``"ANY"`` (OR).
        live_feed: a FyersLiveFeed instance.
        timeframe: candle timeframe (M1 / M5 / M15 / M30 / H1 / D1).

    Returns:
        dict with:
            ``passed``      — overall True/False
            ``trigger``     — "ALL" / "ANY"
            ``results``     — per-condition list of {condition, value, passed}
            ``summary``     — human-readable explanation
    """
    if not conditions:
        return {
            "passed": True,
            "trigger": trigger,
            "results": [],
            "summary": "No conditions defined — pass by default",
        }

    ctx = IndicatorContext(symbol=symbol, live_feed=live_feed, timeframe=timeframe)
    results = []
    for cond in conditions:
        result = await _evaluate_one(ctx, cond)
        results.append(result)

    passes = [r["passed"] for r in results]
    if trigger.upper() == "ANY":
        overall = any(passes)
    else:
        overall = all(passes)

    summary = _build_summary(results, trigger.upper(), overall)
    return {
        "passed": overall,
        "trigger": trigger.upper(),
        "results": results,
        "summary": summary,
    }


async def _evaluate_one(ctx: IndicatorContext, cond: dict) -> dict:
    """Evaluate a single condition dict."""
    indicator = cond.get("indicator", "").upper()
    operator = cond.get("operator", ">")
    target = cond.get("value")

    # Extract optional parameters
    params = {}
    for key in ("period", "fast", "slow", "signal", "std", "multiplier"):
        if key in cond:
            params[key] = cond[key]

    try:
        actual = await ctx.value(indicator, params)
    except Exception as e:
        logger.warning(f"Indicator {indicator} compute failed: {e}")
        actual = None

    if actual is None or operator not in OPERATORS:
        return {
            "condition": cond,
            "actual": actual,
            "passed": False,
            "reason": "indicator unavailable" if actual is None else f"unknown operator: {operator}",
        }

    try:
        passed = OPERATORS[operator](actual, target)
    except (ValueError, TypeError) as e:
        return {"condition": cond, "actual": actual, "passed": False, "reason": str(e)}

    return {"condition": cond, "actual": actual, "passed": bool(passed)}


def _build_summary(results: list[dict], trigger: str, overall: bool) -> str:
    parts = []
    for r in results:
        c = r["condition"]
        ind = c.get("indicator", "?")
        op = c.get("operator", "?")
        target = c.get("value", "?")
        actual = r.get("actual")
        passed = r.get("passed")
        mark = "✓" if passed else "✗"
        actual_str = f"{actual:.2f}" if isinstance(actual, (int, float)) else str(actual)
        parts.append(f"{mark} {ind} {op} {target} (actual={actual_str})")
    joiner = " AND " if trigger == "ALL" else " OR "
    verdict = "ENTRY OK" if overall else "WAIT"
    return f"[{verdict}] {joiner.join(parts)}"


def supported_indicators_spec() -> dict:
    """Return metadata for all supported indicators (used by the UI)."""
    return SUPPORTED_INDICATORS
