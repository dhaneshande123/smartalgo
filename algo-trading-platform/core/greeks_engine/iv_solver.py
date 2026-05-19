"""Implied Volatility solver using multiple numerical methods.

Provides a hybrid IV solver that combines Newton-Raphson (fast convergence)
with Brent's method (guaranteed convergence) to robustly extract implied
volatility from market option prices.

Also includes IV percentile and IV rank calculations for volatility analytics.
"""

from __future__ import annotations

import math
from typing import Optional

from scipy.optimize import brentq
from scipy.stats import norm

from .pricing import BlackScholes


class IVSolver:
    """Computes implied volatility from market prices.

    Methods:
        1. **Newton-Raphson** -- fast quadratic convergence using BS vega as
           the derivative.  May fail for extreme strikes or near-zero vega.
        2. **Brent's method** -- robust bracketed root-finding via
           ``scipy.optimize.brentq``.  Always converges within the vol bounds.
        3. **Hybrid** (default) -- attempts Newton-Raphson first; falls back
           to Brent's if Newton does not converge within ``max_iterations``.

    The solver also provides convenience methods for IV percentile and IV rank
    which are commonly used in volatility analysis dashboards.
    """

    # Absolute bounds on implied volatility
    VOL_MIN: float = 0.001    # 0.1%
    VOL_MAX: float = 5.0      # 500%

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def solve(
        market_price: float,
        spot: float,
        strike: float,
        time_to_expiry: float,
        rate: float,
        option_type: str = "CE",
        dividend_yield: float = 0.0,
        method: str = "hybrid",
        initial_vol: float = 0.2,
        max_iterations: int = 100,
        tolerance: float = 1e-8,
    ) -> float | None:
        """Solve for implied volatility.

        Given a market-observed option price and the other BSM inputs, find the
        volatility ``sigma`` such that ``BS(sigma) == market_price``.

        Args:
            market_price: Observed market price of the option.
            spot: Current underlying price.
            strike: Strike price.
            time_to_expiry: Time to expiry in years.
            rate: Risk-free rate (annualised).
            option_type: ``"CE"`` for call, ``"PE"`` for put.
            dividend_yield: Continuous dividend yield.
            method: Solver method -- ``"newton"``, ``"brent"``, or
                ``"hybrid"`` (default).
            initial_vol: Starting guess for Newton-Raphson (default 0.2).
            max_iterations: Maximum Newton-Raphson iterations.
            tolerance: Convergence tolerance on the price difference.

        Returns:
            Implied volatility as a decimal (e.g. 0.15 for 15 %), or
            ``None`` if no solution could be found.
        """
        is_call = option_type.upper() == "CE"
        S = spot
        K = strike
        T = time_to_expiry
        r = rate
        q = dividend_yield

        # ------------------------------------------------------------------
        # Quick sanity checks
        # ------------------------------------------------------------------
        if market_price <= 0.0:
            return None
        if T <= 0.0:
            return None  # Cannot solve IV at expiry
        if S <= 0.0 or K <= 0.0:
            return None

        # Check that market price is above intrinsic value
        df_r = math.exp(-r * T)
        df_q = math.exp(-q * T)
        forward = S * math.exp((r - q) * T)

        if is_call:
            intrinsic = max(S * df_q - K * df_r, 0.0)
            # Upper bound: call price <= S * e^{-qT}
            upper_bound = S * df_q
        else:
            intrinsic = max(K * df_r - S * df_q, 0.0)
            # Upper bound: put price <= K * e^{-rT}
            upper_bound = K * df_r

        if market_price < intrinsic - tolerance:
            # Price below intrinsic -- no valid IV
            return None
        if market_price > upper_bound + tolerance:
            # Price above theoretical maximum -- no valid IV
            return None

        # ------------------------------------------------------------------
        # Dispatch to solver method
        # ------------------------------------------------------------------
        method = method.lower().strip()

        if method == "newton":
            return IVSolver._newton_raphson(
                market_price, S, K, T, r, option_type, q,
                initial_vol, max_iterations, tolerance,
            )
        elif method == "brent":
            return IVSolver._brent(
                market_price, S, K, T, r, option_type, q, tolerance,
            )
        elif method == "hybrid":
            # Try Newton first
            result = IVSolver._newton_raphson(
                market_price, S, K, T, r, option_type, q,
                initial_vol, max_iterations, tolerance,
            )
            if result is not None:
                return result
            # Fall back to Brent's
            return IVSolver._brent(
                market_price, S, K, T, r, option_type, q, tolerance,
            )
        else:
            raise ValueError(
                f"Unknown method '{method}'. Use 'newton', 'brent', or 'hybrid'."
            )

    # ------------------------------------------------------------------
    # Newton-Raphson
    # ------------------------------------------------------------------

    @staticmethod
    def _newton_raphson(
        market_price: float,
        spot: float,
        strike: float,
        T: float,
        r: float,
        option_type: str,
        q: float,
        initial_vol: float,
        max_iter: int,
        tol: float,
    ) -> float | None:
        """Newton-Raphson solver for implied volatility.

        The iteration is:

        .. math::
            \\sigma_{n+1} = \\sigma_n
            - \\frac{\\text{BS}(\\sigma_n) - P_{\\text{mkt}}}{\\text{vega}(\\sigma_n)}

        where vega is the *annual* (un-scaled) BS vega so that the units
        match the price difference in the numerator.

        Guards:
            - If vega drops below ``1e-12`` the method switches to bisection
              (returns ``None`` to trigger fallback in hybrid mode).
            - The volatility is clamped to ``[VOL_MIN, VOL_MAX]`` at each step.

        Returns:
            IV as a decimal, or ``None`` if convergence fails.
        """
        sigma = initial_vol

        # Clamp starting value
        sigma = max(IVSolver.VOL_MIN, min(sigma, IVSolver.VOL_MAX))

        for _ in range(max_iter):
            result = BlackScholes.price(
                spot, strike, T, r, sigma, option_type, q
            )
            bs_price = result.price
            diff = bs_price - market_price

            if abs(diff) < tol:
                return sigma

            # Annual vega (un-scaled) = vega_pct * 100
            vega_annual = result.vega * 100.0

            if abs(vega_annual) < 1e-12:
                # Vega too small -- Newton step unreliable
                return None

            sigma -= diff / vega_annual

            # Clamp to valid range
            sigma = max(IVSolver.VOL_MIN, min(sigma, IVSolver.VOL_MAX))

        # Did not converge
        return None

    # ------------------------------------------------------------------
    # Brent's method
    # ------------------------------------------------------------------

    @staticmethod
    def _brent(
        market_price: float,
        spot: float,
        strike: float,
        T: float,
        r: float,
        option_type: str,
        q: float,
        tol: float,
    ) -> float | None:
        """Brent's method solver for implied volatility.

        Uses ``scipy.optimize.brentq`` on the objective
        ``f(sigma) = BS(sigma) - market_price`` over the interval
        ``[VOL_MIN, VOL_MAX]``.

        Returns:
            IV as a decimal, or ``None`` if no root exists in the bracket.
        """

        def objective(sigma: float) -> float:
            res = BlackScholes.price(
                spot, strike, T, r, sigma, option_type, q
            )
            return res.price - market_price

        # Verify that a sign change exists across the bracket
        try:
            f_low = objective(IVSolver.VOL_MIN)
            f_high = objective(IVSolver.VOL_MAX)
        except (ValueError, OverflowError, ZeroDivisionError):
            return None

        # If both endpoints have the same sign, no root in the interval
        if f_low * f_high > 0.0:
            return None

        try:
            iv = brentq(
                objective,
                IVSolver.VOL_MIN,
                IVSolver.VOL_MAX,
                xtol=tol,
                rtol=tol,
                maxiter=200,
            )
            return float(iv)
        except (ValueError, RuntimeError):
            return None

    # ------------------------------------------------------------------
    # Volatility analytics
    # ------------------------------------------------------------------

    @staticmethod
    def iv_percentile(current_iv: float, historical_ivs: list[float]) -> float:
        """IV percentile: percentage of historical readings below current IV.

        This answers the question *"What fraction of the time was IV lower
        than it is today?"*  A percentile of 90 means the current IV is
        higher than 90 % of the historical observations.

        Args:
            current_iv: Current implied volatility (decimal).
            historical_ivs: Historical IV observations (e.g. 252 trading
                days).

        Returns:
            Percentile from 0 to 100.  Returns 0.0 if the historical list
            is empty.
        """
        if not historical_ivs:
            return 0.0
        count_below = sum(1 for iv in historical_ivs if iv < current_iv)
        return (count_below / len(historical_ivs)) * 100.0

    @staticmethod
    def iv_rank(current_iv: float, historical_ivs: list[float]) -> float:
        """IV Rank: normalised position within the historical min-max range.

        .. math::
            \\text{IV Rank} = \\frac{\\text{current} - \\min}{\\max - \\min}
            \\times 100

        Args:
            current_iv: Current implied volatility (decimal).
            historical_ivs: Historical IV observations.

        Returns:
            Rank from 0 to 100.  Returns 0.0 if the historical list is
            empty or if max equals min (flat IV history).
        """
        if not historical_ivs:
            return 0.0
        iv_min = min(historical_ivs)
        iv_max = max(historical_ivs)
        if iv_max == iv_min:
            return 0.0
        rank = (current_iv - iv_min) / (iv_max - iv_min) * 100.0
        # Clamp to [0, 100] in case current IV is outside historical range
        return max(0.0, min(100.0, rank))
