"""
AI Signal Engine.

Translates live market state (regime, IV Rank, ADX, VIX) into a ranked
list of strategy recommendations. Each recommendation includes:

- ``strategy_class``     — key into ``core.strategy_fit.STRATEGY_FIT``
- ``confidence``         — 0..100 composite score
- ``signal``             — STRONG_ENTRY / ENTRY / WAIT / AVOID / NEUTRAL
- ``reasoning``          — list of human-readable reasons feeding the score
- ``regime``             — current regime context
- ``expected_return_pct`` and ``max_loss_pct``
- ``capital_req``
- ``ready_to_deploy``    — confidence >= AUTO_DEPLOY_THRESHOLD

The scoring is intentionally **rule-based and transparent** — every
contribution to the score has a named reason. Backtest-based win-rate
seeding is mixed in via the ``win_rate`` weight; once we have real paper
trading results we can swap that for empirical win rates.
"""

from __future__ import annotations

import logging
from typing import Any

from core import strategy_fit
from core.market_regime import classify_regime, get_iv_tracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal labels
# ---------------------------------------------------------------------------

def _label_for(confidence: float) -> str:
    if confidence >= 80:
        return "STRONG_ENTRY"
    if confidence >= 65:
        return "ENTRY"
    if confidence >= 50:
        return "WAIT"
    if confidence >= 35:
        return "NEUTRAL"
    return "AVOID"


def _label_color(label: str) -> str:
    return {
        "STRONG_ENTRY": "green",
        "ENTRY":        "blue",
        "WAIT":         "yellow",
        "NEUTRAL":      "gray",
        "AVOID":        "red",
    }.get(label, "gray")


# ---------------------------------------------------------------------------
# Sub-scorers
# ---------------------------------------------------------------------------

def _score_regime(strat: dict, regime: str | None) -> tuple[float, str]:
    """0-100 sub-score for regime fit."""
    if not regime or regime == "UNKNOWN":
        return 50.0, "regime unknown — neutral"
    ideal = strat.get("ideal_regime") or []
    if regime in ideal:
        return 100.0, f"regime {regime} matches ideal ({', '.join(ideal)})"
    # Soft penalty if regime is conceptually related
    if regime == "RANGE_BOUND" and any(r in ideal for r in ("LOW_VOL",)):
        return 70.0, f"regime {regime} partially fits"
    if regime in ("TRENDING_UP", "TRENDING_DOWN") and "TRENDING_UP" in ideal:
        # opposite-trend strategies get a hard penalty
        if (regime == "TRENDING_DOWN" and "TRENDING_DOWN" not in ideal) or \
           (regime == "TRENDING_UP" and "TRENDING_UP" not in ideal):
            return 20.0, f"regime {regime} conflicts with ideal"
    return 30.0, f"regime {regime} does not match ideal ({', '.join(ideal)})"


def _score_iv_rank(strat: dict, iv_rank: float | None) -> tuple[float, str]:
    if iv_rank is None:
        return 50.0, "iv_rank n/a (insufficient samples)"
    band = strat.get("ideal_iv_rank") or {}
    lo, hi = band.get("min", 0), band.get("max", 100)
    if lo <= iv_rank <= hi:
        # peak inside the band — distance from middle penalises
        mid = (lo + hi) / 2
        spread = max(1, hi - lo)
        # how close to mid as a fraction (0=center, 1=edge)
        dist = abs(iv_rank - mid) / (spread / 2)
        return float(100 - dist * 20), f"IV rank {iv_rank:.0f} inside [{lo}, {hi}]"
    # outside band — penalty proportional to how far
    over = max(0, iv_rank - hi)
    under = max(0, lo - iv_rank)
    miss = over + under
    score = max(0.0, 60.0 - miss * 1.5)
    return score, f"IV rank {iv_rank:.0f} outside [{lo}, {hi}]"


def _score_adx(strat: dict, adx: float | None) -> tuple[float, str]:
    if adx is None:
        return 50.0, "ADX n/a"
    band = strat.get("ideal_adx") or {}
    lo, hi = band.get("min", 0), band.get("max", 100)
    if lo <= adx <= hi:
        return 100.0, f"ADX {adx:.1f} in trend-fit band [{lo}, {hi}]"
    over = max(0, adx - hi)
    under = max(0, lo - adx)
    miss = over + under
    score = max(0.0, 70.0 - miss * 2.0)
    return score, f"ADX {adx:.1f} outside [{lo}, {hi}]"


def _score_vix(strat: dict, vix: float | None) -> tuple[float, str]:
    if vix is None:
        return 50.0, "VIX n/a"
    band = strat.get("ideal_vix") or {}
    lo, hi = band.get("min", 0), band.get("max", 100)
    if lo <= vix <= hi:
        return 100.0, f"VIX {vix:.1f} in fit band [{lo}, {hi}]"
    miss = max(0, vix - hi) + max(0, lo - vix)
    score = max(0.0, 65.0 - miss * 4.0)
    return score, f"VIX {vix:.1f} outside [{lo}, {hi}]"


def _score_win_rate(strat: dict) -> tuple[float, str]:
    wr = float(strat.get("win_rate", 0.5))
    score = wr * 100.0
    return score, f"baseline win rate {wr*100:.0f}%"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_strategy(
    strategy_class: str,
    *,
    regime: str | None,
    iv_rank: float | None,
    adx: float | None,
    vix: float | None,
) -> dict[str, Any] | None:
    """Score a single strategy against current market state.

    Returns dict with confidence + sub-scores + reasoning, or None if the
    strategy is unknown.
    """
    strat = strategy_fit.get_strategy(strategy_class)
    if not strat:
        return None

    sub: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []

    s_reg, r_reg = _score_regime(strat, regime); sub["regime"] = {"score": s_reg, "reason": r_reg}; reasons.append(r_reg)
    s_iv,  r_iv  = _score_iv_rank(strat, iv_rank); sub["iv_rank"] = {"score": s_iv, "reason": r_iv}; reasons.append(r_iv)
    s_adx, r_adx = _score_adx(strat, adx); sub["adx"] = {"score": s_adx, "reason": r_adx}; reasons.append(r_adx)
    s_vix, r_vix = _score_vix(strat, vix); sub["vix"] = {"score": s_vix, "reason": r_vix}; reasons.append(r_vix)
    s_wr,  r_wr  = _score_win_rate(strat); sub["win_rate"] = {"score": s_wr, "reason": r_wr}; reasons.append(r_wr)

    w = strategy_fit.SCORE_WEIGHTS
    confidence = (
        s_reg * w["regime"]
        + s_iv  * w["iv_rank"]
        + s_adx * w["adx"]
        + s_vix * w["vix"]
        + s_wr  * w["win_rate"]
    )
    confidence = round(max(0.0, min(100.0, confidence)), 1)

    label = _label_for(confidence)
    ready = confidence >= strategy_fit.AUTO_DEPLOY_THRESHOLD

    return {
        "strategy_class": strategy_class,
        "strategy_name":  strat["name"],
        "category":       strat["category"],
        "description":    strat["description"],
        "confidence":     confidence,
        "signal":         label,
        "color":          _label_color(label),
        "ready_to_deploy": ready,
        "sub_scores":     sub,
        "reasoning":      reasons,
        "expected_return_pct": strat.get("avg_return_pct"),
        "max_loss_pct":   strat.get("max_loss_pct"),
        "win_rate_pct":   round(strat.get("win_rate", 0) * 100),
        "capital_req":    strat.get("capital_req"),
        "ideal_regime":   strat.get("ideal_regime"),
        "default_legs":   strat.get("default_legs"),
        "entry_conditions": strat.get("entry_conditions"),
        "risk_params":    strat.get("risk_params"),
    }


def generate_signals(
    *,
    symbol: str = "NIFTY",
    candles: list[dict] | None = None,
    vix: float | None = None,
    spot: float | None = None,
) -> dict[str, Any]:
    """Top-level: classify regime, then score every strategy.

    Returns the regime + sorted list of strategy signals (highest confidence
    first) along with deploy-ready picks.
    """
    regime_data = classify_regime(symbol=symbol, candles=candles, vix=vix, spot=spot)

    # ADX from regime signals (already computed there)
    adx_val = regime_data.get("signals", {}).get("adx")
    iv_rank = get_iv_tracker().iv_rank(symbol)

    signals: list[dict[str, Any]] = []
    for strategy_class in strategy_fit.list_strategies():
        sig = score_strategy(
            strategy_class,
            regime=regime_data["regime"],
            iv_rank=iv_rank,
            adx=adx_val,
            vix=vix,
        )
        if sig:
            signals.append(sig)

    signals.sort(key=lambda s: s["confidence"], reverse=True)

    top = signals[0] if signals else None
    ready = [s for s in signals if s["ready_to_deploy"]]

    return {
        "symbol": symbol,
        "regime": regime_data,
        "iv_rank": iv_rank,
        "vix": vix,
        "spot": spot,
        "signals": signals,
        "top_pick": top,
        "deploy_ready": ready,
        "deploy_ready_count": len(ready),
        "auto_deploy_threshold": strategy_fit.AUTO_DEPLOY_THRESHOLD,
    }


def build_deploy_payload(
    signal: dict[str, Any],
    *,
    spot_price: float,
    lot_size: int,
    name_suffix: str = "AI",
) -> dict[str, Any]:
    """Construct a strategy-deploy payload from a signal.

    The result is shaped to match the body the existing /api/strategies/deploy
    endpoint expects. Includes auto-generated entry_conditions so the executor
    waits for the right market state before placing orders.
    """
    return {
        "name": f"{signal['strategy_name']} [{name_suffix}]",
        "underlying": "NIFTY",
        "spot_price": spot_price,
        "lot_size": lot_size,
        "legs": signal.get("default_legs", []),
        "risk_params": signal.get("risk_params", {}),
        "schedule": "market_open",
        "entry_conditions": signal.get("entry_conditions", []),
        "entry_trigger": "ALL",
        "condition_timeframe": "M5",
        "execution_mode": "paper",  # SAFETY: AI auto-deploy is paper-only by default
        "strategy_class": signal["strategy_class"],
    }
