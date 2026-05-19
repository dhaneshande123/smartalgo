"""Options pricing models -- Black-Scholes, Binomial Tree, and Monte Carlo.

Provides three pricing engines for option valuation:
- BlackScholes: Analytical European option pricing with full greeks through 3rd order.
- BinomialTree: CRR binomial tree for American options with early exercise.
- MonteCarlo: Monte Carlo simulation with antithetic variates for path-dependent options.

All models return a ``PricingResult`` dataclass containing the option price and
a comprehensive set of greeks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class PricingResult:
    """Result from an option pricing calculation."""

    price: float
    delta: float
    gamma: float
    theta: float   # per calendar day
    vega: float    # per 1% vol move
    rho: float     # per 1% rate move
    # Higher-order greeks
    charm: float = 0.0    # dDelta/dT
    vanna: float = 0.0    # dDelta/dVol
    volga: float = 0.0    # dVega/dVol (vomma)
    speed: float = 0.0    # dGamma/dSpot
    zomma: float = 0.0    # dGamma/dVol
    color: float = 0.0    # dGamma/dT
    lambda_: float = 0.0  # leverage ratio (omega)


# ---------------------------------------------------------------------------
# Black-Scholes-Merton
# ---------------------------------------------------------------------------

class BlackScholes:
    """Black-Scholes-Merton model for European options.

    Used for NIFTY / BANKNIFTY index options which are European-style.

    All greeks through 3rd order are computed analytically using the
    generalized BSM formula with continuous dividend yield.

    Conventions:
        - ``theta`` is returned as *daily* theta (annual theta / 365).
        - ``vega`` is returned per **1 %** move in implied volatility.
        - ``rho`` is returned per **1 %** move in the risk-free rate.
    """

    # ----- helper functions ------------------------------------------------

    @staticmethod
    def d1(
        spot: float,
        strike: float,
        T: float,
        r: float,
        sigma: float,
        q: float = 0.0,
    ) -> float:
        """Compute d1 of the BSM formula.

        .. math::
            d_1 = \\frac{\\ln(S/K) + (r - q + \\sigma^2/2)\\,T}{\\sigma\\sqrt{T}}
        """
        if T <= 0.0 or sigma <= 0.0:
            # At expiry or zero vol the standard formula is undefined;
            # return +/-inf based on moneyness so N(d1) collapses to 0 or 1.
            if spot > strike:
                return float("inf")
            elif spot < strike:
                return float("-inf")
            return 0.0
        sqrt_T = math.sqrt(T)
        return (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (
            sigma * sqrt_T
        )

    @staticmethod
    def d2(
        spot: float,
        strike: float,
        T: float,
        r: float,
        sigma: float,
        q: float = 0.0,
    ) -> float:
        """Compute d2 of the BSM formula.

        .. math::
            d_2 = d_1 - \\sigma\\sqrt{T}
        """
        if T <= 0.0 or sigma <= 0.0:
            return BlackScholes.d1(spot, strike, T, r, sigma, q)
        sqrt_T = math.sqrt(T)
        d1_val = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (
            sigma * sqrt_T
        )
        return d1_val - sigma * sqrt_T

    # ----- main pricing method ---------------------------------------------

    @staticmethod
    def price(
        spot: float,
        strike: float,
        time_to_expiry: float,
        rate: float,
        volatility: float,
        option_type: str = "CE",
        dividend_yield: float = 0.0,
    ) -> PricingResult:
        """Compute option price and all greeks analytically.

        Uses the generalized BSM with continuous dividend yield *q*.

        Args:
            spot: Current price of the underlying.
            strike: Strike price.
            time_to_expiry: Years to expiry (e.g. ``7/365`` for 7 days).
            rate: Risk-free rate (annualised, e.g. 0.07 for 7 %).
            volatility: Implied volatility (annualised, e.g. 0.15 for 15 %).
            option_type: ``"CE"`` for call, ``"PE"`` for put.
            dividend_yield: Continuous dividend yield.

        Returns:
            A :class:`PricingResult` with price and all greeks.
        """
        is_call = option_type.upper() == "CE"
        S = spot
        K = strike
        T = time_to_expiry
        r = rate
        sigma = volatility
        q = dividend_yield

        # ------------------------------------------------------------------
        # Edge case: at or past expiry
        # ------------------------------------------------------------------
        if T <= 0.0:
            intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
            delta = 0.0
            if S > K and is_call:
                delta = 1.0
            elif S < K and not is_call:
                delta = -1.0
            return PricingResult(
                price=intrinsic,
                delta=delta,
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                rho=0.0,
            )

        # ------------------------------------------------------------------
        # Edge case: zero volatility
        # ------------------------------------------------------------------
        if sigma <= 0.0:
            df_r = math.exp(-r * T)
            df_q = math.exp(-q * T)
            forward = S * math.exp((r - q) * T)
            if is_call:
                intrinsic = max(forward - K, 0.0) * df_r
                delta = df_q if forward > K else 0.0
                rho_val = (K * T * df_r / 100.0) if forward > K else 0.0
            else:
                intrinsic = max(K - forward, 0.0) * df_r
                delta = -df_q if forward < K else 0.0
                rho_val = (-K * T * df_r / 100.0) if forward < K else 0.0
            return PricingResult(
                price=intrinsic,
                delta=delta,
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                rho=rho_val,
            )

        # ------------------------------------------------------------------
        # Standard BSM computation
        # ------------------------------------------------------------------
        sqrt_T = math.sqrt(T)
        d1_val = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (
            sigma * sqrt_T
        )
        d2_val = d1_val - sigma * sqrt_T

        df_q = math.exp(-q * T)  # discount factor for dividend yield
        df_r = math.exp(-r * T)  # discount factor for risk-free rate

        Nd1 = norm.cdf(d1_val)
        Nd2 = norm.cdf(d2_val)
        Nmd1 = norm.cdf(-d1_val)
        Nmd2 = norm.cdf(-d2_val)
        nd1 = norm.pdf(d1_val)  # standard normal density at d1

        # --- Option price ---
        if is_call:
            price_val = S * df_q * Nd1 - K * df_r * Nd2
        else:
            price_val = K * df_r * Nmd2 - S * df_q * Nmd1

        # Clamp to avoid tiny negatives from floating point
        price_val = max(price_val, 0.0)

        # ------------------------------------------------------------------
        # First-order greeks
        # ------------------------------------------------------------------
        if is_call:
            delta = df_q * Nd1
        else:
            delta = df_q * (Nd1 - 1.0)

        gamma = df_q * nd1 / (S * sigma * sqrt_T)

        # Annual theta (call)
        theta_annual_common = -(S * sigma * df_q * nd1) / (2.0 * sqrt_T)
        if is_call:
            theta_annual = (
                theta_annual_common
                - r * K * df_r * Nd2
                + q * S * df_q * Nd1
            )
        else:
            theta_annual = (
                theta_annual_common
                + r * K * df_r * Nmd2
                - q * S * df_q * Nmd1
            )
        theta_daily = theta_annual / 365.0

        # Vega: per 1% vol move = vega_annual / 100
        vega_annual = S * df_q * nd1 * sqrt_T
        vega_pct = vega_annual / 100.0

        # Rho: per 1% rate move
        if is_call:
            rho_pct = K * T * df_r * Nd2 / 100.0
        else:
            rho_pct = -K * T * df_r * Nmd2 / 100.0

        # ------------------------------------------------------------------
        # Second-order greeks
        # ------------------------------------------------------------------

        # Charm (delta decay): dDelta/dT
        # For a call:
        #   charm = -df_q * [ nd1 * (2*(r-q)*T - d2*sigma*sqrt_T) / (2*T*sigma*sqrt_T) + q*N(d1) ]
        # For a put, add q instead of subtracting (sign flip on the q*N term):
        charm_common = df_q * nd1 * (
            2.0 * (r - q) * T - d2_val * sigma * sqrt_T
        ) / (2.0 * T * sigma * sqrt_T)
        if is_call:
            charm = -charm_common - q * df_q * Nd1
        else:
            charm = -charm_common + q * df_q * Nmd1

        # Vanna: dDelta/dVol  =  -df_q * nd1 * d2 / sigma
        vanna = -df_q * nd1 * d2_val / sigma

        # Volga (Vomma): dVega/dVol = vega * d1 * d2 / sigma
        volga = vega_annual * d1_val * d2_val / sigma

        # ------------------------------------------------------------------
        # Third-order greeks
        # ------------------------------------------------------------------

        # Speed: dGamma/dSpot = -gamma/S * (d1/(sigma*sqrt_T) + 1)
        speed = -gamma / S * (d1_val / (sigma * sqrt_T) + 1.0)

        # Zomma: dGamma/dVol = gamma * (d1*d2 - 1) / sigma
        zomma = gamma * (d1_val * d2_val - 1.0) / sigma

        # Color: dGamma/dT
        color = -df_q * nd1 / (2.0 * S * T * sigma * sqrt_T) * (
            2.0 * q * T
            + 1.0
            + d1_val
            * (2.0 * (r - q) * T - d2_val * sigma * sqrt_T)
            / (sigma * sqrt_T)
        )

        # Lambda (leverage / omega)
        lambda_val = delta * S / price_val if price_val > 1e-15 else 0.0

        return PricingResult(
            price=price_val,
            delta=delta,
            gamma=gamma,
            theta=theta_daily,
            vega=vega_pct,
            rho=rho_pct,
            charm=charm,
            vanna=vanna,
            volga=volga,
            speed=speed,
            zomma=zomma,
            color=color,
            lambda_=lambda_val,
        )


# ---------------------------------------------------------------------------
# Binomial Tree (CRR)
# ---------------------------------------------------------------------------

class BinomialTree:
    """Cox-Ross-Rubinstein binomial tree for American options.

    Used for stock options on NSE which may be American-style.
    Supports early exercise pricing.

    Greeks are extracted via finite differences on the first few tree nodes.
    """

    @staticmethod
    def price(
        spot: float,
        strike: float,
        time_to_expiry: float,
        rate: float,
        volatility: float,
        option_type: str = "CE",
        steps: int = 200,
        is_american: bool = True,
        dividend_yield: float = 0.0,
    ) -> PricingResult:
        """Price an option using the CRR binomial tree.

        For American options the algorithm checks early exercise at every node.
        Greeks (delta, gamma, theta) are computed via finite differences from
        the first two time-steps of the tree.

        Args:
            spot: Current underlying price.
            strike: Strike price.
            time_to_expiry: Time to expiry in years.
            rate: Risk-free rate (annualised).
            volatility: Implied volatility (annualised).
            option_type: ``"CE"`` for call, ``"PE"`` for put.
            steps: Number of time steps in the tree.
            is_american: If ``True``, allow early exercise.
            dividend_yield: Continuous dividend yield.

        Returns:
            A :class:`PricingResult` with price, delta, gamma, theta.
            Vega and rho are computed by central finite differences (bump
            and re-price).
        """
        is_call = option_type.upper() == "CE"
        S = spot
        K = strike
        T = time_to_expiry
        r = rate
        sigma = volatility
        q = dividend_yield

        # Edge case: expiry
        if T <= 0.0:
            intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
            delta = 0.0
            if S > K and is_call:
                delta = 1.0
            elif S < K and not is_call:
                delta = -1.0
            return PricingResult(
                price=intrinsic, delta=delta, gamma=0.0,
                theta=0.0, vega=0.0, rho=0.0,
            )

        # Edge case: zero vol -- behaves like forward
        if sigma <= 0.0:
            return BlackScholes.price(S, K, T, r, 0.0, option_type, q)

        dt = T / steps
        u = math.exp(sigma * math.sqrt(dt))
        d = 1.0 / u
        erdt = math.exp((r - q) * dt)
        p = (erdt - d) / (u - d)

        # Probability must be in (0, 1) for the tree to be valid
        if not (0.0 < p < 1.0):
            # Fall back to many more steps or BS
            return BlackScholes.price(S, K, T, r, sigma, option_type, q)

        disc = math.exp(-r * dt)

        # ------------------------------------------------------------------
        # Build terminal payoff layer using NumPy for speed
        # ------------------------------------------------------------------
        js = np.arange(steps + 1, dtype=np.float64)
        spot_prices = S * (u ** (steps - js)) * (d ** js)

        if is_call:
            option_values = np.maximum(spot_prices - K, 0.0)
        else:
            option_values = np.maximum(K - spot_prices, 0.0)

        # ------------------------------------------------------------------
        # Backward induction -- save layers 2, 1, 0 for greek extraction
        # ------------------------------------------------------------------
        saved_layers: dict[int, np.ndarray] = {}

        for i in range(steps - 1, -1, -1):
            option_values = disc * (
                p * option_values[:-1] + (1.0 - p) * option_values[1:]
            )
            if is_american:
                js_i = np.arange(i + 1, dtype=np.float64)
                spots_i = S * (u ** (i - js_i)) * (d ** js_i)
                if is_call:
                    exercise = np.maximum(spots_i - K, 0.0)
                else:
                    exercise = np.maximum(K - spots_i, 0.0)
                option_values = np.maximum(option_values, exercise)
            if i <= 2:
                saved_layers[i] = option_values.copy()

        price_val = float(option_values[0])

        # ------------------------------------------------------------------
        # Greeks from finite differences
        # ------------------------------------------------------------------
        f = saved_layers  # shorthand

        # Layer-1 values
        f10 = float(f[1][0])  # S*u
        f11 = float(f[1][1])  # S*d

        S_u = S * u
        S_d = S * d

        delta = (f10 - f11) / (S_u - S_d)

        # Layer-2 values for gamma
        f20 = float(f[2][0])  # S*u^2
        f21 = float(f[2][1])  # S*u*d = S
        f22 = float(f[2][2])  # S*d^2

        S_uu = S * u * u
        S_ud = S  # u*d = 1
        S_dd = S * d * d

        delta_up = (f20 - f21) / (S_uu - S_ud)
        delta_down = (f21 - f22) / (S_ud - S_dd)
        gamma = (delta_up - delta_down) / (0.5 * (S_uu - S_dd))

        # Theta from tree: price at t=2*dt vs t=0, centred on the middle node
        theta_annual = (f21 - price_val) / (2.0 * dt)
        theta_daily = theta_annual / 365.0

        # ------------------------------------------------------------------
        # Vega and Rho via bump-and-reprice (central differences)
        # ------------------------------------------------------------------
        dsigma = 0.001  # 0.1% bump
        dr = 0.0001     # 1 bp bump

        price_up_v = BinomialTree._quick_price(
            S, K, T, r, sigma + dsigma, is_call, steps, is_american, q
        )
        price_down_v = BinomialTree._quick_price(
            S, K, T, r, sigma - dsigma, is_call, steps, is_american, q
        )
        # vega per 1% vol move
        vega_pct = (price_up_v - price_down_v) / (2.0 * dsigma) / 100.0

        price_up_r = BinomialTree._quick_price(
            S, K, T, r + dr, sigma, is_call, steps, is_american, q
        )
        price_down_r = BinomialTree._quick_price(
            S, K, T, r - dr, sigma, is_call, steps, is_american, q
        )
        # rho per 1% rate move
        rho_pct = (price_up_r - price_down_r) / (2.0 * dr) / 100.0

        return PricingResult(
            price=price_val,
            delta=delta,
            gamma=gamma,
            theta=theta_daily,
            vega=vega_pct,
            rho=rho_pct,
        )

    # ------------------------------------------------------------------
    # Internal helper: fast price-only tree (no greek extraction)
    # ------------------------------------------------------------------

    @staticmethod
    def _quick_price(
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        is_call: bool,
        steps: int,
        is_american: bool,
        q: float,
    ) -> float:
        """Run a binomial tree and return only the root price (no greeks)."""
        if sigma <= 0.0:
            res = BlackScholes.price(
                S, K, T, r, 0.0, "CE" if is_call else "PE", q
            )
            return res.price

        dt = T / steps
        u = math.exp(sigma * math.sqrt(dt))
        d = 1.0 / u
        erdt = math.exp((r - q) * dt)
        p = (erdt - d) / (u - d)

        if not (0.0 < p < 1.0):
            res = BlackScholes.price(
                S, K, T, r, sigma, "CE" if is_call else "PE", q
            )
            return res.price

        disc = math.exp(-r * dt)

        js = np.arange(steps + 1, dtype=np.float64)
        spots = S * (u ** (steps - js)) * (d ** js)

        if is_call:
            vals = np.maximum(spots - K, 0.0)
        else:
            vals = np.maximum(K - spots, 0.0)

        for i in range(steps - 1, -1, -1):
            vals = disc * (p * vals[:-1] + (1.0 - p) * vals[1:])
            if is_american:
                js_i = np.arange(i + 1, dtype=np.float64)
                spots_i = S * (u ** (i - js_i)) * (d ** js_i)
                if is_call:
                    exercise = np.maximum(spots_i - K, 0.0)
                else:
                    exercise = np.maximum(K - spots_i, 0.0)
                vals = np.maximum(vals, exercise)

        return float(vals[0])


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

class MonteCarlo:
    """Monte Carlo pricing for exotic / path-dependent options.

    Uses geometric Brownian motion with **antithetic variates** for variance
    reduction.  Greeks are computed via bump-and-reprice (central finite
    differences) which re-uses the same random draws for consistency.
    """

    @staticmethod
    def price(
        spot: float,
        strike: float,
        time_to_expiry: float,
        rate: float,
        volatility: float,
        option_type: str = "CE",
        num_paths: int = 50_000,
        num_steps: int = 252,
        dividend_yield: float = 0.0,
        seed: int | None = None,
    ) -> PricingResult:
        """Price a European option via Monte Carlo with antithetic variates.

        Args:
            spot: Current underlying price.
            strike: Strike price.
            time_to_expiry: Time to expiry in years.
            rate: Risk-free rate (annualised).
            volatility: Implied volatility (annualised).
            option_type: ``"CE"`` for call, ``"PE"`` for put.
            num_paths: Number of simulation paths (half will be antithetic).
            num_steps: Number of time steps per path.
            dividend_yield: Continuous dividend yield.
            seed: Optional random seed for reproducibility.

        Returns:
            A :class:`PricingResult` with price and numerically estimated
            greeks.
        """
        is_call = option_type.upper() == "CE"
        S = spot
        K = strike
        T = time_to_expiry
        r = rate
        sigma = volatility
        q = dividend_yield

        # Edge cases
        if T <= 0.0:
            intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
            delta = 0.0
            if S > K and is_call:
                delta = 1.0
            elif S < K and not is_call:
                delta = -1.0
            return PricingResult(
                price=intrinsic, delta=delta, gamma=0.0,
                theta=0.0, vega=0.0, rho=0.0,
            )

        if sigma <= 0.0:
            return BlackScholes.price(S, K, T, r, 0.0, option_type, q)

        rng = np.random.default_rng(seed)
        dt = T / num_steps
        drift = (r - q - 0.5 * sigma * sigma) * dt
        diffusion = sigma * math.sqrt(dt)

        # Generate half the paths, then mirror for antithetic
        half = num_paths // 2
        Z = rng.standard_normal((half, num_steps))

        def _simulate(z_matrix: np.ndarray, s0: float) -> np.ndarray:
            """Simulate terminal prices from standard normal draws."""
            log_increments = drift + diffusion * z_matrix
            log_ST = math.log(s0) + np.sum(log_increments, axis=1)
            return np.exp(log_ST)

        def _mc_price(s0: float, sig: float, r_: float) -> float:
            """Get discounted payoff for given parameters."""
            d_ = (r_ - q - 0.5 * sig * sig) * dt
            diff_ = sig * math.sqrt(dt)

            log_inc = d_ + diff_ * Z
            log_ST = math.log(s0) + np.sum(log_inc, axis=1)
            ST = np.exp(log_ST)

            log_inc_anti = d_ - diff_ * Z
            log_ST_anti = math.log(s0) + np.sum(log_inc_anti, axis=1)
            ST_anti = np.exp(log_ST_anti)

            if is_call:
                payoff = np.maximum(ST - K, 0.0)
                payoff_anti = np.maximum(ST_anti - K, 0.0)
            else:
                payoff = np.maximum(K - ST, 0.0)
                payoff_anti = np.maximum(K - ST_anti, 0.0)

            avg_payoff = 0.5 * (payoff + payoff_anti)
            return float(math.exp(-r_ * T) * np.mean(avg_payoff))

        price_val = _mc_price(S, sigma, r)

        # ------------------------------------------------------------------
        # Greeks via central finite differences (bump-and-reprice)
        # ------------------------------------------------------------------
        dS = S * 0.005  # 0.5% spot bump
        dsig = 0.001    # 10 bps vol bump
        dr = 0.0001     # 1 bp rate bump
        dT = 1.0 / 365.0  # 1-day time bump

        # Delta
        p_up = _mc_price(S + dS, sigma, r)
        p_down = _mc_price(S - dS, sigma, r)
        delta = (p_up - p_down) / (2.0 * dS)

        # Gamma
        gamma = (p_up - 2.0 * price_val + p_down) / (dS * dS)

        # Vega (per 1% vol)
        p_vup = _mc_price(S, sigma + dsig, r)
        p_vdn = _mc_price(S, sigma - dsig, r)
        vega_pct = (p_vup - p_vdn) / (2.0 * dsig) / 100.0

        # Rho (per 1% rate)
        p_rup = _mc_price(S, sigma, r + dr)
        p_rdn = _mc_price(S, sigma, r - dr)
        rho_pct = (p_rup - p_rdn) / (2.0 * dr) / 100.0

        # Theta (per day) -- use forward difference (shorter T)
        if T > dT:
            # Re-simulate with shorter time
            # For theta we need a fresh _mc_price with different T
            # We'll approximate by using the analytical bump approach
            def _mc_price_T(t_: float) -> float:
                dt_ = t_ / num_steps
                d_ = (r - q - 0.5 * sigma * sigma) * dt_
                diff_ = sigma * math.sqrt(dt_)
                log_inc_ = d_ + diff_ * Z
                log_ST_ = math.log(S) + np.sum(log_inc_, axis=1)
                ST_ = np.exp(log_ST_)

                log_inc_anti_ = d_ - diff_ * Z
                log_ST_anti_ = math.log(S) + np.sum(log_inc_anti_, axis=1)
                ST_anti_ = np.exp(log_ST_anti_)

                if is_call:
                    pay = np.maximum(ST_ - K, 0.0)
                    pay_a = np.maximum(ST_anti_ - K, 0.0)
                else:
                    pay = np.maximum(K - ST_, 0.0)
                    pay_a = np.maximum(K - ST_anti_, 0.0)
                avg = 0.5 * (pay + pay_a)
                return float(math.exp(-r * t_) * np.mean(avg))

            p_t_down = _mc_price_T(T - dT)
            theta_daily = (p_t_down - price_val) / dT / 365.0
        else:
            theta_daily = 0.0

        return PricingResult(
            price=max(price_val, 0.0),
            delta=delta,
            gamma=gamma,
            theta=theta_daily,
            vega=vega_pct,
            rho=rho_pct,
        )
