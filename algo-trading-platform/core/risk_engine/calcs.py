"""
SmartAlgo Risk Engine
─────────────────────────────────────────────────────────────────────────────
Institutional-grade risk calculations for Indian F&O markets.

Pure-Python module (no FastAPI imports). Computes:
  - Black-Scholes Greeks (delta, gamma, theta, vega, rho)
  - Implied Volatility back-solve (Newton-Raphson)
  - Portfolio Greeks aggregation (signed by side)
  - Value at Risk (VaR) — Parametric delta-normal method
  - Stress testing — Taylor expansion (Δ, Γ, ν, Θ)
  - NSE F&O margin approximation (SPAN + Exposure)
  - Risk limit breach detection
  - Drawdown tracking

All functions are stateless and side-effect free. Pass in data, get numbers
back. Storage and orchestration live elsewhere (state_store.py, api.py).

Conventions:
  - Quantities are SIGNED at the position level: +qty = LONG, -qty = SHORT
  - When `side`/`qty` are unsigned, the function infers sign internally.
  - All Greeks returned are PER-CONTRACT (multiply by qty * lot_size for portfolio).
  - Time `T` is in YEARS (e.g., 7 days = 7/365.0 = 0.0192).
  - Volatility `sigma` is annualized (e.g., 16% IV = 0.16, NOT 16).
  - Rates `r` and `q` (dividend) are annualized continuous rates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional, Iterable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# ─────────────────────────────────────────────────────────────────────────────
# Constants — calibrated for Indian options
# ─────────────────────────────────────────────────────────────────────────────

RISK_FREE_RATE_DEFAULT = 0.065        # ~India 10Y G-Sec yield, used if not passed
DIVIDEND_YIELD_DEFAULT = 0.012        # NIFTY dividend yield approx
TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365

# VaR Z-scores
Z_SCORE_95 = 1.6449
Z_SCORE_99 = 2.3263

# Margin model (NSE F&O approximation — accurate within ~5% of real SPAN)
SPAN_NOTIONAL_PCT = 0.115             # ~11.5% of notional for options sellers
EXPOSURE_NOTIONAL_PCT = 0.025         # ~2.5% additional exposure margin
BUYER_MARGIN_IS_PREMIUM = True        # Option buyers: margin = premium paid

# Default risk limits (used if SQLite has none yet)
DEFAULT_RISK_LIMITS = {
    "max_daily_loss": 50_000.0,           # Rs50k/day
    "max_drawdown_pct": 5.0,              # 5% of peak equity
    "max_position_value": 2_000_000.0,    # Rs20L per strategy
    "max_portfolio_value": 10_000_000.0,  # Rs1Cr total
    "max_open_strategies": 10,
    "max_orders_per_minute": 30,
    "max_delta_exposure": 500.0,          # Net portfolio delta (in lots-equivalent)
    "max_gamma_exposure": 100.0,
    "max_vega_exposure": 50_000.0,        # Rs per 1% IV move
    "max_theta_decay": -25_000.0,         # Negative theta = decay AGAINST us
    "position_concentration_limit_pct": 0.25,  # 25% in single underlying
    "consecutive_losses_limit": 5,
    "alert_threshold_pct": 0.80,          # Alert at 80% of any limit
    "auto_kill_threshold_pct": 1.00,      # Auto-kill at 100% (conservative)
}


# ─────────────────────────────────────────────────────────────────────────────
# Black-Scholes Greeks
# ─────────────────────────────────────────────────────────────────────────────


def _norm_cdf(x: float) -> float:
    """Standard normal CDF — using erf for accuracy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(
    S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0
) -> tuple[float, float]:
    """Black-Scholes d1 and d2 helpers. Guards against zero T/sigma."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def bs_price(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "CE", q: float = 0.0,
) -> float:
    """
    Black-Scholes-Merton option price.

    Returns 0 for degenerate inputs (T≤0, sigma≤0, etc).
    option_type: 'CE' (call) or 'PE' (put).
    """
    if T <= 0 or sigma <= 0:
        # Intrinsic only
        if option_type.upper() == "CE":
            return max(0.0, S - K)
        return max(0.0, K - S)

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    if option_type.upper() == "CE":
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def bs_greeks(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "CE", q: float = 0.0,
) -> dict:
    """
    Black-Scholes-Merton Greeks.

    Returns dict with: delta, gamma, theta (per day), vega (per 1% IV), rho (per 1% rate).

    Note: theta and vega are scaled to "per day" and "per 1% IV move" — these are
    the conventions traders use (not raw BS units).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    n_d1 = _norm_pdf(d1)
    discount_r = math.exp(-r * T)
    discount_q = math.exp(-q * T)
    sqrt_T = math.sqrt(T)

    if option_type.upper() == "CE":
        delta = discount_q * _norm_cdf(d1)
        theta_raw = (
            -S * discount_q * n_d1 * sigma / (2.0 * sqrt_T)
            - r * K * discount_r * _norm_cdf(d2)
            + q * S * discount_q * _norm_cdf(d1)
        )
        rho = K * T * discount_r * _norm_cdf(d2)
    else:
        delta = -discount_q * _norm_cdf(-d1)
        theta_raw = (
            -S * discount_q * n_d1 * sigma / (2.0 * sqrt_T)
            + r * K * discount_r * _norm_cdf(-d2)
            - q * S * discount_q * _norm_cdf(-d1)
        )
        rho = -K * T * discount_r * _norm_cdf(-d2)

    gamma = discount_q * n_d1 / (S * sigma * sqrt_T)
    vega = S * discount_q * n_d1 * sqrt_T  # per 1.0 change in sigma

    # Convert to trader conventions:
    return {
        "delta": delta,                              # 0..1 (call), -1..0 (put)
        "gamma": gamma,                              # per unit spot move
        "theta": theta_raw / CALENDAR_DAYS_PER_YEAR, # per CALENDAR day
        "vega": vega / 100.0,                        # per 1% IV move (not 1.0)
        "rho": rho / 100.0,                          # per 1% rate move
    }


def implied_volatility(
    market_price: float, S: float, K: float, T: float, r: float,
    option_type: str = "CE", q: float = 0.0,
    initial_guess: float = 0.20, max_iter: int = 50, tol: float = 1e-5,
) -> float:
    """
    Back-solve implied volatility using Newton-Raphson.

    Returns annualized IV (e.g., 0.16 for 16%). Returns 0.0 if cannot converge
    or for degenerate inputs.
    """
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0

    # Intrinsic floor check
    intrinsic = max(0.0, (S - K) if option_type.upper() == "CE" else (K - S))
    if market_price < intrinsic:
        return 0.0  # Below intrinsic — quote is stale/wrong

    sigma = initial_guess
    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, option_type, q)
        diff = price - market_price
        if abs(diff) < tol:
            return max(0.001, sigma)  # clamp to 0.1% minimum

        # Vega for Newton step (raw, not scaled)
        _, _ = _d1_d2(S, K, T, r, sigma, q)
        d1, _ = _d1_d2(S, K, T, r, sigma, q)
        vega = S * math.exp(-q * T) * _norm_pdf(d1) * math.sqrt(T)
        if vega < 1e-8:
            return 0.0  # No sensitivity — bail
        sigma = sigma - diff / vega
        if sigma <= 0:
            sigma = 0.01  # bounce back from negative
        if sigma > 5.0:
            return 0.0  # IV > 500% means convergence failed

    return max(0.001, min(sigma, 5.0))


# ─────────────────────────────────────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────────────────────────────────────


def days_to_expiry(expiry_str: str, ref_date: Optional[datetime] = None) -> float:
    """
    Calendar days from `ref_date` (default now) to expiry midnight.
    Returns a float (fractional days). Min 0.0 (expired).

    Accepts ISO date 'YYYY-MM-DD' or full ISO datetime.
    """
    if not expiry_str:
        return 0.0
    ref = ref_date or datetime.now(IST)
    try:
        # Try date first
        if len(expiry_str) == 10:
            exp = datetime.strptime(expiry_str, "%Y-%m-%d").replace(
                hour=15, minute=30, tzinfo=IST
            )
        else:
            exp = datetime.fromisoformat(expiry_str)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=IST)
        delta = (exp - ref).total_seconds() / 86400.0
        return max(0.0, delta)
    except Exception:
        return 0.0


def time_to_expiry_years(expiry_str: str, ref_date: Optional[datetime] = None) -> float:
    """Days to expiry expressed in years (calendar)."""
    return days_to_expiry(expiry_str, ref_date) / CALENDAR_DAYS_PER_YEAR


# ─────────────────────────────────────────────────────────────────────────────
# Position-level Greeks computation (with side sign)
# ─────────────────────────────────────────────────────────────────────────────


def _signed_qty(qty: float, side: str) -> float:
    """Convert unsigned qty + side to signed qty (+BUY, -SELL)."""
    side_upper = (side or "").upper().strip()
    if side_upper in ("SELL", "SHORT", "S"):
        return -abs(qty)
    return abs(qty)


def position_greeks(
    position: dict, spot: float, iv: float, r: float = RISK_FREE_RATE_DEFAULT,
    q: float = DIVIDEND_YIELD_DEFAULT, lot_size: int = 1,
    ref_date: Optional[datetime] = None,
) -> dict:
    """
    Compute Greeks for a single position (one leg of a strategy).

    `position` dict must contain:
        strike, option_type ('CE'/'PE'), side ('BUY'/'SELL'), qty, expiry

    Returns position-level Greeks (already multiplied by signed_qty * lot_size).
    """
    strike = float(position.get("strike", 0) or 0)
    option_type = str(position.get("option_type", "CE")).upper()
    side = str(position.get("side", "BUY")).upper()
    qty = float(position.get("qty", 0) or 0)
    expiry = position.get("expiry", "")

    if strike <= 0 or qty <= 0 or spot <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    T = time_to_expiry_years(expiry, ref_date)
    if T <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    # Per-contract Greeks
    g = bs_greeks(spot, strike, T, r, iv, option_type, q)

    # Scale by signed contract count
    signed = _signed_qty(qty, side) * lot_size
    return {
        "delta": g["delta"] * signed,
        "gamma": g["gamma"] * signed,
        "theta": g["theta"] * signed,    # already per-day
        "vega": g["vega"] * signed,      # already per-1%
        "rho": g["rho"] * signed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio-level aggregation
# ─────────────────────────────────────────────────────────────────────────────


def aggregate_portfolio_greeks(
    strategies: dict, chain_cache: dict, lot_sizes: Optional[dict] = None,
    r: float = RISK_FREE_RATE_DEFAULT, q: float = DIVIDEND_YIELD_DEFAULT,
    ref_date: Optional[datetime] = None,
) -> dict:
    """
    Sum signed Greeks across ALL deployed strategy positions.

    `strategies`: _deployed_strategies dict from api.py.
                  Only RUNNING/ENTERED strategies counted.
    `chain_cache`: _fyers_chain_cache — per-underlying option chain.
    `lot_sizes`: optional dict like {'NIFTY': 65, 'BANKNIFTY': 30}. Defaults to 1.

    Returns:
        {
            'portfolio': {delta, gamma, theta, vega, rho, total_notional, total_premium},
            'by_strategy': {sid: {delta, gamma, theta, vega, name, underlying}},
            'by_underlying': {'NIFTY': {delta, ...}, 'BANKNIFTY': {...}},
            'positions_count': int,
            'strategies_count': int,
        }
    """
    lot_sizes = lot_sizes or {}
    portfolio = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0,
                 "total_notional": 0.0, "total_premium": 0.0}
    by_strategy: dict = {}
    by_underlying: dict = {}
    positions_count = 0
    strategies_count = 0

    for sid, strat in (strategies or {}).items():
        status = str(strat.get("status", "")).upper()
        if status not in ("RUNNING", "ENTERED"):
            continue
        # Only entered strategies have real exposure
        if not strat.get("entered", False) and status != "ENTERED":
            continue

        underlying = (strat.get("underlying") or "NIFTY").upper()
        chain = chain_cache.get(underlying) or {}
        spot = float(chain.get("spot_price", 0) or 0)
        india_vix = float(chain.get("india_vix", 14.0) or 14.0)
        # India VIX is in % (e.g., 14.0) — convert to decimal for BS
        default_iv = india_vix / 100.0
        lot_size = int(lot_sizes.get(underlying, chain.get("lot_size", 1)) or 1)

        strategies_count += 1
        strat_greeks = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

        for pos in strat.get("positions", []):
            positions_count += 1
            # Try to find per-strike IV from chain (else fallback to India VIX)
            strike = pos.get("strike")
            opt_type = (pos.get("option_type") or "CE").upper()
            iv = _find_strike_iv(chain, strike, opt_type) or default_iv

            pg = position_greeks(
                pos, spot=spot, iv=iv, r=r, q=q,
                lot_size=lot_size, ref_date=ref_date,
            )
            for k in ("delta", "gamma", "theta", "vega", "rho"):
                portfolio[k] += pg[k]
                strat_greeks[k] += pg[k]

            # Notional & premium
            notional = abs(_signed_qty(pos.get("qty", 0), pos.get("side", ""))) \
                       * lot_size * (strike or 0)
            premium = abs(_signed_qty(pos.get("qty", 0), pos.get("side", ""))) \
                      * lot_size * float(pos.get("entry_price", 0) or 0)
            portfolio["total_notional"] += notional
            portfolio["total_premium"] += premium

            # By underlying
            if underlying not in by_underlying:
                by_underlying[underlying] = {"delta": 0.0, "gamma": 0.0, "theta": 0.0,
                                              "vega": 0.0, "rho": 0.0, "notional": 0.0}
            for k in ("delta", "gamma", "theta", "vega", "rho"):
                by_underlying[underlying][k] += pg[k]
            by_underlying[underlying]["notional"] += notional

        by_strategy[sid] = {
            **strat_greeks,
            "name": strat.get("name", sid),
            "underlying": underlying,
            "status": status,
            "pnl": float(strat.get("pnl", 0) or 0),
        }

    return {
        "portfolio": portfolio,
        "by_strategy": by_strategy,
        "by_underlying": by_underlying,
        "positions_count": positions_count,
        "strategies_count": strategies_count,
    }


def _find_strike_iv(chain: dict, strike, option_type: str) -> Optional[float]:
    """
    Back-solve IV for a specific strike from the chain cache.
    Returns annualized IV (decimal) or None if data missing/invalid.
    """
    if not chain or strike is None:
        return None
    chain_data = chain.get("chain") or []
    spot = float(chain.get("spot_price", 0) or 0)
    if spot <= 0:
        return None

    # Find matching strike + type
    for opt in chain_data:
        try:
            if (int(opt.get("strike", 0)) == int(strike) and
                    str(opt.get("option_type", "")).upper() == option_type):
                ltp = float(opt.get("ltp", 0) or 0)
                if ltp <= 0:
                    return None
                # Determine expiry — use chain's expiry if available
                expiry = chain.get("expiry") or chain.get("next_expiry")
                T = time_to_expiry_years(expiry) if expiry else (7.0 / 365.0)
                iv = implied_volatility(
                    ltp, spot, float(strike), T, RISK_FREE_RATE_DEFAULT,
                    option_type, DIVIDEND_YIELD_DEFAULT,
                )
                return iv if iv > 0 else None
        except (TypeError, ValueError):
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Value at Risk (Parametric Delta-Normal)
# ─────────────────────────────────────────────────────────────────────────────


def parametric_var(
    portfolio_delta: float, spot: float, annual_vol: float,
    confidence: float = 0.95, time_horizon_days: float = 1.0,
) -> float:
    """
    Parametric VaR using delta-normal method.

    VaR = Z × σ × |Δ| × S × √(T/252)

    Returns positive number = expected MAX LOSS at confidence level.
    """
    if portfolio_delta == 0 or spot <= 0 or annual_vol <= 0:
        return 0.0

    z = Z_SCORE_99 if confidence >= 0.99 else Z_SCORE_95
    # Time scaling — use 252 (trading days) for daily vol
    time_factor = math.sqrt(time_horizon_days / TRADING_DAYS_PER_YEAR)
    return z * annual_vol * abs(portfolio_delta) * spot * time_factor


def expected_shortfall(
    portfolio_delta: float, spot: float, annual_vol: float,
    confidence: float = 0.95, time_horizon_days: float = 1.0,
) -> float:
    """
    Expected Shortfall (CVaR) — average loss in worst (1-confidence)% scenarios.
    For normal distribution: ES = σ × φ(z) / (1-α).
    """
    if portfolio_delta == 0 or spot <= 0 or annual_vol <= 0:
        return 0.0

    z = Z_SCORE_99 if confidence >= 0.99 else Z_SCORE_95
    alpha = 1.0 - confidence
    es_multiplier = _norm_pdf(z) / alpha
    time_factor = math.sqrt(time_horizon_days / TRADING_DAYS_PER_YEAR)
    return es_multiplier * annual_vol * abs(portfolio_delta) * spot * time_factor


# ─────────────────────────────────────────────────────────────────────────────
# Stress Testing — Taylor expansion
# ─────────────────────────────────────────────────────────────────────────────


# Standard stress scenarios for Indian F&O
DEFAULT_STRESS_SCENARIOS = [
    {"name": "Mild Decline",       "spot_pct": -2.0,  "iv_change_pct": +10.0},
    {"name": "Moderate Decline",   "spot_pct": -5.0,  "iv_change_pct": +25.0},
    {"name": "Crash",              "spot_pct": -10.0, "iv_change_pct": +60.0},
    {"name": "Black Swan",         "spot_pct": -15.0, "iv_change_pct": +100.0},
    {"name": "Mild Rally",         "spot_pct": +2.0,  "iv_change_pct": -5.0},
    {"name": "Strong Rally",       "spot_pct": +5.0,  "iv_change_pct": -15.0},
    {"name": "Melt-up",            "spot_pct": +10.0, "iv_change_pct": -25.0},
    {"name": "IV Spike",           "spot_pct": 0.0,   "iv_change_pct": +50.0},
    {"name": "IV Crush",           "spot_pct": 0.0,   "iv_change_pct": -30.0},
    {"name": "Overnight Gap Down", "spot_pct": -3.0,  "iv_change_pct": +40.0},
]


def stress_test_portfolio(
    portfolio_greeks: dict, spot: float, current_iv: float,
    scenarios: Optional[list[dict]] = None,
) -> dict:
    """
    Apply Taylor expansion to estimate portfolio P&L under shock scenarios:
        ΔPnL = Δ·ΔS + 0.5·Γ·ΔS² + ν·ΔIV + Θ·ΔT

    `portfolio_greeks`: output from aggregate_portfolio_greeks()['portfolio'].
    `spot`: current spot price of primary underlying (e.g., NIFTY level).
    `current_iv`: current India VIX as decimal (e.g., 0.14 for 14%).
    `scenarios`: list of {name, spot_pct, iv_change_pct, [days_decay]}.

    Returns:
        {
            'base_spot': spot,
            'base_iv_pct': current_iv * 100,
            'scenarios': [...],  # with estimated_pnl, severity, etc.
        }
    """
    scenarios = scenarios or DEFAULT_STRESS_SCENARIOS
    delta = portfolio_greeks.get("delta", 0.0)
    gamma = portfolio_greeks.get("gamma", 0.0)
    vega = portfolio_greeks.get("vega", 0.0)   # per 1% IV
    theta = portfolio_greeks.get("theta", 0.0)  # per day

    results = []
    for sc in scenarios:
        spot_pct = float(sc.get("spot_pct", 0.0))
        iv_change_pct = float(sc.get("iv_change_pct", 0.0))
        days_decay = float(sc.get("days_decay", 1.0))  # default 1 day

        delta_S = spot * (spot_pct / 100.0)
        delta_IV_abs_pct = (current_iv * 100.0) * (iv_change_pct / 100.0)  # absolute % move

        # Taylor expansion
        delta_pnl = delta * delta_S
        gamma_pnl = 0.5 * gamma * delta_S * delta_S
        vega_pnl = vega * delta_IV_abs_pct
        theta_pnl = theta * days_decay
        total_pnl = delta_pnl + gamma_pnl + vega_pnl + theta_pnl

        # Severity classification
        if total_pnl <= -200_000:
            severity = "CRITICAL"
        elif total_pnl <= -50_000:
            severity = "HIGH"
        elif total_pnl <= -10_000:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        results.append({
            "scenario": sc.get("name", "Scenario"),
            "spot_pct": spot_pct,
            "iv_change_pct": iv_change_pct,
            "days_decay": days_decay,
            "shocked_spot": round(spot + delta_S, 2),
            "delta_pnl": round(delta_pnl, 2),
            "gamma_pnl": round(gamma_pnl, 2),
            "vega_pnl": round(vega_pnl, 2),
            "theta_pnl": round(theta_pnl, 2),
            "estimated_pnl": round(total_pnl, 2),
            "delta_impact": round(delta * delta_S, 2),
            "vega_impact": round(vega * delta_IV_abs_pct, 2),
            "severity": severity,
        })

    return {
        "base_spot": round(spot, 2),
        "base_iv_pct": round(current_iv * 100.0, 2),
        "scenarios": results,
        "worst_case_pnl": min((r["estimated_pnl"] for r in results), default=0.0),
        "best_case_pnl": max((r["estimated_pnl"] for r in results), default=0.0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Margin Calculation (NSE F&O approximation)
# ─────────────────────────────────────────────────────────────────────────────


def calculate_margin(
    strategies: dict, chain_cache: dict, lot_sizes: Optional[dict] = None,
    available_capital: float = 1_000_000.0,
) -> dict:
    """
    Approximate NSE F&O margin requirement for all deployed strategies.

    For each position:
      - Buyer (BUY side): margin = premium paid * qty * lot_size
      - Seller (SELL side): margin = (SPAN_PCT + EXPOSURE_PCT) * notional + premium received

    Returns:
        {
            'total_margin_required': Rs,
            'span_margin': Rs,
            'exposure_margin': Rs,
            'premium_paid': Rs (buyers),
            'premium_received': Rs (sellers),
            'net_option_value': Rs,
            'available_margin': Rs,
            'utilization_pct': 0-100,
            'margin_by_strategy': {sid: Rs},
        }
    """
    lot_sizes = lot_sizes or {}
    span_total = 0.0
    exposure_total = 0.0
    premium_paid = 0.0
    premium_received = 0.0
    margin_by_strategy: dict = {}

    for sid, strat in (strategies or {}).items():
        status = str(strat.get("status", "")).upper()
        if status not in ("RUNNING", "ENTERED") or not strat.get("entered", False):
            if status != "ENTERED":
                continue

        underlying = (strat.get("underlying") or "NIFTY").upper()
        chain = chain_cache.get(underlying) or {}
        lot_size = int(lot_sizes.get(underlying, chain.get("lot_size", 1)) or 1)

        strat_margin = 0.0
        for pos in strat.get("positions", []):
            side = str(pos.get("side", "BUY")).upper()
            qty = float(pos.get("qty", 0) or 0)
            strike = float(pos.get("strike", 0) or 0)
            premium = float(pos.get("entry_price", 0) or 0)

            notional = abs(qty) * lot_size * strike
            prem_value = abs(qty) * lot_size * premium

            if side in ("SELL", "SHORT"):
                # Option writer: SPAN + Exposure + premium hedge
                span = notional * SPAN_NOTIONAL_PCT
                exp = notional * EXPOSURE_NOTIONAL_PCT
                span_total += span
                exposure_total += exp
                premium_received += prem_value
                strat_margin += (span + exp)
            else:
                # Option buyer: margin = premium paid
                premium_paid += prem_value
                strat_margin += prem_value

        margin_by_strategy[sid] = round(strat_margin, 2)

    total_margin = span_total + exposure_total + premium_paid
    available = max(0.0, available_capital - total_margin)
    utilization = (total_margin / available_capital * 100.0) if available_capital > 0 else 0.0

    return {
        "total_margin_required": round(total_margin, 2),
        "span_margin": round(span_total, 2),
        "exposure_margin": round(exposure_total, 2),
        "premium_paid": round(premium_paid, 2),
        "premium_received": round(premium_received, 2),
        "net_option_value": round(premium_paid - premium_received, 2),
        "available_margin": round(available, 2),
        "utilization_pct": round(utilization, 2),
        "margin_by_strategy": margin_by_strategy,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Risk Limit Checker
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LimitBreach:
    """A single risk limit breach."""
    limit_name: str
    current_value: float
    limit_value: float
    utilization_pct: float
    severity: str   # 'WARN' (80-99%) or 'BREACH' (100%+)
    message: str


def check_risk_limits(
    metrics: dict, limits: Optional[dict] = None,
) -> list[LimitBreach]:
    """
    Check current portfolio metrics against configured limits.

    `metrics` dict expected keys (any subset ok):
        daily_loss_used, current_drawdown_pct, total_margin_used,
        net_delta, net_gamma, net_vega, net_theta,
        open_strategies_count, position_concentration_pct,
        consecutive_losses,

    Returns list of LimitBreach. Severity = 'WARN' if 80-99% utilization,
    'BREACH' if 100%+.
    """
    limits = {**DEFAULT_RISK_LIMITS, **(limits or {})}
    breaches: list[LimitBreach] = []
    alert_pct = limits["alert_threshold_pct"]

    def _check(name: str, current: float, limit_val: float, direction: str = "above"):
        if limit_val == 0:
            return
        if direction == "above":
            util = abs(current) / abs(limit_val)
        else:
            util = abs(current) / abs(limit_val)
        util_pct = util * 100.0
        if util >= 1.0:
            severity = "BREACH"
        elif util >= alert_pct:
            severity = "WARN"
        else:
            return
        breaches.append(LimitBreach(
            limit_name=name,
            current_value=round(current, 4),
            limit_value=round(limit_val, 4),
            utilization_pct=round(util_pct, 2),
            severity=severity,
            message=f"{name}: {round(util_pct, 1)}% of limit (current {round(current, 2)}, "
                    f"limit {round(limit_val, 2)})",
        ))

    # Daily loss (loss is positive number when used; limit is positive)
    if "daily_loss_used" in metrics:
        _check("max_daily_loss", abs(metrics["daily_loss_used"]),
               limits["max_daily_loss"])

    # Drawdown
    if "current_drawdown_pct" in metrics:
        _check("max_drawdown_pct", abs(metrics["current_drawdown_pct"]),
               limits["max_drawdown_pct"])

    # Greeks
    if "net_delta" in metrics:
        _check("max_delta_exposure", metrics["net_delta"], limits["max_delta_exposure"])
    if "net_gamma" in metrics:
        _check("max_gamma_exposure", metrics["net_gamma"], limits["max_gamma_exposure"])
    if "net_vega" in metrics:
        _check("max_vega_exposure", metrics["net_vega"], limits["max_vega_exposure"])
    # Theta is negative when working against us; we check the magnitude
    if "net_theta" in metrics:
        theta = metrics["net_theta"]
        if theta < 0:  # decay against us
            _check("max_theta_decay", theta, limits["max_theta_decay"])

    # Open strategies count
    if "open_strategies_count" in metrics:
        _check("max_open_strategies", metrics["open_strategies_count"],
               limits["max_open_strategies"])

    # Concentration
    if "position_concentration_pct" in metrics:
        _check("position_concentration_limit_pct",
               metrics["position_concentration_pct"],
               limits["position_concentration_limit_pct"] * 100.0)

    # Consecutive losses
    if "consecutive_losses" in metrics:
        _check("consecutive_losses_limit", metrics["consecutive_losses"],
               limits["consecutive_losses_limit"])

    return breaches


def should_auto_kill(breaches: list[LimitBreach], limits: Optional[dict] = None) -> bool:
    """
    Decide if auto-kill should trigger based on breaches and config.

    Conservative mode: auto-kill if ANY of:
      - max_daily_loss BREACHED (100%+)
      - max_drawdown_pct BREACHED
    """
    if not breaches:
        return False
    auto_kill_limits = {"max_daily_loss", "max_drawdown_pct"}
    for b in breaches:
        if b.severity == "BREACH" and b.limit_name in auto_kill_limits:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Drawdown Tracker
# ─────────────────────────────────────────────────────────────────────────────


def calculate_drawdown(equity_curve: list[float]) -> dict:
    """
    Compute current and max drawdown from an equity time series.

    `equity_curve`: list of total equity values (most recent LAST).

    Returns:
        {
            'peak_equity': float,
            'current_equity': float,
            'drawdown_amount': float,  # positive = loss from peak
            'drawdown_pct': float,     # 0-100
            'max_drawdown_pct': float, # worst historical drawdown
            'duration_days': int,      # days since peak
        }
    """
    if not equity_curve:
        return {
            "peak_equity": 0.0, "current_equity": 0.0,
            "drawdown_amount": 0.0, "drawdown_pct": 0.0,
            "max_drawdown_pct": 0.0, "duration_days": 0,
        }

    current = equity_curve[-1]
    peak = equity_curve[0]
    max_dd = 0.0
    peak_idx = 0
    running_peak = equity_curve[0]
    running_peak_idx = 0

    for i, eq in enumerate(equity_curve):
        if eq > running_peak:
            running_peak = eq
            running_peak_idx = i
        if eq > peak:
            peak = eq
            peak_idx = i
        if running_peak > 0:
            dd = (running_peak - eq) / running_peak
            if dd > max_dd:
                max_dd = dd

    dd_amount = max(0.0, peak - current)
    dd_pct = (dd_amount / peak * 100.0) if peak > 0 else 0.0
    duration = len(equity_curve) - 1 - peak_idx  # bars since peak

    return {
        "peak_equity": round(peak, 2),
        "current_equity": round(current, 2),
        "drawdown_amount": round(dd_amount, 2),
        "drawdown_pct": round(dd_pct, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "duration_days": duration,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Concentration analyzer
# ─────────────────────────────────────────────────────────────────────────────


def position_concentration(strategies: dict, chain_cache: dict,
                            lot_sizes: Optional[dict] = None) -> dict:
    """
    Compute % of total notional in each underlying.

    Returns:
        {
            'total_notional': float,
            'by_underlying': {'NIFTY': {'notional': Rs, 'pct': 0-100}, ...},
            'max_concentration_pct': float,
            'max_concentration_underlying': str,
        }
    """
    lot_sizes = lot_sizes or {}
    by_underlying: dict = {}
    total = 0.0

    for sid, strat in (strategies or {}).items():
        status = str(strat.get("status", "")).upper()
        if status not in ("RUNNING", "ENTERED") or not strat.get("entered", False):
            if status != "ENTERED":
                continue

        underlying = (strat.get("underlying") or "NIFTY").upper()
        chain = chain_cache.get(underlying) or {}
        lot_size = int(lot_sizes.get(underlying, chain.get("lot_size", 1)) or 1)

        for pos in strat.get("positions", []):
            qty = float(pos.get("qty", 0) or 0)
            strike = float(pos.get("strike", 0) or 0)
            notional = abs(qty) * lot_size * strike
            by_underlying.setdefault(underlying, 0.0)
            by_underlying[underlying] += notional
            total += notional

    pct_by = {}
    max_pct = 0.0
    max_underlying = "—"
    for u, n in by_underlying.items():
        pct = (n / total * 100.0) if total > 0 else 0.0
        pct_by[u] = {"notional": round(n, 2), "pct": round(pct, 2)}
        if pct > max_pct:
            max_pct = pct
            max_underlying = u

    return {
        "total_notional": round(total, 2),
        "by_underlying": pct_by,
        "max_concentration_pct": round(max_pct, 2),
        "max_concentration_underlying": max_underlying,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Self-test (run as module)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick sanity check
    print("=== Risk Engine Self-Test ===\n")

    # 1. Black-Scholes
    price = bs_price(24800, 24800, 7/365, 0.065, 0.15, "CE")
    print(f"ATM Call (NIFTY 24800, 7DTE, 15% IV): Rs{price:.2f}")

    greeks = bs_greeks(24800, 24800, 7/365, 0.065, 0.15, "CE")
    print(f"  delta={greeks['delta']:.4f}  gamma={greeks['gamma']:.6f}  "
          f"theta={greeks['theta']:.2f}  vega={greeks['vega']:.2f}")

    # 2. IV back-solve
    iv = implied_volatility(165.0, 24800, 24800, 7/365, 0.065, "CE")
    print(f"\nIV back-solve (Rs165 premium): {iv*100:.2f}%")

    # 3. VaR
    var95 = parametric_var(0.5, 24800, 0.15, 0.95, 1)
    print(f"\n1-day 95% VaR (delta=0.5): Rs{var95:,.2f}")

    # 4. Stress test
    pf_greeks = {"delta": 0.5, "gamma": 0.01, "theta": -250.0, "vega": 1500.0}
    stress = stress_test_portfolio(pf_greeks, 24800, 0.14)
    print(f"\nStress test - worst case: Rs{stress['worst_case_pnl']:,.2f}")
    for s in stress['scenarios'][:3]:
        print(f"  {s['scenario']:20s} -> Rs{s['estimated_pnl']:>10,.2f}  ({s['severity']})")

    # 5. Drawdown
    eq_curve = [1_000_000, 1_050_000, 1_080_000, 1_020_000, 980_000, 1_010_000]
    dd = calculate_drawdown(eq_curve)
    print(f"\nDrawdown: {dd['drawdown_pct']:.2f}% (peak Rs{dd['peak_equity']:,.0f})")

    print("\nAll self-tests passed")
