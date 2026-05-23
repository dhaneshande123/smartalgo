"""
Strategy-Fit Matrix.

Encodes the *what works when* knowledge for every strategy template the
platform supports. The AI signal engine reads this map, scores how well
the current market matches each strategy's ideal conditions, and ranks
them by confidence.

Each entry includes:
- ``ideal_regime``      regimes (HIGH_VOL / LOW_VOL / TRENDING_UP / etc.)
                        where this strategy historically performs best
- ``ideal_iv_rank``     range of IV Rank (0-100) that suits this strategy
                        (e.g. premium sellers want HIGH iv_rank, buyers want LOW)
- ``ideal_adx``         ADX range — sellers want low ADX (no trend),
                        directional plays want high ADX
- ``win_rate``          baseline historical win rate (seeds confidence;
                        will be updated by real backtest results later)
- ``avg_return_pct``    typical % return on capital deployed
- ``max_loss_pct``      typical % max loss on capital deployed
- ``category``          option_selling / option_buying / hedged / intraday / etc.
- ``capital_req``       indicative capital needed (₹)
- ``default_legs``      template legs for auto-deploy (offsets + premiums in pts)
- ``entry_conditions``  default condition list the AI auto-deploy will use
- ``risk_params``       default risk knobs

Premium scoring weights are at the bottom and are deliberately tunable.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Strategy templates
# ---------------------------------------------------------------------------
# Each entry's keys map to UI / executor fields. Keep names consistent with
# the dashboard StrategyBuilder so auto-deploy can construct a valid payload.

STRATEGY_FIT: dict[str, dict[str, Any]] = {

    # ─── Premium-selling, range-bound ────────────────────────────────
    "iron_condor": {
        "name": "Iron Condor",
        "category": "option_selling",
        "description": "Sell OTM call + put, buy further OTM as protection. Profits from time decay in a sideways market.",
        "ideal_regime": ["RANGE_BOUND", "LOW_VOL"],
        "ideal_iv_rank": {"min": 50, "max": 100},
        "ideal_adx": {"min": 0, "max": 22},
        "ideal_vix": {"min": 12, "max": 25},
        "win_rate": 0.74,
        "avg_return_pct": 3.8,
        "max_loss_pct": 12.0,
        "capital_req": 200000,
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 200, "premium": 85, "lots": 1, "id": 1},
            {"type": "CE", "action": "BUY",  "offset": 400, "premium": 35, "lots": 1, "id": 2},
            {"type": "PE", "action": "SELL", "offset": -200, "premium": 80, "lots": 1, "id": 3},
            {"type": "PE", "action": "BUY",  "offset": -400, "premium": 30, "lots": 1, "id": 4},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
            {"indicator": "IV_RANK", "operator": ">", "value": 50},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 22},
        ],
        "risk_params": {
            "maxLossPerTrade": 8000, "target": 4000, "trailingStopPct": 30,
        },
    },

    "short_strangle": {
        "name": "Short Strangle",
        "category": "option_selling",
        "description": "Sell OTM call + OTM put, no protection. Higher credit, undefined risk. Range-bound markets only.",
        "ideal_regime": ["RANGE_BOUND", "LOW_VOL"],
        "ideal_iv_rank": {"min": 60, "max": 100},
        "ideal_adx": {"min": 0, "max": 20},
        "ideal_vix": {"min": 14, "max": 22},
        "win_rate": 0.78,
        "avg_return_pct": 4.5,
        "max_loss_pct": 25.0,
        "capital_req": 250000,
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 300, "premium": 65, "lots": 1, "id": 1},
            {"type": "PE", "action": "SELL", "offset": -300, "premium": 60, "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
            {"indicator": "IV_RANK", "operator": ">", "value": 60},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 20},
        ],
        "risk_params": {"maxLossPerTrade": 15000, "target": 5000, "trailingStopPct": 35},
    },

    "short_straddle": {
        "name": "Short Straddle (ATM)",
        "category": "option_selling",
        "description": "Sell ATM call + ATM put. Max premium, max risk. Pure neutral bet.",
        "ideal_regime": ["RANGE_BOUND", "LOW_VOL"],
        "ideal_iv_rank": {"min": 70, "max": 100},
        "ideal_adx": {"min": 0, "max": 18},
        "ideal_vix": {"min": 16, "max": 24},
        "win_rate": 0.71,
        "avg_return_pct": 5.2,
        "max_loss_pct": 30.0,
        "capital_req": 300000,
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 0, "premium": 180, "lots": 1, "id": 1},
            {"type": "PE", "action": "SELL", "offset": 0, "premium": 175, "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
            {"indicator": "IV_RANK", "operator": ">", "value": 70},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 18},
        ],
        "risk_params": {"maxLossPerTrade": 20000, "target": 8000, "trailingStopPct": 40},
    },

    # ─── Long volatility ──────────────────────────────────────────────
    "long_straddle": {
        "name": "Long Straddle",
        "category": "option_buying",
        "description": "Buy ATM call + put. Pays off on a big move either way. Best when IV is cheap before an event.",
        "ideal_regime": ["LOW_VOL", "TRENDING_UP", "TRENDING_DOWN"],
        "ideal_iv_rank": {"min": 0, "max": 30},
        "ideal_adx": {"min": 25, "max": 100},
        "ideal_vix": {"min": 0, "max": 14},
        "win_rate": 0.48,
        "avg_return_pct": 8.2,
        "max_loss_pct": 10.0,
        "capital_req": 150000,
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
            {"type": "PE", "action": "BUY", "offset": 0, "premium": 175, "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "IV_RANK", "operator": "<", "value": 30},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 25},
        ],
        "risk_params": {"maxLossPerTrade": 12000, "target": 18000, "trailingStopPct": 25},
    },

    # ─── Directional spreads ──────────────────────────────────────────
    "bull_call_spread": {
        "name": "Bull Call Spread",
        "category": "directional",
        "description": "Buy ATM call + sell OTM call. Bullish view with limited downside.",
        "ideal_regime": ["TRENDING_UP"],
        "ideal_iv_rank": {"min": 0, "max": 60},
        "ideal_adx": {"min": 22, "max": 100},
        "ideal_vix": {"min": 0, "max": 20},
        "win_rate": 0.58,
        "avg_return_pct": 6.5,
        "max_loss_pct": 8.0,
        "capital_req": 100000,
        "default_legs": [
            {"type": "CE", "action": "BUY",  "offset": 0,   "premium": 180, "lots": 1, "id": 1},
            {"type": "CE", "action": "SELL", "offset": 200, "premium": 85,  "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "TRENDING_UP"},
            {"indicator": "SUPERTREND_DIR", "operator": "==", "value": "UP"},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 22},
        ],
        "risk_params": {"maxLossPerTrade": 7500, "target": 10000, "trailingStopPct": 35},
    },

    "bear_put_spread": {
        "name": "Bear Put Spread",
        "category": "directional",
        "description": "Buy ATM put + sell OTM put. Bearish view with limited risk.",
        "ideal_regime": ["TRENDING_DOWN"],
        "ideal_iv_rank": {"min": 0, "max": 60},
        "ideal_adx": {"min": 22, "max": 100},
        "ideal_vix": {"min": 0, "max": 20},
        "win_rate": 0.55,
        "avg_return_pct": 6.0,
        "max_loss_pct": 8.0,
        "capital_req": 100000,
        "default_legs": [
            {"type": "PE", "action": "BUY",  "offset": 0,    "premium": 175, "lots": 1, "id": 1},
            {"type": "PE", "action": "SELL", "offset": -200, "premium": 80,  "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "TRENDING_DOWN"},
            {"indicator": "SUPERTREND_DIR", "operator": "==", "value": "DOWN"},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 22},
        ],
        "risk_params": {"maxLossPerTrade": 7500, "target": 10000, "trailingStopPct": 35},
    },

    # ─── Mean reversion ───────────────────────────────────────────────
    "mean_reversion": {
        "name": "Mean Reversion",
        "category": "intraday",
        "description": "Fade extreme RSI readings — buy oversold, sell overbought. Best in range-bound markets.",
        "ideal_regime": ["RANGE_BOUND"],
        "ideal_iv_rank": {"min": 0, "max": 100},
        "ideal_adx": {"min": 0, "max": 22},
        "ideal_vix": {"min": 0, "max": 25},
        "win_rate": 0.63,
        "avg_return_pct": 2.5,
        "max_loss_pct": 4.0,
        "capital_req": 100000,
        "default_legs": [
            # Buy ATM call OR put depending on direction — handled by AI
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
        ],
        "entry_conditions": [
            {"indicator": "RSI", "period": 14, "operator": "<", "value": 30},
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
        ],
        "risk_params": {"maxLossPerTrade": 4000, "target": 6000, "trailingStopPct": 30},
    },

    # ─── Momentum / trend-following ───────────────────────────────────
    "momentum_breakout": {
        "name": "Momentum Breakout",
        "category": "intraday",
        "description": "Buy ATM options when price breaks above 20-period high with rising volume.",
        "ideal_regime": ["TRENDING_UP", "TRENDING_DOWN"],
        "ideal_iv_rank": {"min": 0, "max": 70},
        "ideal_adx": {"min": 25, "max": 100},
        "ideal_vix": {"min": 0, "max": 22},
        "win_rate": 0.52,
        "avg_return_pct": 7.8,
        "max_loss_pct": 6.0,
        "capital_req": 120000,
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
        ],
        "entry_conditions": [
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 25},
            {"indicator": "VWAP_CROSS", "operator": ">", "value": 0},
            {"indicator": "MACD_HISTOGRAM", "operator": ">", "value": 0},
        ],
        "risk_params": {"maxLossPerTrade": 6000, "target": 9000, "trailingStopPct": 25},
    },

    "supertrend_follow": {
        "name": "Supertrend Follow",
        "category": "intraday",
        "description": "Enter in the direction Supertrend flipped. Exit when it flips again.",
        "ideal_regime": ["TRENDING_UP", "TRENDING_DOWN"],
        "ideal_iv_rank": {"min": 0, "max": 80},
        "ideal_adx": {"min": 22, "max": 100},
        "ideal_vix": {"min": 0, "max": 22},
        "win_rate": 0.55,
        "avg_return_pct": 6.0,
        "max_loss_pct": 5.0,
        "capital_req": 100000,
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
        ],
        "entry_conditions": [
            {"indicator": "SUPERTREND_DIR", "operator": "==", "value": "UP"},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 22},
        ],
        "risk_params": {"maxLossPerTrade": 5000, "target": 8000, "trailingStopPct": 30},
    },

    # ─── Jade Lizard (advanced hybrid) ────────────────────────────────
    "jade_lizard": {
        "name": "Jade Lizard",
        "category": "option_selling",
        "description": "Short OTM put + short call spread on the call side. No upside risk if credit > call spread width.",
        "ideal_regime": ["RANGE_BOUND", "TRENDING_UP"],
        "ideal_iv_rank": {"min": 50, "max": 100},
        "ideal_adx": {"min": 0, "max": 28},
        "ideal_vix": {"min": 13, "max": 22},
        "win_rate": 0.72,
        "avg_return_pct": 4.0,
        "max_loss_pct": 10.0,
        "capital_req": 180000,
        "default_legs": [
            {"type": "PE", "action": "SELL", "offset": -200, "premium": 80, "lots": 1, "id": 1},
            {"type": "CE", "action": "SELL", "offset":  200, "premium": 85, "lots": 1, "id": 2},
            {"type": "CE", "action": "BUY",  "offset":  400, "premium": 35, "lots": 1, "id": 3},
        ],
        "entry_conditions": [
            {"indicator": "IV_RANK", "operator": ">", "value": 50},
            {"indicator": "REGIME", "operator": "!=", "value": "TRENDING_DOWN"},
        ],
        "risk_params": {"maxLossPerTrade": 9000, "target": 4500, "trailingStopPct": 32},
    },
}


# ---------------------------------------------------------------------------
# Scoring weights (composite confidence)
# ---------------------------------------------------------------------------

SCORE_WEIGHTS = {
    "regime":    0.35,   # how well the regime matches
    "iv_rank":   0.25,   # IV environment fit
    "adx":       0.15,   # trend strength fit
    "vix":       0.10,   # absolute vol level
    "win_rate":  0.15,   # baseline historical win rate
}

# Confidence above which auto-deploy will fire
AUTO_DEPLOY_THRESHOLD = 70.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_strategies() -> list[str]:
    return list(STRATEGY_FIT.keys())


def get_strategy(strategy_class: str) -> dict[str, Any] | None:
    return STRATEGY_FIT.get(strategy_class)


def by_category() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sid, s in STRATEGY_FIT.items():
        out.setdefault(s["category"], []).append(sid)
    return out
