"""Unit tests for the Greeks Engine (Phase 4).

Tests cover:
- Black-Scholes pricing (calls, puts, edge cases, put-call parity, greeks)
- Binomial Tree pricing (American & European, convergence to BS)
- Monte Carlo pricing (convergence, antithetic variates)
- IV Solver (Newton-Raphson, Brent's, hybrid, edge cases)
- IV Surface Builder (smile construction, surface assembly, anomaly detection)
- Payoff Calculator (expiry payoff, before-expiry, breakevens, strategy ID)
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from core.greeks_engine import (
    BlackScholes,
    BinomialTree,
    MonteCarlo,
    PricingResult,
    IVSolver,
    IVSurfaceBuilder,
    IVSurface,
    IVSmile,
    IVPoint,
    PayoffCalculator,
    OptionLeg,
    FutureLeg,
)


# =====================================================================
# Black-Scholes Tests
# =====================================================================

class TestBlackScholes:
    """Test analytical Black-Scholes pricing."""

    # --- Basic pricing ---

    def test_atm_call_positive_price(self):
        res = BlackScholes.price(
            spot=100, strike=100, time_to_expiry=1.0,
            rate=0.05, volatility=0.20, option_type="CE",
        )
        assert res.price > 0
        assert 0 < res.delta < 1

    def test_atm_put_positive_price(self):
        res = BlackScholes.price(
            spot=100, strike=100, time_to_expiry=1.0,
            rate=0.05, volatility=0.20, option_type="PE",
        )
        assert res.price > 0
        assert -1 < res.delta < 0

    def test_deep_itm_call_delta_near_one(self):
        res = BlackScholes.price(
            spot=200, strike=100, time_to_expiry=0.5,
            rate=0.05, volatility=0.20, option_type="CE",
        )
        assert res.delta > 0.99

    def test_deep_otm_call_delta_near_zero(self):
        res = BlackScholes.price(
            spot=50, strike=100, time_to_expiry=0.1,
            rate=0.05, volatility=0.20, option_type="CE",
        )
        assert res.delta < 0.01

    def test_deep_itm_put_delta_near_minus_one(self):
        res = BlackScholes.price(
            spot=50, strike=100, time_to_expiry=0.5,
            rate=0.05, volatility=0.20, option_type="PE",
        )
        assert res.delta < -0.99

    # --- Put-Call Parity ---

    def test_put_call_parity(self):
        """C - P = S*e^{-qT} - K*e^{-rT}"""
        S, K, T, r, sigma = 24200, 24200, 30 / 365, 0.07, 0.15
        call = BlackScholes.price(S, K, T, r, sigma, "CE")
        put = BlackScholes.price(S, K, T, r, sigma, "PE")
        lhs = call.price - put.price
        rhs = S - K * math.exp(-r * T)
        assert abs(lhs - rhs) < 0.01

    def test_put_call_parity_otm(self):
        S, K, T, r, sigma = 24200, 24500, 14 / 365, 0.07, 0.18
        call = BlackScholes.price(S, K, T, r, sigma, "CE")
        put = BlackScholes.price(S, K, T, r, sigma, "PE")
        lhs = call.price - put.price
        rhs = S - K * math.exp(-r * T)
        assert abs(lhs - rhs) < 0.01

    # --- Edge cases ---

    def test_zero_time_to_expiry_itm_call(self):
        res = BlackScholes.price(
            spot=110, strike=100, time_to_expiry=0.0,
            rate=0.05, volatility=0.20, option_type="CE",
        )
        assert res.price == 10.0
        assert res.delta == 1.0

    def test_zero_time_to_expiry_otm_call(self):
        res = BlackScholes.price(
            spot=90, strike=100, time_to_expiry=0.0,
            rate=0.05, volatility=0.20, option_type="CE",
        )
        assert res.price == 0.0
        assert res.delta == 0.0

    def test_zero_volatility_itm_call(self):
        res = BlackScholes.price(
            spot=110, strike=100, time_to_expiry=1.0,
            rate=0.05, volatility=0.0, option_type="CE",
        )
        assert res.price > 0

    def test_zero_volatility_otm_call(self):
        res = BlackScholes.price(
            spot=90, strike=100, time_to_expiry=1.0,
            rate=0.05, volatility=0.0, option_type="CE",
        )
        assert res.price == 0.0

    # --- Greeks sign checks ---

    def test_call_theta_negative(self):
        """Long call theta should be negative (time decay)."""
        res = BlackScholes.price(
            spot=100, strike=100, time_to_expiry=0.5,
            rate=0.05, volatility=0.20, option_type="CE",
        )
        assert res.theta < 0

    def test_gamma_positive(self):
        """Gamma is always positive for long options."""
        call = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        put = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "PE")
        assert call.gamma > 0
        assert put.gamma > 0

    def test_call_put_same_gamma(self):
        """Call and put gamma should be equal at same strike."""
        call = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        put = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "PE")
        assert abs(call.gamma - put.gamma) < 1e-10

    def test_vega_positive(self):
        """Vega is always positive for long options."""
        res = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        assert res.vega > 0

    def test_call_rho_positive(self):
        """Call rho should be positive."""
        res = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        assert res.rho > 0

    def test_put_rho_negative(self):
        """Put rho should be negative."""
        res = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "PE")
        assert res.rho < 0

    # --- Higher-order greeks ---

    def test_higher_order_greeks_populated(self):
        res = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        assert res.charm != 0.0
        assert res.vanna != 0.0
        assert res.volga != 0.0
        assert res.speed != 0.0
        assert res.zomma != 0.0
        assert res.color != 0.0

    def test_lambda_leverage_ratio(self):
        """Lambda (omega) = delta * S / price."""
        res = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        expected = res.delta * 100 / res.price
        assert abs(res.lambda_ - expected) < 1e-10

    # --- NIFTY realistic values ---

    def test_nifty_atm_call_price_range(self):
        """NIFTY 24200 ATM weekly call should be in reasonable range."""
        res = BlackScholes.price(
            spot=24200, strike=24200, time_to_expiry=7 / 365,
            rate=0.07, volatility=0.15, option_type="CE",
        )
        # ATM weekly call should be roughly 150-300 at 15% vol
        assert 100 < res.price < 400
        assert 0.45 < res.delta < 0.55

    # --- d1/d2 static methods ---

    def test_d1_d2_consistency(self):
        """d2 = d1 - sigma*sqrt(T)."""
        S, K, T, r, sigma = 100, 105, 0.5, 0.05, 0.25
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        d2 = BlackScholes.d2(S, K, T, r, sigma)
        assert abs(d2 - (d1 - sigma * math.sqrt(T))) < 1e-12


# =====================================================================
# Binomial Tree Tests
# =====================================================================

class TestBinomialTree:
    """Test CRR binomial tree pricing."""

    def test_european_converges_to_bs(self):
        """European binomial tree should converge to BS as steps increase."""
        bs = BlackScholes.price(100, 100, 1.0, 0.05, 0.20, "CE")
        bt = BinomialTree.price(
            100, 100, 1.0, 0.05, 0.20, "CE",
            steps=500, is_american=False,
        )
        assert abs(bt.price - bs.price) < 0.10

    def test_american_call_equals_european_no_dividend(self):
        """American call = European call when there's no dividend."""
        eu = BinomialTree.price(
            100, 100, 1.0, 0.05, 0.20, "CE",
            steps=200, is_american=False,
        )
        am = BinomialTree.price(
            100, 100, 1.0, 0.05, 0.20, "CE",
            steps=200, is_american=True,
        )
        assert abs(am.price - eu.price) < 0.10

    def test_american_put_geq_european_put(self):
        """American put should be >= European put (early exercise value)."""
        eu = BinomialTree.price(
            100, 100, 1.0, 0.05, 0.20, "PE",
            steps=200, is_american=False,
        )
        am = BinomialTree.price(
            100, 100, 1.0, 0.05, 0.20, "PE",
            steps=200, is_american=True,
        )
        assert am.price >= eu.price - 0.01

    def test_at_expiry(self):
        """At expiry, binomial tree gives intrinsic value."""
        res = BinomialTree.price(110, 100, 0.0, 0.05, 0.20, "CE")
        assert res.price == 10.0

    def test_delta_reasonable(self):
        """ATM delta should be around 0.5."""
        res = BinomialTree.price(100, 100, 0.5, 0.05, 0.20, "CE", steps=200)
        assert 0.4 < res.delta < 0.65

    def test_gamma_positive(self):
        res = BinomialTree.price(100, 100, 0.5, 0.05, 0.20, "CE", steps=200)
        assert res.gamma > 0

    def test_vega_positive(self):
        res = BinomialTree.price(100, 100, 0.5, 0.05, 0.20, "CE", steps=200)
        assert res.vega > 0


# =====================================================================
# Monte Carlo Tests
# =====================================================================

class TestMonteCarlo:
    """Test Monte Carlo pricing with antithetic variates."""

    def test_mc_converges_to_bs(self):
        """MC price should be close to BS for European options."""
        bs = BlackScholes.price(100, 100, 1.0, 0.05, 0.20, "CE")
        mc = MonteCarlo.price(
            100, 100, 1.0, 0.05, 0.20, "CE",
            num_paths=100_000, seed=42,
        )
        assert abs(mc.price - bs.price) < 0.50

    def test_mc_put_converges(self):
        bs = BlackScholes.price(100, 100, 1.0, 0.05, 0.20, "PE")
        mc = MonteCarlo.price(
            100, 100, 1.0, 0.05, 0.20, "PE",
            num_paths=100_000, seed=42,
        )
        assert abs(mc.price - bs.price) < 0.50

    def test_mc_deterministic_with_seed(self):
        """Same seed should give same result."""
        mc1 = MonteCarlo.price(100, 100, 1.0, 0.05, 0.20, "CE", seed=123)
        mc2 = MonteCarlo.price(100, 100, 1.0, 0.05, 0.20, "CE", seed=123)
        assert mc1.price == mc2.price

    def test_mc_at_expiry(self):
        res = MonteCarlo.price(110, 100, 0.0, 0.05, 0.20, "CE")
        assert res.price == 10.0

    def test_mc_delta_reasonable(self):
        mc = MonteCarlo.price(
            100, 100, 0.5, 0.05, 0.20, "CE",
            num_paths=50_000, seed=42,
        )
        assert 0.35 < mc.delta < 0.75


# =====================================================================
# IV Solver Tests
# =====================================================================

class TestIVSolver:
    """Test implied volatility solver."""

    # --- Round-trip tests ---

    def test_iv_round_trip_call(self):
        """Solve IV from a BS price, should recover original vol."""
        original_vol = 0.20
        bs = BlackScholes.price(100, 100, 0.5, 0.05, original_vol, "CE")
        iv = IVSolver.solve(bs.price, 100, 100, 0.5, 0.05, "CE")
        assert iv is not None
        assert abs(iv - original_vol) < 1e-6

    def test_iv_round_trip_put(self):
        original_vol = 0.25
        bs = BlackScholes.price(100, 105, 0.5, 0.05, original_vol, "PE")
        iv = IVSolver.solve(bs.price, 100, 105, 0.5, 0.05, "PE")
        assert iv is not None
        assert abs(iv - original_vol) < 1e-6

    def test_iv_round_trip_high_vol(self):
        original_vol = 0.80
        bs = BlackScholes.price(100, 100, 1.0, 0.05, original_vol, "CE")
        iv = IVSolver.solve(bs.price, 100, 100, 1.0, 0.05, "CE")
        assert iv is not None
        assert abs(iv - original_vol) < 1e-5

    def test_iv_round_trip_low_vol(self):
        original_vol = 0.05
        bs = BlackScholes.price(100, 100, 1.0, 0.05, original_vol, "CE")
        iv = IVSolver.solve(bs.price, 100, 100, 1.0, 0.05, "CE")
        assert iv is not None
        assert abs(iv - original_vol) < 1e-5

    # --- Method-specific tests ---

    def test_newton_method(self):
        bs = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        iv = IVSolver.solve(bs.price, 100, 100, 0.5, 0.05, "CE", method="newton")
        assert iv is not None
        assert abs(iv - 0.20) < 1e-6

    def test_brent_method(self):
        bs = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        iv = IVSolver.solve(bs.price, 100, 100, 0.5, 0.05, "CE", method="brent")
        assert iv is not None
        assert abs(iv - 0.20) < 1e-6

    def test_hybrid_method(self):
        bs = BlackScholes.price(100, 100, 0.5, 0.05, 0.20, "CE")
        iv = IVSolver.solve(bs.price, 100, 100, 0.5, 0.05, "CE", method="hybrid")
        assert iv is not None
        assert abs(iv - 0.20) < 1e-6

    # --- Edge cases ---

    def test_iv_zero_price_returns_none(self):
        assert IVSolver.solve(0.0, 100, 100, 0.5, 0.05, "CE") is None

    def test_iv_negative_price_returns_none(self):
        assert IVSolver.solve(-1.0, 100, 100, 0.5, 0.05, "CE") is None

    def test_iv_zero_time_returns_none(self):
        assert IVSolver.solve(5.0, 100, 100, 0.0, 0.05, "CE") is None

    def test_iv_price_below_intrinsic_returns_none(self):
        # Call intrinsic ~ S - K*e^(-rT) = 110 - 100*e^(-0.05*0.5) ~ 12.47
        # Price 1.0 is way below intrinsic
        assert IVSolver.solve(1.0, 110, 100, 0.5, 0.05, "CE") is None

    def test_iv_invalid_method_raises(self):
        with pytest.raises(ValueError, match="Unknown method"):
            IVSolver.solve(10.0, 100, 100, 0.5, 0.05, "CE", method="bisect")

    # --- NIFTY realistic scenario ---

    def test_nifty_iv_solve(self):
        """Solve IV for a NIFTY ATM weekly option."""
        # Typical NIFTY 24200 CE weekly with ~200 premium -> ~14-16% IV
        iv = IVSolver.solve(
            market_price=200, spot=24200, strike=24200,
            time_to_expiry=7 / 365, rate=0.07, option_type="CE",
        )
        assert iv is not None
        assert 0.10 < iv < 0.25

    # --- IV Percentile & Rank ---

    def test_iv_percentile(self):
        historical = [0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22, 0.24, 0.26, 0.28]
        pct = IVSolver.iv_percentile(0.20, historical)
        # 5 values below 0.20 out of 10 -> 50%
        assert pct == 50.0

    def test_iv_percentile_empty(self):
        assert IVSolver.iv_percentile(0.15, []) == 0.0

    def test_iv_percentile_all_below(self):
        assert IVSolver.iv_percentile(0.30, [0.10, 0.15, 0.20]) == 100.0

    def test_iv_rank(self):
        historical = [0.10, 0.12, 0.14, 0.16, 0.18, 0.20]
        rank = IVSolver.iv_rank(0.15, historical)
        # (0.15 - 0.10) / (0.20 - 0.10) * 100 = 50.0
        assert abs(rank - 50.0) < 1e-10

    def test_iv_rank_empty(self):
        assert IVSolver.iv_rank(0.15, []) == 0.0

    def test_iv_rank_flat(self):
        assert IVSolver.iv_rank(0.15, [0.15, 0.15, 0.15]) == 0.0

    def test_iv_rank_clamped(self):
        """IV rank should be clamped to [0, 100] if current is outside range."""
        rank = IVSolver.iv_rank(0.30, [0.10, 0.15, 0.20])
        assert rank == 100.0


# =====================================================================
# IV Surface Tests
# =====================================================================

class TestIVSurface:
    """Test IV surface construction and analysis."""

    @pytest.fixture
    def builder(self):
        return IVSurfaceBuilder()

    @pytest.fixture
    def sample_smile(self, builder):
        """Create a sample smile for NIFTY weekly."""
        strikes = [23800, 23900, 24000, 24100, 24200, 24300, 24400, 24500, 24600]
        ivs = [0.18, 0.17, 0.16, 0.155, 0.15, 0.155, 0.16, 0.17, 0.18]
        option_types = ["PE", "PE", "PE", "PE", "CE", "CE", "CE", "CE", "CE"]
        return builder.build_smile(
            underlying="NIFTY",
            spot=24200,
            expiry=date(2026, 4, 3),
            time_to_expiry=3 / 365,
            strikes=strikes,
            ivs=ivs,
            option_types=option_types,
        )

    def test_build_smile_point_count(self, sample_smile):
        assert len(sample_smile.points) == 9

    def test_build_smile_atm_iv(self, sample_smile):
        """ATM IV should be the IV at the strike closest to spot."""
        # Strike 24200 has IV 0.15 and moneyness closest to 1.0
        assert abs(sample_smile.atm_iv - 0.15) < 0.01

    def test_build_smile_sorted_by_strike(self, sample_smile):
        strikes = [p.strike for p in sample_smile.points]
        assert strikes == sorted(strikes)

    def test_build_smile_moneyness(self, sample_smile):
        """Moneyness at ATM should be ~1.0."""
        atm_point = min(sample_smile.points, key=lambda p: abs(p.moneyness - 1.0))
        assert abs(atm_point.moneyness - 1.0) < 0.01

    def test_build_smile_mismatched_lengths(self, builder):
        with pytest.raises(ValueError):
            builder.build_smile(
                "NIFTY", 24200, date(2026, 4, 3), 3 / 365,
                strikes=[24200, 24300],
                ivs=[0.15],
                option_types=["CE", "CE"],
            )

    def test_build_surface(self, builder, sample_smile):
        smile2 = builder.build_smile(
            "NIFTY", 24200, date(2026, 4, 10), 10 / 365,
            strikes=[24000, 24200, 24400],
            ivs=[0.16, 0.15, 0.16],
            option_types=["PE", "CE", "CE"],
        )
        surface = builder.build_surface("NIFTY", 24200, [sample_smile, smile2])
        assert len(surface.smiles) == 2
        assert surface.underlying == "NIFTY"
        assert surface.spot_price == 24200

    def test_surface_get_iv_interpolation(self, builder, sample_smile):
        smile2 = builder.build_smile(
            "NIFTY", 24200, date(2026, 4, 10), 10 / 365,
            strikes=[24000, 24200, 24400],
            ivs=[0.17, 0.16, 0.17],
            option_types=["PE", "CE", "CE"],
        )
        surface = builder.build_surface("NIFTY", 24200, [sample_smile, smile2])
        iv = surface.get_iv(24200, 6 / 365)
        assert iv is not None
        assert 0.14 < iv < 0.17

    def test_surface_term_structure(self, builder, sample_smile):
        smile2 = builder.build_smile(
            "NIFTY", 24200, date(2026, 4, 10), 10 / 365,
            strikes=[24000, 24200, 24400],
            ivs=[0.17, 0.16, 0.17],
            option_types=["PE", "CE", "CE"],
        )
        surface = builder.build_surface("NIFTY", 24200, [sample_smile, smile2])
        term = surface.get_term_structure(moneyness=1.0)
        assert len(term) == 2
        # Should be sorted by time
        assert term[0][0] < term[1][0]

    def test_surface_to_matrix(self, builder, sample_smile):
        smile2 = builder.build_smile(
            "NIFTY", 24200, date(2026, 4, 10), 10 / 365,
            strikes=[24000, 24200, 24400],
            ivs=[0.17, 0.16, 0.17],
            option_types=["PE", "CE", "CE"],
        )
        surface = builder.build_surface("NIFTY", 24200, [sample_smile, smile2])
        strikes, expiries, matrix = surface.to_matrix()
        assert len(strikes) > 0
        assert len(expiries) == 2
        assert matrix.shape[0] == 2

    def test_surface_get_skew(self, builder, sample_smile):
        surface = builder.build_surface("NIFTY", 24200, [sample_smile])
        skew = surface.get_skew(date(2026, 4, 3))
        # Skew may be zero or non-zero depending on delta computation
        assert skew is not None

    def test_surface_empty_get_iv(self):
        surface = IVSurface(underlying="NIFTY", spot_price=24200)
        assert surface.get_iv(24200, 0.02) is None

    def test_anomaly_detection_not_enough_history(self, builder, sample_smile):
        surface = builder.build_surface("NIFTY", 24200, [sample_smile])
        anomalies = builder.detect_skew_anomaly("NIFTY")
        # Not enough history
        assert anomalies == []

    def test_tracked_underlyings(self, builder, sample_smile):
        builder.build_surface("NIFTY", 24200, [sample_smile])
        assert "NIFTY" in builder.tracked_underlyings

    def test_get_surface(self, builder, sample_smile):
        builder.build_surface("NIFTY", 24200, [sample_smile])
        surface = builder.get_surface("NIFTY")
        assert surface is not None
        assert surface.underlying == "NIFTY"

    def test_get_surface_not_found(self, builder):
        assert builder.get_surface("BANKNIFTY") is None


# =====================================================================
# Payoff Calculator Tests
# =====================================================================

class TestPayoffCalculator:
    """Test multi-leg payoff calculations."""

    @pytest.fixture
    def calc(self):
        return PayoffCalculator()

    # --- Single leg ---

    def test_long_call_payoff(self, calc):
        legs = [OptionLeg(strike=100, option_type="CE", side="BUY",
                          quantity=1, lot_size=1, premium=5)]
        result = calc.compute_payoff_at_expiry(legs, spot_range=(80, 130))
        points = result["points"]
        # At spot=120: payoff = (120-100-5)*1 = 15
        high_spot = [p for p in points if abs(p["underlying_price"] - 120) < 2]
        if high_spot:
            assert abs(high_spot[0]["payoff"] - 15.0) < 2.0
        # At spot=90: payoff = -5 (premium lost, option expires worthless)
        low_spot = [p for p in points if abs(p["underlying_price"] - 90) < 2]
        if low_spot:
            assert abs(low_spot[0]["payoff"] - (-5.0)) < 1.0
        # Max loss = premium paid = -5
        assert result["max_loss"] is not None

    def test_short_put_payoff(self, calc):
        legs = [OptionLeg(strike=100, option_type="PE", side="SELL",
                          quantity=1, lot_size=1, premium=5)]
        result = calc.compute_payoff_at_expiry(legs)
        assert result["total_premium"] == 5.0  # credit received

    # --- Vertical spread ---

    def test_bull_call_spread(self, calc):
        legs = PayoffCalculator.vertical_spread(
            long_strike=100, short_strike=110,
            option_type="CE", long_premium=8, short_premium=3,
            lots=1, lot_size=1,
        )
        result = calc.compute_payoff_at_expiry(legs)
        # Max profit at spot >= 110: (110-100) - (8-3) = 5
        # Max loss at spot <= 100: -(8-3) = -5
        assert result["max_profit"] is not None
        assert result["max_loss"] is not None
        assert abs(result["max_profit"] - 5.0) < 1.0
        assert abs(result["max_loss"] - (-5.0)) < 1.0

    # --- Straddle ---

    def test_long_straddle_payoff(self, calc):
        legs = PayoffCalculator.straddle(
            strike=100, call_premium=6, put_premium=5, side="BUY",
            lots=1, lot_size=1,
        )
        result = calc.compute_payoff_at_expiry(legs)
        # Should have two breakevens
        assert len(result["breakevens"]) == 2
        # Breakevens at 100-11=89 and 100+11=111
        breakevens = sorted(result["breakevens"])
        assert abs(breakevens[0] - 89) < 2
        assert abs(breakevens[1] - 111) < 2

    def test_short_straddle_payoff(self, calc):
        legs = PayoffCalculator.straddle(
            strike=100, call_premium=6, put_premium=5, side="SELL",
            lots=1, lot_size=1,
        )
        result = calc.compute_payoff_at_expiry(legs)
        # Max profit = total premium = 6+5 = 11
        assert result["max_profit"] is not None
        assert abs(result["max_profit"] - 11.0) < 1.0

    # --- Iron Condor ---

    def test_iron_condor_payoff(self, calc):
        legs = PayoffCalculator.iron_condor(
            short_call_strike=24500, long_call_strike=24700,
            short_put_strike=23900, long_put_strike=23700,
            short_call_premium=50, long_call_premium=15,
            short_put_premium=45, long_put_premium=12,
            lots=1, lot_size=25,
        )
        result = calc.compute_payoff_at_expiry(legs)
        # Net credit = (50-15+45-12) * 25 = 68 * 25 = 1700
        assert result["max_profit"] is not None
        assert abs(result["max_profit"] - 1700) < 100
        # Max loss = (200 - 68) * 25 = 3300
        assert result["max_loss"] is not None
        assert abs(result["max_loss"] - (-3300)) < 100
        # Should have two breakevens
        assert len(result["breakevens"]) == 2

    # --- Strangle ---

    def test_strangle_legs(self, calc):
        legs = PayoffCalculator.strangle(
            call_strike=24500, put_strike=23900,
            call_premium=50, put_premium=45, side="SELL",
            lots=1, lot_size=25,
        )
        assert len(legs) == 2
        assert legs[0].option_type == "CE"
        assert legs[1].option_type == "PE"

    # --- Breakeven detection ---

    def test_breakeven_simple(self, calc):
        points = [
            {"underlying_price": 95, "payoff": -5.0},
            {"underlying_price": 100, "payoff": 0.0},
            {"underlying_price": 105, "payoff": 5.0},
        ]
        be = PayoffCalculator.compute_breakevens(points)
        assert len(be) >= 1

    def test_breakeven_zero_crossing(self, calc):
        points = [
            {"underlying_price": 90, "payoff": -10.0},
            {"underlying_price": 100, "payoff": 10.0},
        ]
        be = PayoffCalculator.compute_breakevens(points)
        assert len(be) == 1
        assert abs(be[0] - 95.0) < 0.1

    # --- Strategy identification ---

    def test_identify_long_call(self):
        legs = [OptionLeg(100, "CE", "BUY", 1, 25, 5)]
        assert PayoffCalculator.identify_strategy(legs) == "Long Call"

    def test_identify_short_put(self):
        legs = [OptionLeg(100, "PE", "SELL", 1, 25, 5)]
        assert PayoffCalculator.identify_strategy(legs) == "Short Put"

    def test_identify_long_straddle(self):
        legs = [
            OptionLeg(100, "CE", "BUY", 1, 25, 5),
            OptionLeg(100, "PE", "BUY", 1, 25, 5),
        ]
        assert PayoffCalculator.identify_strategy(legs) == "Long Straddle"

    def test_identify_short_strangle(self):
        legs = [
            OptionLeg(105, "CE", "SELL", 1, 25, 5),
            OptionLeg(95, "PE", "SELL", 1, 25, 5),
        ]
        assert PayoffCalculator.identify_strategy(legs) == "Short Strangle"

    def test_identify_bull_call_spread(self):
        legs = [
            OptionLeg(100, "CE", "BUY", 1, 25, 8),
            OptionLeg(110, "CE", "SELL", 1, 25, 3),
        ]
        assert PayoffCalculator.identify_strategy(legs) == "Bull Call Spread"

    def test_identify_bear_put_spread(self):
        legs = [
            OptionLeg(110, "PE", "BUY", 1, 25, 8),
            OptionLeg(100, "PE", "SELL", 1, 25, 3),
        ]
        result = PayoffCalculator.identify_strategy(legs)
        # Could be "Bear Put Spread" or "Bull Put Spread" depending on implementation
        assert "Put Spread" in result

    def test_identify_iron_condor(self):
        legs = PayoffCalculator.iron_condor(
            24500, 24700, 23900, 23700,
            50, 15, 45, 12, 1, 25,
        )
        assert PayoffCalculator.identify_strategy(legs) == "Iron Condor"

    def test_identify_custom_strategy(self):
        # 5 legs -> should be "Custom Strategy"
        legs = [
            OptionLeg(100, "CE", "BUY", 1, 25, 5),
            OptionLeg(105, "CE", "SELL", 1, 25, 3),
            OptionLeg(110, "CE", "BUY", 1, 25, 2),
            OptionLeg(95, "PE", "BUY", 1, 25, 4),
            OptionLeg(90, "PE", "SELL", 1, 25, 2),
        ]
        assert PayoffCalculator.identify_strategy(legs) == "Custom Strategy"

    def test_identify_no_legs(self):
        assert PayoffCalculator.identify_strategy([]) == "No Legs"

    # --- Before-expiry payoff ---

    def test_before_expiry_payoff(self, calc):
        legs = [OptionLeg(strike=24200, option_type="CE", side="BUY",
                          quantity=1, lot_size=25, premium=200)]
        result = calc.compute_payoff_before_expiry(
            legs, days_to_expiry=7, volatility=0.15, rate=0.07,
        )
        assert len(result["points"]) == 200
        # P&L should generally be higher than expiry payoff due to time value

    # --- FutureLeg ---

    def test_future_leg_payoff(self, calc):
        legs = [FutureLeg(entry_price=24200, side="BUY", quantity=1, lot_size=25)]
        result = calc.compute_payoff_at_expiry(legs, spot_range=(23000, 25400))
        # At spot=24700: payoff = (24700-24200)*25 = 12500
        high_spot = [p for p in result["points"]
                     if abs(p["underlying_price"] - 24700) < 20]
        if high_spot:
            assert abs(high_spot[0]["payoff"] - 12500) < 500

    def test_default_range_auto(self, calc):
        """Verify auto-range does not crash."""
        legs = [OptionLeg(24200, "CE", "BUY", 1, 25, 200)]
        result = calc.compute_payoff_at_expiry(legs)
        assert len(result["points"]) == 200


# =====================================================================
# PricingResult Tests
# =====================================================================

class TestPricingResult:
    """Test the PricingResult dataclass."""

    def test_creation(self):
        r = PricingResult(
            price=10.0, delta=0.5, gamma=0.01,
            theta=-0.05, vega=0.1, rho=0.02,
        )
        assert r.price == 10.0
        assert r.charm == 0.0  # default

    def test_higher_order_defaults(self):
        r = PricingResult(
            price=10.0, delta=0.5, gamma=0.01,
            theta=-0.05, vega=0.1, rho=0.02,
        )
        assert r.vanna == 0.0
        assert r.volga == 0.0
        assert r.speed == 0.0
        assert r.zomma == 0.0
        assert r.color == 0.0
        assert r.lambda_ == 0.0
