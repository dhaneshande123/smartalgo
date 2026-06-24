"""
Strategy-Fit Matrix — Institutional-Grade Option Strategies for Indian Markets.

Each strategy encodes *what works when* with regime-adaptive entry conditions,
delta-aware strike placement, multi-indicator confluence filters, and
professional risk management.

Scoring weights at the bottom drive the AI signal engine's confidence ranking.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Strategy templates
# ---------------------------------------------------------------------------

STRATEGY_FIT: dict[str, dict[str, Any]] = {

    # ═══════════════════════════════════════════════════════════════════
    # PREMIUM SELLING — Theta-positive, range-bound
    # ═══════════════════════════════════════════════════════════════════

    "iron_condor": {
        "name": "1SD Iron Condor",
        "category": "option_selling",
        "description": (
            "Sell 1-standard-deviation OTM call + put with protective wings. "
            "Profits from theta decay while IV contracts. Strikes placed near "
            "16-delta ensuring ~68% probability of profit. Best when VIX is "
            "elevated (mean-reversion setup) and ADX confirms no trend."
        ),
        "edge": "Positive theta + IV contraction. 68% POP at 1SD strikes.",
        "ideal_regime": ["RANGE_BOUND", "LOW_VOL"],
        "ideal_iv_rank": {"min": 50, "max": 100},
        "ideal_adx": {"min": 0, "max": 22},
        "ideal_vix": {"min": 12, "max": 25},
        "win_rate": 0.74,
        "avg_return_pct": 3.8,
        "max_loss_pct": 12.0,
        "capital_req": 200000,
        "schedule_window": ["09:25", "14:30"],
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 200,  "premium": 85, "lots": 1, "id": 1},
            {"type": "CE", "action": "BUY",  "offset": 400,  "premium": 35, "lots": 1, "id": 2},
            {"type": "PE", "action": "SELL", "offset": -200, "premium": 80, "lots": 1, "id": 3},
            {"type": "PE", "action": "BUY",  "offset": -400, "premium": 30, "lots": 1, "id": 4},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
            {"indicator": "IV_RANK", "operator": ">", "value": 50},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 22},
            {"indicator": "BB_POSITION", "operator": ">", "value": 0.25},
            {"indicator": "BB_POSITION", "operator": "<", "value": 0.75},
        ],
        "risk_params": {
            "maxLossPerTrade": 8000, "target": 4000, "trailingStopPct": 30, "maxHoldMinutes": 300,
        },
    },

    "short_strangle": {
        "name": "16-Delta Strangle",
        "category": "option_selling",
        "description": (
            "Sell ~16-delta OTM call + put — the institutional standard for "
            "premium harvesting. No hedge wings = higher credit but unlimited "
            "risk. Requires disciplined adjustment when delta breaches 30. "
            "Best entry: elevated VIX with ADX < 20 confirming no trend."
        ),
        "edge": "Maximum theta per margin dollar. 84% POP at 16-delta.",
        "ideal_regime": ["RANGE_BOUND", "LOW_VOL"],
        "ideal_iv_rank": {"min": 60, "max": 100},
        "ideal_adx": {"min": 0, "max": 20},
        "ideal_vix": {"min": 14, "max": 22},
        "win_rate": 0.78,
        "avg_return_pct": 4.5,
        "max_loss_pct": 25.0,
        "capital_req": 250000,
        "schedule_window": ["09:25", "14:00"],
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 300,  "premium": 65, "lots": 1, "id": 1},
            {"type": "PE", "action": "SELL", "offset": -300, "premium": 60, "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
            {"indicator": "IV_RANK", "operator": ">", "value": 60},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 20},
            {"indicator": "RSI", "period": 14, "operator": ">", "value": 35},
            {"indicator": "RSI", "period": 14, "operator": "<", "value": 65},
        ],
        "risk_params": {"maxLossPerTrade": 15000, "target": 5000, "trailingStopPct": 35, "maxHoldMinutes": 300},
    },

    "short_straddle": {
        "name": "ATM Straddle Sell",
        "category": "option_selling",
        "description": (
            "Sell ATM call + put for maximum premium collection. Highest "
            "theta but requires perfect timing and tight risk management. "
            "Entry only when VIX > 16 (elevated IV = rich premiums) and "
            "market is confirmed range-bound. Auto square-off at 15:15."
        ),
        "edge": "Maximum premium capture. Highest theta-per-lot of all strategies.",
        "ideal_regime": ["RANGE_BOUND", "LOW_VOL"],
        "ideal_iv_rank": {"min": 70, "max": 100},
        "ideal_adx": {"min": 0, "max": 18},
        "ideal_vix": {"min": 16, "max": 24},
        "win_rate": 0.71,
        "avg_return_pct": 5.2,
        "max_loss_pct": 30.0,
        "capital_req": 300000,
        "schedule_window": ["09:30", "13:00"],
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 0, "premium": 180, "lots": 1, "id": 1},
            {"type": "PE", "action": "SELL", "offset": 0, "premium": 175, "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
            {"indicator": "IV_RANK", "operator": ">", "value": 70},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 18},
            {"indicator": "VIX", "operator": ">", "value": 16},
        ],
        "risk_params": {"maxLossPerTrade": 20000, "target": 8000, "trailingStopPct": 40, "maxHoldMinutes": 300},
    },

    "iron_butterfly": {
        "name": "Iron Butterfly",
        "category": "option_selling",
        "description": (
            "Sell ATM call + put, buy OTM wings. Captures maximum theta "
            "at the ATM strike with defined risk. Higher credit than Iron "
            "Condor but narrower profit zone. Best on non-trending, "
            "low-ADX days when you expect NIFTY to pin near a round number."
        ),
        "edge": "Highest credit of defined-risk strategies. Max profit at expiry if spot = strike.",
        "ideal_regime": ["RANGE_BOUND"],
        "ideal_iv_rank": {"min": 60, "max": 100},
        "ideal_adx": {"min": 0, "max": 16},
        "ideal_vix": {"min": 14, "max": 24},
        "win_rate": 0.62,
        "avg_return_pct": 6.5,
        "max_loss_pct": 15.0,
        "capital_req": 200000,
        "schedule_window": ["09:25", "14:00"],
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 0,    "premium": 180, "lots": 1, "id": 1},
            {"type": "PE", "action": "SELL", "offset": 0,    "premium": 175, "lots": 1, "id": 2},
            {"type": "CE", "action": "BUY",  "offset": 300,  "premium": 50,  "lots": 1, "id": 3},
            {"type": "PE", "action": "BUY",  "offset": -300, "premium": 45,  "lots": 1, "id": 4},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
            {"indicator": "IV_RANK", "operator": ">", "value": 60},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 16},
        ],
        "risk_params": {"maxLossPerTrade": 12000, "target": 7000, "trailingStopPct": 35, "maxHoldMinutes": 300},
    },

    "jade_lizard": {
        "name": "Jade Lizard",
        "category": "option_selling",
        "description": (
            "Short OTM put + short call spread. Eliminates upside risk entirely "
            "when net credit exceeds call spread width. Bullish-to-neutral bias. "
            "Institutional favorite for collecting premium with zero upside risk."
        ),
        "edge": "Zero upside risk when credit > call spread width. Bullish theta play.",
        "ideal_regime": ["RANGE_BOUND", "TRENDING_UP"],
        "ideal_iv_rank": {"min": 50, "max": 100},
        "ideal_adx": {"min": 0, "max": 28},
        "ideal_vix": {"min": 13, "max": 22},
        "win_rate": 0.72,
        "avg_return_pct": 4.0,
        "max_loss_pct": 10.0,
        "capital_req": 180000,
        "schedule_window": ["09:25", "14:30"],
        "default_legs": [
            {"type": "PE", "action": "SELL", "offset": -200, "premium": 80, "lots": 1, "id": 1},
            {"type": "CE", "action": "SELL", "offset":  200, "premium": 85, "lots": 1, "id": 2},
            {"type": "CE", "action": "BUY",  "offset":  400, "premium": 35, "lots": 1, "id": 3},
        ],
        "entry_conditions": [
            {"indicator": "IV_RANK", "operator": ">", "value": 50},
            {"indicator": "REGIME", "operator": "!=", "value": "TRENDING_DOWN"},
            {"indicator": "RSI", "period": 14, "operator": ">", "value": 40},
        ],
        "risk_params": {"maxLossPerTrade": 9000, "target": 4500, "trailingStopPct": 32, "maxHoldMinutes": 300},
    },

    "expiry_straddle_sell": {
        "name": "Expiry Day Straddle",
        "category": "option_selling",
        "description": (
            "Sell ATM straddle on weekly expiry day. Theta accelerates "
            "exponentially in the last few hours — premium decays 3-5x "
            "faster than a normal day. Enter after 10:30 once initial "
            "volatility settles. Tight SL mandatory."
        ),
        "edge": "Theta acceleration on expiry. Premium decays 3-5x faster than normal days.",
        "ideal_regime": ["RANGE_BOUND", "LOW_VOL"],
        "ideal_iv_rank": {"min": 30, "max": 100},
        "ideal_adx": {"min": 0, "max": 22},
        "ideal_vix": {"min": 10, "max": 22},
        "win_rate": 0.68,
        "avg_return_pct": 3.0,
        "max_loss_pct": 8.0,
        "capital_req": 300000,
        "schedule_window": ["10:30", "14:30"],
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 0, "premium": 90, "lots": 1, "id": 1},
            {"type": "PE", "action": "SELL", "offset": 0, "premium": 85, "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "IS_EXPIRY_DAY", "operator": "==", "value": True},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 22},
            {"indicator": "REGIME", "operator": "!=", "value": "HIGH_VOL"},
        ],
        "risk_params": {"maxLossPerTrade": 10000, "target": 3500, "trailingStopPct": 50, "maxHoldMinutes": 120},
    },

    # ═══════════════════════════════════════════════════════════════════
    # VOLATILITY BUYING — Long gamma, event-driven
    # ═══════════════════════════════════════════════════════════════════

    "long_straddle": {
        "name": "Long Straddle (Pre-Event)",
        "category": "option_buying",
        "description": (
            "Buy ATM call + put when IV is cheap (IV Rank < 30). Profits "
            "from a big move in either direction or an IV expansion. Best "
            "entered before events (budget, RBI policy, earnings). "
            "Requires ADX > 25 confirming a move is building."
        ),
        "edge": "Long gamma before events. Profits from moves OR IV expansion.",
        "ideal_regime": ["LOW_VOL", "TRENDING_UP", "TRENDING_DOWN"],
        "ideal_iv_rank": {"min": 0, "max": 30},
        "ideal_adx": {"min": 25, "max": 100},
        "ideal_vix": {"min": 0, "max": 14},
        "win_rate": 0.48,
        "avg_return_pct": 8.2,
        "max_loss_pct": 10.0,
        "capital_req": 150000,
        "schedule_window": ["09:20", "14:00"],
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
            {"type": "PE", "action": "BUY", "offset": 0, "premium": 175, "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "IV_RANK", "operator": "<", "value": 30},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 25},
        ],
        "risk_params": {"maxLossPerTrade": 12000, "target": 18000, "trailingStopPct": 25, "maxHoldMinutes": 240},
    },

    "long_strangle_otm": {
        "name": "OTM Long Strangle",
        "category": "option_buying",
        "description": (
            "Buy OTM call + put — cheaper than a straddle, needs a bigger "
            "move to profit. Lower cost, higher leverage. Enter when IV "
            "is extremely cheap and a breakout is imminent (ADX rising, "
            "Bollinger squeeze)."
        ),
        "edge": "Cheaper than straddle with higher leverage. Breakout play.",
        "ideal_regime": ["LOW_VOL"],
        "ideal_iv_rank": {"min": 0, "max": 20},
        "ideal_adx": {"min": 20, "max": 100},
        "ideal_vix": {"min": 0, "max": 13},
        "win_rate": 0.42,
        "avg_return_pct": 12.0,
        "max_loss_pct": 8.0,
        "capital_req": 80000,
        "schedule_window": ["09:20", "13:00"],
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 200,  "premium": 85, "lots": 1, "id": 1},
            {"type": "PE", "action": "BUY", "offset": -200, "premium": 80, "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "IV_RANK", "operator": "<", "value": 20},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 20},
            {"indicator": "BB_WIDTH", "operator": "<", "value": 0.015},
        ],
        "risk_params": {"maxLossPerTrade": 6000, "target": 15000, "trailingStopPct": 20, "maxHoldMinutes": 240},
    },

    # ═══════════════════════════════════════════════════════════════════
    # DIRECTIONAL — Trend-following with defined risk
    # ═══════════════════════════════════════════════════════════════════

    "bull_call_spread": {
        "name": "Bull Call Spread",
        "category": "directional",
        "description": (
            "Buy ATM call + sell OTM call. Defined-risk bullish bet. "
            "Costs less than naked call, profits are capped at spread "
            "width. Enter when Supertrend is UP, ADX > 22 confirms "
            "trend strength, and RSI is not overbought."
        ),
        "edge": "Defined risk bullish play. Lower cost than naked calls.",
        "ideal_regime": ["TRENDING_UP"],
        "ideal_iv_rank": {"min": 0, "max": 60},
        "ideal_adx": {"min": 22, "max": 100},
        "ideal_vix": {"min": 0, "max": 20},
        "win_rate": 0.58,
        "avg_return_pct": 6.5,
        "max_loss_pct": 8.0,
        "capital_req": 100000,
        "schedule_window": ["09:25", "14:30"],
        "default_legs": [
            {"type": "CE", "action": "BUY",  "offset": 0,   "premium": 180, "lots": 1, "id": 1},
            {"type": "CE", "action": "SELL", "offset": 200, "premium": 85,  "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "TRENDING_UP"},
            {"indicator": "SUPERTREND_DIR", "operator": "==", "value": "UP"},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 22},
            {"indicator": "RSI", "period": 14, "operator": "<", "value": 70},
        ],
        "risk_params": {"maxLossPerTrade": 7500, "target": 10000, "trailingStopPct": 35, "maxHoldMinutes": 300},
    },

    "bear_put_spread": {
        "name": "Bear Put Spread",
        "category": "directional",
        "description": (
            "Buy ATM put + sell OTM put. Defined-risk bearish bet. "
            "Enter when Supertrend flips DOWN, MACD crosses below signal, "
            "and ADX confirms momentum. Cheaper than naked puts."
        ),
        "edge": "Defined risk bearish play. Profits from downtrend with capped loss.",
        "ideal_regime": ["TRENDING_DOWN"],
        "ideal_iv_rank": {"min": 0, "max": 60},
        "ideal_adx": {"min": 22, "max": 100},
        "ideal_vix": {"min": 0, "max": 20},
        "win_rate": 0.55,
        "avg_return_pct": 6.0,
        "max_loss_pct": 8.0,
        "capital_req": 100000,
        "schedule_window": ["09:25", "14:30"],
        "default_legs": [
            {"type": "PE", "action": "BUY",  "offset": 0,    "premium": 175, "lots": 1, "id": 1},
            {"type": "PE", "action": "SELL", "offset": -200, "premium": 80,  "lots": 1, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "TRENDING_DOWN"},
            {"indicator": "SUPERTREND_DIR", "operator": "==", "value": "DOWN"},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 22},
            {"indicator": "RSI", "period": 14, "operator": ">", "value": 30},
        ],
        "risk_params": {"maxLossPerTrade": 7500, "target": 10000, "trailingStopPct": 35, "maxHoldMinutes": 300},
    },

    "ratio_call_spread": {
        "name": "Call Ratio Back Spread",
        "category": "directional",
        "description": (
            "Sell 1 ATM call, buy 2 OTM calls. Net debit or small credit. "
            "Unlimited upside profit if a strong rally occurs. Limited loss "
            "if market stays flat (the worst case). Institutional play for "
            "asymmetric bullish exposure."
        ),
        "edge": "Unlimited upside with defined max loss. Asymmetric risk-reward.",
        "ideal_regime": ["TRENDING_UP", "LOW_VOL"],
        "ideal_iv_rank": {"min": 0, "max": 50},
        "ideal_adx": {"min": 25, "max": 100},
        "ideal_vix": {"min": 0, "max": 18},
        "win_rate": 0.45,
        "avg_return_pct": 15.0,
        "max_loss_pct": 6.0,
        "capital_req": 150000,
        "schedule_window": ["09:25", "13:00"],
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 0,   "premium": 180, "lots": 1, "id": 1},
            {"type": "CE", "action": "BUY",  "offset": 200, "premium": 85,  "lots": 2, "id": 2},
        ],
        "entry_conditions": [
            {"indicator": "REGIME", "operator": "==", "value": "TRENDING_UP"},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 25},
            {"indicator": "MACD_HISTOGRAM", "operator": ">", "value": 0},
            {"indicator": "IV_RANK", "operator": "<", "value": 50},
        ],
        "risk_params": {"maxLossPerTrade": 8000, "target": 20000, "trailingStopPct": 20, "maxHoldMinutes": 240},
    },

    # ═══════════════════════════════════════════════════════════════════
    # HEDGED — Risk-managed multi-leg
    # ═══════════════════════════════════════════════════════════════════

    "broken_wing_butterfly": {
        "name": "Broken Wing Butterfly",
        "category": "hedged",
        "description": (
            "Asymmetric butterfly — skip-strike on one side for a credit. "
            "Zero risk on the wide side, moderate risk on the narrow side. "
            "Placed for a credit so even max loss is reduced. Used by "
            "institutional desks to express a directional view with protection."
        ),
        "edge": "Zero risk on one side. Placed for credit — even max loss is reduced.",
        "ideal_regime": ["RANGE_BOUND", "TRENDING_UP"],
        "ideal_iv_rank": {"min": 40, "max": 100},
        "ideal_adx": {"min": 0, "max": 25},
        "ideal_vix": {"min": 12, "max": 22},
        "win_rate": 0.60,
        "avg_return_pct": 5.0,
        "max_loss_pct": 8.0,
        "capital_req": 150000,
        "schedule_window": ["09:30", "14:00"],
        "default_legs": [
            {"type": "CE", "action": "BUY",  "offset": -100, "premium": 220, "lots": 1, "id": 1},
            {"type": "CE", "action": "SELL", "offset": 100,  "premium": 120, "lots": 2, "id": 2},
            {"type": "CE", "action": "BUY",  "offset": 400,  "premium": 35,  "lots": 1, "id": 3},
        ],
        "entry_conditions": [
            {"indicator": "IV_RANK", "operator": ">", "value": 40},
            {"indicator": "REGIME", "operator": "!=", "value": "HIGH_VOL"},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 25},
        ],
        "risk_params": {"maxLossPerTrade": 7000, "target": 5000, "trailingStopPct": 30, "maxHoldMinutes": 300},
    },

    # ═══════════════════════════════════════════════════════════════════
    # INTRADAY — Momentum & Mean-reversion
    # ═══════════════════════════════════════════════════════════════════

    "mean_reversion": {
        "name": "RSI Mean Reversion",
        "category": "intraday",
        "description": (
            "Fade extreme RSI readings — buy ATM CE when RSI < 30 "
            "(oversold), sell when RSI > 70 (overbought). Requires "
            "range-bound market (trending markets kill mean reversion). "
            "Quick scalp with tight SL."
        ),
        "edge": "Fade extremes in range markets. Quick theta-neutral scalps.",
        "ideal_regime": ["RANGE_BOUND"],
        "ideal_iv_rank": {"min": 0, "max": 100},
        "ideal_adx": {"min": 0, "max": 22},
        "ideal_vix": {"min": 0, "max": 25},
        "win_rate": 0.63,
        "avg_return_pct": 2.5,
        "max_loss_pct": 4.0,
        "capital_req": 100000,
        "schedule_window": ["09:30", "14:30"],
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
        ],
        "entry_conditions": [
            {"indicator": "RSI", "period": 14, "operator": "<", "value": 30},
            {"indicator": "REGIME", "operator": "==", "value": "RANGE_BOUND"},
            {"indicator": "ADX", "period": 14, "operator": "<", "value": 22},
        ],
        "risk_params": {"maxLossPerTrade": 4000, "target": 6000, "trailingStopPct": 30, "maxHoldMinutes": 120},
    },

    "momentum_breakout": {
        "name": "VWAP Momentum Breakout",
        "category": "intraday",
        "description": (
            "Buy ATM CE/PE when price breaks above/below VWAP with "
            "ADX > 25 confirming trend strength and MACD histogram "
            "positive. Catches intraday trending moves. Best on "
            "gap-up/gap-down days with follow-through."
        ),
        "edge": "Catches trending intraday moves with VWAP + ADX confirmation.",
        "ideal_regime": ["TRENDING_UP", "TRENDING_DOWN"],
        "ideal_iv_rank": {"min": 0, "max": 70},
        "ideal_adx": {"min": 25, "max": 100},
        "ideal_vix": {"min": 0, "max": 22},
        "win_rate": 0.52,
        "avg_return_pct": 7.8,
        "max_loss_pct": 6.0,
        "capital_req": 120000,
        "schedule_window": ["09:25", "14:00"],
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
        ],
        "entry_conditions": [
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 25},
            {"indicator": "VWAP_CROSS", "operator": ">", "value": 0},
            {"indicator": "MACD_HISTOGRAM", "operator": ">", "value": 0},
        ],
        "risk_params": {"maxLossPerTrade": 6000, "target": 9000, "trailingStopPct": 25, "maxHoldMinutes": 150},
    },

    "supertrend_follow": {
        "name": "Supertrend Rider",
        "category": "intraday",
        "description": (
            "Enter in the direction Supertrend flipped. Hold until "
            "Supertrend reverses or trailing SL hits. ADX filter ensures "
            "genuine trend (avoids whipsaws in choppy markets). "
            "Classic systematic trend-following approach."
        ),
        "edge": "Systematic trend following. Rides established trends with trailing SL.",
        "ideal_regime": ["TRENDING_UP", "TRENDING_DOWN"],
        "ideal_iv_rank": {"min": 0, "max": 80},
        "ideal_adx": {"min": 22, "max": 100},
        "ideal_vix": {"min": 0, "max": 22},
        "win_rate": 0.55,
        "avg_return_pct": 6.0,
        "max_loss_pct": 5.0,
        "capital_req": 100000,
        "schedule_window": ["09:25", "14:30"],
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
        ],
        "entry_conditions": [
            {"indicator": "SUPERTREND_DIR", "operator": "==", "value": "UP"},
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 22},
        ],
        "risk_params": {"maxLossPerTrade": 5000, "target": 8000, "trailingStopPct": 30, "maxHoldMinutes": 180},
    },

    "orb_breakout": {
        "name": "Opening Range Breakout",
        "category": "intraday",
        "description": (
            "Trade the first 15-minute range breakout. Wait for 9:15-9:30 "
            "range to establish, then buy CE on high breakout or PE on "
            "low breakdown. ADX rising + volume surge confirms the break. "
            "Classic institutional intraday strategy."
        ),
        "edge": "Trades the established first-15-min range. 60%+ moves extend from ORB.",
        "ideal_regime": ["TRENDING_UP", "TRENDING_DOWN", "HIGH_VOL"],
        "ideal_iv_rank": {"min": 0, "max": 80},
        "ideal_adx": {"min": 18, "max": 100},
        "ideal_vix": {"min": 0, "max": 25},
        "win_rate": 0.56,
        "avg_return_pct": 5.5,
        "max_loss_pct": 4.0,
        "capital_req": 100000,
        "schedule_window": ["09:30", "10:15"],
        "default_legs": [
            {"type": "CE", "action": "BUY", "offset": 0, "premium": 180, "lots": 1, "id": 1},
        ],
        "entry_conditions": [
            {"indicator": "ADX", "period": 14, "operator": ">", "value": 18},
            {"indicator": "MACD_HISTOGRAM", "operator": ">", "value": 0},
        ],
        "risk_params": {"maxLossPerTrade": 4000, "target": 7000, "trailingStopPct": 25, "maxHoldMinutes": 90},
    },

    # ═══════════════════════════════════════════════════════════════════
    # VIX-BASED — Volatility-regime strategies
    # ═══════════════════════════════════════════════════════════════════

    "vix_mean_reversion": {
        "name": "VIX Crush Play",
        "category": "option_selling",
        "description": (
            "When VIX spikes above 20, sell premium aggressively — VIX "
            "mean-reverts 80%+ of the time. Use Iron Condor structure "
            "for defined risk. The elevated IV makes premiums rich. "
            "Wait for VIX to start declining (confirmed by VIX < prev close)."
        ),
        "edge": "VIX mean-reverts 80%+ of the time. Sell rich premiums after spike.",
        "ideal_regime": ["HIGH_VOL", "RANGE_BOUND"],
        "ideal_iv_rank": {"min": 75, "max": 100},
        "ideal_adx": {"min": 0, "max": 30},
        "ideal_vix": {"min": 20, "max": 40},
        "win_rate": 0.70,
        "avg_return_pct": 5.5,
        "max_loss_pct": 14.0,
        "capital_req": 250000,
        "schedule_window": ["09:25", "14:00"],
        "default_legs": [
            {"type": "CE", "action": "SELL", "offset": 250,  "premium": 110, "lots": 1, "id": 1},
            {"type": "CE", "action": "BUY",  "offset": 450,  "premium": 50,  "lots": 1, "id": 2},
            {"type": "PE", "action": "SELL", "offset": -250, "premium": 105, "lots": 1, "id": 3},
            {"type": "PE", "action": "BUY",  "offset": -450, "premium": 45,  "lots": 1, "id": 4},
        ],
        "entry_conditions": [
            {"indicator": "VIX", "operator": ">", "value": 20},
            {"indicator": "IV_RANK", "operator": ">", "value": 75},
        ],
        "risk_params": {"maxLossPerTrade": 12000, "target": 6000, "trailingStopPct": 35, "maxHoldMinutes": 300},
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
