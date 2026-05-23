"""
Market Regime Classifier + IV analytics.

Two things live here:

1. ``IVTracker`` — keeps an in-memory rolling history of ATM straddle IV
   per symbol. Used to compute IV Rank and IV Percentile, which are the
   real-money signals for "is volatility cheap or expensive right now?".

2. ``classify_regime(symbol, ...)`` — composite classifier that returns one
   of:

      ``HIGH_VOL`` / ``LOW_VOL`` / ``TRENDING_UP`` / ``TRENDING_DOWN`` /
      ``RANGE_BOUND`` / ``UNKNOWN``

   using live VIX, ADX, IV Rank, and price vs VWAP as inputs.

This module is intentionally light — it doesn't pull data itself. Callers
pass in candles (for ADX/VWAP) and the current option chain snapshot. The
intent is: ``core.api`` wires the live Fyers feed into these functions; the
backtest engine can pass historical bars to the same code.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from core import indicators


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How many recent IV samples to keep per symbol (rolling window for IV Rank).
# At one sample every 5 minutes during market hours, 252*78 = ~52 weeks of
# market data. We cap lower for memory; reset on each restart.
_IV_HISTORY_CAP = 2000

# Regime thresholds — these are the standard "rules of thumb" from desk traders.
# Tweak per-instrument once we have backtest data.
_HIGH_VOL_VIX = 18.0
_LOW_VOL_VIX = 12.0
_HIGH_VOL_IV_RANK = 70.0
_LOW_VOL_IV_RANK = 30.0
_TRENDING_ADX = 25.0
_RANGE_ADX = 20.0


# ---------------------------------------------------------------------------
# IV Tracker
# ---------------------------------------------------------------------------

class IVTracker:
    """Rolling history of ATM IV per symbol.

    A single instance is shared across the platform (``core.api`` constructs
    one and reuses it). It is fed an IV sample every time the option chain
    is refreshed; lookups (IV rank, percentile) read from the deque.
    """

    def __init__(self, cap: int = _IV_HISTORY_CAP) -> None:
        self._cap = cap
        # symbol -> deque of (timestamp, iv)
        self._history: dict[str, deque[tuple[float, float]]] = {}

    def record(self, symbol: str, iv: float) -> None:
        """Add a new IV sample for ``symbol``."""
        sym = (symbol or "").upper()
        if not sym or iv is None or iv <= 0:
            return
        if sym not in self._history:
            self._history[sym] = deque(maxlen=self._cap)
        self._history[sym].append((time.time(), float(iv)))

    def latest(self, symbol: str) -> float | None:
        """Most recent recorded IV for ``symbol``."""
        sym = (symbol or "").upper()
        dq = self._history.get(sym)
        if not dq:
            return None
        return dq[-1][1]

    def history(self, symbol: str) -> list[float]:
        """Return all recorded IV values (oldest first)."""
        sym = (symbol or "").upper()
        dq = self._history.get(sym)
        if not dq:
            return []
        return [iv for _, iv in dq]

    def iv_rank(self, symbol: str) -> float | None:
        """IV Rank in [0, 100]: where today's IV sits between the 52w low and high.

        Returns ``None`` if we have fewer than 20 samples — not enough to be
        meaningful. After that returns a 0-100 score where 0 = at low, 100 = at high.
        """
        h = self.history(symbol)
        if len(h) < 20:
            return None
        current = h[-1]
        lo = min(h)
        hi = max(h)
        if hi - lo < 1e-9:
            return 50.0
        return float((current - lo) / (hi - lo) * 100.0)

    def iv_percentile(self, symbol: str) -> float | None:
        """IV Percentile in [0, 100]: % of historical samples below today's IV."""
        h = self.history(symbol)
        if len(h) < 20:
            return None
        current = h[-1]
        below = sum(1 for v in h[:-1] if v < current)
        return float(below / max(1, len(h) - 1) * 100.0)

    def sample_count(self, symbol: str) -> int:
        sym = (symbol or "").upper()
        return len(self._history.get(sym, []))


# Module-level singleton (used by core.api). The tests can construct their own.
_global_iv_tracker = IVTracker()


def get_iv_tracker() -> IVTracker:
    """Return the global IVTracker instance."""
    return _global_iv_tracker


def record_iv_from_option_chain(chain_data: dict[str, Any]) -> None:
    """Extract the implied volatility proxy from a /api/market/option-chain
    response and record it.

    Sources (in priority order):
        1. ``india_vix`` field on the response — this is what Fyers returns
           for index option chains and equals NIFTY ATM IV by construction.
        2. Average of ATM ``call_iv`` and ``put_iv`` from the chain rows if
           Fyers ever populates them (currently they don't, but we keep the
           fallback for future-proofing).

    A single sample per symbol per option-chain refresh is recorded.
    """
    if not isinstance(chain_data, dict):
        return
    symbol = chain_data.get("symbol")
    if not symbol:
        return

    iv_value: float | None = None

    # Source 1: india_vix (preferred — already an IV % from Fyers)
    vix = chain_data.get("india_vix")
    if vix is not None:
        try:
            v = float(vix)
            if v > 0:
                iv_value = v
        except (TypeError, ValueError):
            pass

    # Source 2: per-leg IV in the chain rows (fallback)
    if iv_value is None:
        atm = chain_data.get("atm_strike")
        rows = chain_data.get("chain", []) or []
        if atm and rows:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("strike") == atm:
                    call_iv = float(row.get("call_iv", 0) or 0)
                    put_iv = float(row.get("put_iv", 0) or 0)
                    if call_iv > 0 and put_iv > 0:
                        iv_value = (call_iv + put_iv) / 2.0
                    break

    if iv_value is not None and iv_value > 0:
        _global_iv_tracker.record(symbol, iv_value)


# ---------------------------------------------------------------------------
# Regime classifier
# ---------------------------------------------------------------------------

def classify_regime(
    *,
    symbol: str,
    candles: list[dict[str, Any]] | None = None,
    vix: float | None = None,
    spot: float | None = None,
) -> dict[str, Any]:
    """Classify the current market regime for ``symbol``.

    Args:
        symbol: NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY.
        candles: optional list of OHLCV dicts (15-min or 1h candles work best).
            If omitted, ADX/VWAP signals will be skipped.
        vix: current INDIA VIX (only relevant for index symbols).
        spot: current spot price.

    Returns:
        dict with:
            ``regime``        — one of HIGH_VOL/LOW_VOL/TRENDING_UP/TRENDING_DOWN/RANGE_BOUND/UNKNOWN
            ``confidence``    — 0..100 estimate
            ``signals``       — dict of individual signal values (vix, adx, iv_rank, etc.)
            ``recommendation`` — short text suggesting strategies that fit this regime
    """
    signals: dict[str, Any] = {}

    # ── 1. Volatility from VIX + IV Rank
    iv_rank = _global_iv_tracker.iv_rank(symbol)
    signals["vix"] = vix
    signals["iv_rank"] = iv_rank

    vol_score = 0  # +ve = high vol, -ve = low vol
    if vix is not None:
        if vix >= _HIGH_VOL_VIX:
            vol_score += 2
        elif vix <= _LOW_VOL_VIX:
            vol_score -= 2
    if iv_rank is not None:
        if iv_rank >= _HIGH_VOL_IV_RANK:
            vol_score += 1
        elif iv_rank <= _LOW_VOL_IV_RANK:
            vol_score -= 1

    # ── 2. Trend from ADX + price vs VWAP
    trend_score = 0  # +ve = up, -ve = down, 0 = range
    adx_value = None
    vwap_value = None
    if candles and len(candles) >= 30:
        arr = indicators.split_ohlcv(candles)
        adx_dict = indicators.adx(arr["High"], arr["Low"], arr["Close"])
        vwap_value = indicators.vwap(arr["High"], arr["Low"], arr["Close"], arr["Volume"])

        if adx_dict:
            adx_value = adx_dict["adx"]
            signals["adx"] = adx_value
            signals["+di"] = adx_dict["+di"]
            signals["-di"] = adx_dict["-di"]
            if adx_value >= _TRENDING_ADX:
                if adx_dict["+di"] > adx_dict["-di"]:
                    trend_score = 2
                else:
                    trend_score = -2
            elif adx_value < _RANGE_ADX:
                trend_score = 0
            else:
                # 20-25 = mild trend, lean by DI cross
                trend_score = 1 if adx_dict["+di"] > adx_dict["-di"] else -1

        if vwap_value and spot:
            signals["vwap"] = vwap_value
            signals["spot_vs_vwap_pct"] = (spot - vwap_value) / vwap_value * 100.0

    # ── 3. Compose final regime
    # Volatility takes precedence — extreme vol overrides trend
    if vol_score >= 2:
        regime = "HIGH_VOL"
        recommendation = "Premium-selling strategies (Iron Condor, Short Strangle). IV is rich."
    elif vol_score <= -2:
        regime = "LOW_VOL"
        recommendation = "Long-volatility strategies (Long Straddle, Calendar Spread). IV is cheap."
    elif trend_score >= 2:
        regime = "TRENDING_UP"
        recommendation = "Momentum, Bull Call Spread, Buy Calls. Avoid mean-reversion."
    elif trend_score <= -2:
        regime = "TRENDING_DOWN"
        recommendation = "Bear Put Spread, Buy Puts, Short Call. Avoid mean-reversion."
    elif trend_score == 0:
        regime = "RANGE_BOUND"
        recommendation = "Iron Condor, Iron Fly, Short Straddle, Mean Reversion."
    else:
        regime = "UNKNOWN"
        recommendation = "Mixed signals — wait for clarity."

    # Confidence: more independent signals agreeing = higher confidence
    confidence_parts = []
    if vix is not None:
        confidence_parts.append(min(abs(vol_score) * 25, 100))
    if iv_rank is not None:
        confidence_parts.append(min(abs(vol_score) * 20, 100))
    if adx_value is not None:
        confidence_parts.append(min(abs(trend_score) * 25, 100))
    confidence = float(sum(confidence_parts) / max(1, len(confidence_parts))) if confidence_parts else 0.0

    return {
        "regime": regime,
        "confidence": round(confidence, 1),
        "signals": signals,
        "recommendation": recommendation,
    }
