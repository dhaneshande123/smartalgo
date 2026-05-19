"""Option strategy payoff calculator -- computes payoff diagrams for multi-leg positions.

Supports arbitrary combinations of calls, puts, and futures with:
* Vectorised expiry-payoff computation over a configurable price range.
* Before-expiry payoff via Black-Scholes repricing of each leg.
* Automatic breakeven detection (zero-crossing interpolation).
* Max-profit / max-loss estimation.
* Strategy identification from leg structure.
* Convenience constructors for common strategies (iron condor, straddle,
  strangle, vertical spread).

All heavy numerical work is done with NumPy for performance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


# ---------------------------------------------------------------------------
# Leg data classes
# ---------------------------------------------------------------------------


@dataclass
class OptionLeg:
    """A single leg in a multi-leg option strategy.

    Attributes:
        strike: Strike price.
        option_type: ``"CE"`` for call, ``"PE"`` for put.
        side: ``"BUY"`` or ``"SELL"``.
        quantity: Number of lots.
        lot_size: Units per lot (e.g. 25 for NIFTY options).
        premium: Premium paid/received **per unit** of the underlying.
    """

    strike: float
    option_type: str  # "CE" or "PE"
    side: str  # "BUY" or "SELL"
    quantity: int  # number of lots
    lot_size: int = 1
    premium: float = 0.0  # premium paid/received per unit

    @property
    def net_quantity(self) -> int:
        """Signed quantity: positive for buy, negative for sell."""
        return self.quantity * self.lot_size * (1 if self.side == "BUY" else -1)


@dataclass
class FutureLeg:
    """A futures leg in the strategy.

    Attributes:
        entry_price: Entry price of the futures position.
        side: ``"BUY"`` or ``"SELL"``.
        quantity: Number of lots.
        lot_size: Units per lot.
    """

    entry_price: float
    side: str
    quantity: int
    lot_size: int = 1

    @property
    def net_quantity(self) -> int:
        """Signed quantity: positive for buy, negative for sell."""
        return self.quantity * self.lot_size * (1 if self.side == "BUY" else -1)


# ---------------------------------------------------------------------------
# Black-Scholes helper (self-contained to avoid circular imports)
# ---------------------------------------------------------------------------

def _bs_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    volatility: float,
    option_type: str = "CE",
) -> float:
    """Return the Black-Scholes price of a European option.

    This is a lightweight helper used only for before-expiry payoff
    calculations.  For full greeks use the ``pricing.BlackScholes`` class.
    """
    if time_to_expiry <= 0.0:
        if option_type.upper() == "CE":
            return max(spot - strike, 0.0)
        return max(strike - spot, 0.0)

    if volatility <= 0.0:
        df = math.exp(-rate * time_to_expiry)
        fwd = spot * math.exp(rate * time_to_expiry)
        if option_type.upper() == "CE":
            return max(fwd - strike, 0.0) * df
        return max(strike - fwd, 0.0) * df

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility ** 2) * time_to_expiry) / (
        volatility * sqrt_t
    )
    d2 = d1 - volatility * sqrt_t

    if option_type.upper() == "CE":
        return float(
            spot * norm.cdf(d1) - strike * math.exp(-rate * time_to_expiry) * norm.cdf(d2)
        )
    return float(
        strike * math.exp(-rate * time_to_expiry) * norm.cdf(-d2) - spot * norm.cdf(-d1)
    )


def _bs_price_vec(
    spots: np.ndarray,
    strike: float,
    time_to_expiry: float,
    rate: float,
    volatility: float,
    option_type: str = "CE",
) -> np.ndarray:
    """Vectorised Black-Scholes over an array of spot prices."""
    if time_to_expiry <= 0.0:
        if option_type.upper() == "CE":
            return np.maximum(spots - strike, 0.0)
        return np.maximum(strike - spots, 0.0)

    if volatility <= 0.0:
        df = math.exp(-rate * time_to_expiry)
        fwd = spots * math.exp(rate * time_to_expiry)
        if option_type.upper() == "CE":
            return np.maximum(fwd - strike, 0.0) * df
        return np.maximum(strike - fwd, 0.0) * df

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (np.log(spots / strike) + (rate + 0.5 * volatility ** 2) * time_to_expiry) / (
        volatility * sqrt_t
    )
    d2 = d1 - volatility * sqrt_t

    df = math.exp(-rate * time_to_expiry)
    if option_type.upper() == "CE":
        return spots * norm.cdf(d1) - strike * df * norm.cdf(d2)
    return strike * df * norm.cdf(-d2) - spots * norm.cdf(-d1)


# ---------------------------------------------------------------------------
# Payoff calculator
# ---------------------------------------------------------------------------


class PayoffCalculator:
    """Computes payoff diagrams for multi-leg option strategies.

    Supports
    --------
    * Any combination of calls, puts, and futures.
    * Payoff at expiry and before expiry (using Black-Scholes pricing).
    * Max profit, max loss, and breakeven calculations.
    * Automatic strategy identification from leg structure.
    * Pre-built constructors for common strategies.
    """

    def __init__(self) -> None:
        pass

    # -----------------------------------------------------------------
    # Expiry payoff
    # -----------------------------------------------------------------

    def compute_payoff_at_expiry(
        self,
        legs: list[OptionLeg | FutureLeg],
        spot_range: tuple[float, float] | None = None,
        num_points: int = 200,
    ) -> dict:
        """Compute the strategy payoff at expiry across underlying prices.

        Parameters
        ----------
        legs:
            Option and/or futures legs comprising the strategy.
        spot_range:
            ``(low, high)`` price range to evaluate.  If ``None`` a
            sensible range is derived from the leg strikes.
        num_points:
            Number of price points to compute.

        Returns
        -------
        dict
            ``{
                "points": [{"underlying_price": float, "payoff": float}, ...],
                "max_profit": float | None,
                "max_loss": float | None,
                "breakevens": [float, ...],
                "total_premium": float,
            }``

            ``max_profit`` / ``max_loss`` are ``None`` when the payoff is
            unbounded in that direction (e.g. naked call sell has unlimited
            loss).
        """
        low, high = self._default_range(legs) if spot_range is None else spot_range
        prices = np.linspace(low, high, num_points)
        payoffs = np.zeros_like(prices)

        total_premium = 0.0

        for leg in legs:
            if isinstance(leg, OptionLeg):
                nq = leg.net_quantity
                is_call = leg.option_type.upper() == "CE"

                if is_call:
                    intrinsic = np.maximum(prices - leg.strike, 0.0)
                else:
                    intrinsic = np.maximum(leg.strike - prices, 0.0)

                # Payoff from option exercise
                payoffs += intrinsic * nq

                # Premium: buyer pays, seller receives
                abs_units = leg.quantity * leg.lot_size
                if leg.side == "BUY":
                    payoffs -= leg.premium * abs_units
                    total_premium -= leg.premium * abs_units
                else:
                    payoffs += leg.premium * abs_units
                    total_premium += leg.premium * abs_units

            elif isinstance(leg, FutureLeg):
                nq = leg.net_quantity
                payoffs += (prices - leg.entry_price) * nq

        # Build points list
        points = [
            {"underlying_price": float(p), "payoff": float(pf)}
            for p, pf in zip(prices, payoffs)
        ]

        # Breakevens
        breakevens = self.compute_breakevens(points)

        # Max profit / max loss
        max_profit = self._compute_max_profit(legs, payoffs)
        max_loss = self._compute_max_loss(legs, payoffs)

        return {
            "points": points,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "breakevens": breakevens,
            "total_premium": float(total_premium),
        }

    # -----------------------------------------------------------------
    # Before-expiry payoff
    # -----------------------------------------------------------------

    def compute_payoff_before_expiry(
        self,
        legs: list[OptionLeg],
        spot_range: tuple[float, float] | None = None,
        days_to_expiry: float = 0.0,
        volatility: float = 0.15,
        rate: float = 0.07,
        num_points: int = 200,
    ) -> dict:
        """Compute the strategy payoff before expiry using Black-Scholes.

        Each leg's current theoretical value at every spot level is
        computed via BS, then compared to the entry premium to determine
        the position P&L.

        Parameters
        ----------
        legs:
            Option legs (futures legs are not supported here).
        spot_range:
            ``(low, high)`` underlying price range.
        days_to_expiry:
            Calendar days remaining to expiry.
        volatility:
            Implied volatility (annualised) used for repricing.
        rate:
            Risk-free rate (annualised).
        num_points:
            Number of evaluation points.

        Returns
        -------
        dict
            Same structure as :meth:`compute_payoff_at_expiry`.
        """
        low, high = self._default_range(legs) if spot_range is None else spot_range
        prices = np.linspace(low, high, num_points)
        payoffs = np.zeros_like(prices)

        tte = days_to_expiry / 365.0
        total_premium = 0.0

        for leg in legs:
            nq = leg.net_quantity
            abs_units = leg.quantity * leg.lot_size

            # Current theoretical value at each spot level
            current_values = _bs_price_vec(
                prices, leg.strike, tte, rate, volatility, leg.option_type,
            )

            # P&L = (current_value - entry_premium) * signed_units
            # For a BUY leg:  P&L per unit = current_value - premium_paid
            # For a SELL leg: P&L per unit = premium_received - current_value
            if leg.side == "BUY":
                payoffs += (current_values - leg.premium) * abs_units
                total_premium -= leg.premium * abs_units
            else:
                payoffs += (leg.premium - current_values) * abs_units
                total_premium += leg.premium * abs_units

        points = [
            {"underlying_price": float(p), "payoff": float(pf)}
            for p, pf in zip(prices, payoffs)
        ]

        breakevens = self.compute_breakevens(points)
        max_pf = float(np.max(payoffs))
        min_pf = float(np.min(payoffs))

        return {
            "points": points,
            "max_profit": max_pf,
            "max_loss": min_pf,
            "breakevens": breakevens,
            "total_premium": float(total_premium),
        }

    # -----------------------------------------------------------------
    # Breakeven detection
    # -----------------------------------------------------------------

    @staticmethod
    def compute_breakevens(points: list[dict]) -> list[float]:
        """Find zero-crossing points in the payoff curve.

        Uses linear interpolation between adjacent points where the
        payoff changes sign.

        Parameters
        ----------
        points:
            List of ``{"underlying_price": float, "payoff": float}``
            dicts, assumed sorted by ``underlying_price``.

        Returns
        -------
        list[float]
            Underlying prices where the payoff crosses zero.
        """
        breakevens: list[float] = []
        for i in range(len(points) - 1):
            p0 = points[i]["payoff"]
            p1 = points[i + 1]["payoff"]

            # Sign change or exact zero
            if p0 * p1 < 0:
                # Linear interpolation for the zero crossing
                x0 = points[i]["underlying_price"]
                x1 = points[i + 1]["underlying_price"]
                # x at y=0:  x = x0 - p0 * (x1-x0) / (p1-p0)
                be = x0 - p0 * (x1 - x0) / (p1 - p0)
                breakevens.append(round(float(be), 2))
            elif p0 == 0.0:
                breakevens.append(round(float(points[i]["underlying_price"]), 2))

        # De-duplicate (edge case: payoff sits at zero for multiple points)
        seen: set[float] = set()
        unique: list[float] = []
        for be in breakevens:
            if be not in seen:
                seen.add(be)
                unique.append(be)
        return unique

    # -----------------------------------------------------------------
    # Strategy identification
    # -----------------------------------------------------------------

    @staticmethod
    def identify_strategy(legs: list[OptionLeg]) -> str:
        """Attempt to identify the strategy name from the legs.

        Recognises common one-to-four-leg option strategies based on
        the number of legs, their option types, sides, and strike
        relationships.

        Parameters
        ----------
        legs:
            Option legs to analyse.

        Returns
        -------
        str
            Human-readable strategy name, or ``"Custom Strategy"`` if
            the combination is not recognised.
        """
        n = len(legs)

        if n == 0:
            return "No Legs"

        # --- Single-leg strategies ---
        if n == 1:
            leg = legs[0]
            side = "Long" if leg.side == "BUY" else "Short"
            kind = "Call" if leg.option_type.upper() == "CE" else "Put"
            return f"{side} {kind}"

        # Normalise and sort for analysis
        sorted_legs = sorted(legs, key=lambda l: (l.strike, l.option_type))

        calls = [l for l in sorted_legs if l.option_type.upper() == "CE"]
        puts = [l for l in sorted_legs if l.option_type.upper() == "PE"]
        buys = [l for l in sorted_legs if l.side == "BUY"]
        sells = [l for l in sorted_legs if l.side == "SELL"]
        strikes = sorted({l.strike for l in sorted_legs})

        # --- Two-leg strategies ---
        if n == 2:
            l1, l2 = sorted_legs

            # Straddle: same strike, one call, one put, same side
            if (
                len(calls) == 1
                and len(puts) == 1
                and l1.strike == l2.strike
                and l1.side == l2.side
            ):
                side = "Long" if l1.side == "BUY" else "Short"
                return f"{side} Straddle"

            # Strangle: different strikes, one call, one put, same side
            if (
                len(calls) == 1
                and len(puts) == 1
                and l1.strike != l2.strike
                and l1.side == l2.side
            ):
                side = "Long" if l1.side == "BUY" else "Short"
                return f"{side} Strangle"

            # Vertical spreads: same type, different strikes, one buy one sell
            if len(calls) == 2 and len(buys) == 1 and len(sells) == 1:
                buy_leg = buys[0]
                sell_leg = sells[0]
                if buy_leg.strike < sell_leg.strike:
                    return "Bull Call Spread"
                return "Bear Call Spread"

            if len(puts) == 2 and len(buys) == 1 and len(sells) == 1:
                buy_leg = buys[0]
                sell_leg = sells[0]
                if buy_leg.strike > sell_leg.strike:
                    return "Bull Put Spread"
                return "Bear Put Spread"

            # Calendar spread: same strike, same type, different
            # (would need expiry info to distinguish -- flag as potential)
            if l1.strike == l2.strike and l1.option_type == l2.option_type and l1.side != l2.side:
                kind = "Call" if l1.option_type.upper() == "CE" else "Put"
                return f"{kind} Calendar Spread"

            # Covered call: one put buy + one call sell at different strikes
            # (simplified recognition)

        # --- Three-leg strategies ---
        if n == 3:
            # Ratio spread: 2 of one side, 1 of the other, same type
            if len(calls) == 3 or len(puts) == 3:
                buy_count = sum(1 for l in sorted_legs if l.side == "BUY")
                sell_count = n - buy_count
                if buy_count == 1 and sell_count == 2:
                    kind = "Call" if len(calls) == 3 else "Put"
                    return f"{kind} Ratio Spread (1x2)"
                if buy_count == 2 and sell_count == 1:
                    kind = "Call" if len(calls) == 3 else "Put"
                    return f"{kind} Back Spread (2x1)"

        # --- Four-leg strategies ---
        if n == 4:
            # Iron Condor: 4 legs, 2 calls + 2 puts, 4 different strikes
            if len(calls) == 2 and len(puts) == 2 and len(strikes) == 4:
                call_buys = [l for l in calls if l.side == "BUY"]
                call_sells = [l for l in calls if l.side == "SELL"]
                put_buys = [l for l in puts if l.side == "BUY"]
                put_sells = [l for l in puts if l.side == "SELL"]
                if (
                    len(call_buys) == 1
                    and len(call_sells) == 1
                    and len(put_buys) == 1
                    and len(put_sells) == 1
                ):
                    return "Iron Condor"

            # Iron Butterfly: 4 legs, 2 calls + 2 puts, 3 strikes
            # (middle strike shared by short call and short put)
            if len(calls) == 2 and len(puts) == 2 and len(strikes) == 3:
                call_sells = [l for l in calls if l.side == "SELL"]
                put_sells = [l for l in puts if l.side == "SELL"]
                if (
                    len(call_sells) == 1
                    and len(put_sells) == 1
                    and call_sells[0].strike == put_sells[0].strike
                ):
                    return "Iron Butterfly"

            # Box spread: bull call spread + bear put spread, same strikes
            if len(calls) == 2 and len(puts) == 2 and len(strikes) == 2:
                return "Box Spread"

        return "Custom Strategy"

    # -----------------------------------------------------------------
    # Pre-built strategy constructors
    # -----------------------------------------------------------------

    @staticmethod
    def iron_condor(
        short_call_strike: float,
        long_call_strike: float,
        short_put_strike: float,
        long_put_strike: float,
        short_call_premium: float,
        long_call_premium: float,
        short_put_premium: float,
        long_put_premium: float,
        lots: int = 1,
        lot_size: int = 25,
    ) -> list[OptionLeg]:
        """Create an iron condor leg set.

        Structure:
            * Sell OTM call  (short_call_strike)
            * Buy further OTM call  (long_call_strike)
            * Sell OTM put  (short_put_strike)
            * Buy further OTM put  (long_put_strike)

        Parameters
        ----------
        short_call_strike, long_call_strike:
            Call wing strikes (long > short).
        short_put_strike, long_put_strike:
            Put wing strikes (long < short).
        short_call_premium, long_call_premium:
            Premiums per unit for the call legs.
        short_put_premium, long_put_premium:
            Premiums per unit for the put legs.
        lots:
            Number of lots per leg.
        lot_size:
            Units per lot.

        Returns
        -------
        list[OptionLeg]
        """
        return [
            OptionLeg(
                strike=short_call_strike, option_type="CE", side="SELL",
                quantity=lots, lot_size=lot_size, premium=short_call_premium,
            ),
            OptionLeg(
                strike=long_call_strike, option_type="CE", side="BUY",
                quantity=lots, lot_size=lot_size, premium=long_call_premium,
            ),
            OptionLeg(
                strike=short_put_strike, option_type="PE", side="SELL",
                quantity=lots, lot_size=lot_size, premium=short_put_premium,
            ),
            OptionLeg(
                strike=long_put_strike, option_type="PE", side="BUY",
                quantity=lots, lot_size=lot_size, premium=long_put_premium,
            ),
        ]

    @staticmethod
    def straddle(
        strike: float,
        call_premium: float,
        put_premium: float,
        side: str = "SELL",
        lots: int = 1,
        lot_size: int = 25,
    ) -> list[OptionLeg]:
        """Create a straddle leg set.

        Parameters
        ----------
        strike:
            ATM strike for both legs.
        call_premium:
            Premium per unit for the call.
        put_premium:
            Premium per unit for the put.
        side:
            ``"BUY"`` for long straddle, ``"SELL"`` for short straddle.
        lots:
            Number of lots per leg.
        lot_size:
            Units per lot.

        Returns
        -------
        list[OptionLeg]
        """
        return [
            OptionLeg(
                strike=strike, option_type="CE", side=side,
                quantity=lots, lot_size=lot_size, premium=call_premium,
            ),
            OptionLeg(
                strike=strike, option_type="PE", side=side,
                quantity=lots, lot_size=lot_size, premium=put_premium,
            ),
        ]

    @staticmethod
    def strangle(
        call_strike: float,
        put_strike: float,
        call_premium: float,
        put_premium: float,
        side: str = "SELL",
        lots: int = 1,
        lot_size: int = 25,
    ) -> list[OptionLeg]:
        """Create a strangle leg set.

        Parameters
        ----------
        call_strike:
            OTM call strike (above spot).
        put_strike:
            OTM put strike (below spot).
        call_premium:
            Premium per unit for the call.
        put_premium:
            Premium per unit for the put.
        side:
            ``"BUY"`` for long strangle, ``"SELL"`` for short strangle.
        lots:
            Number of lots per leg.
        lot_size:
            Units per lot.

        Returns
        -------
        list[OptionLeg]
        """
        return [
            OptionLeg(
                strike=call_strike, option_type="CE", side=side,
                quantity=lots, lot_size=lot_size, premium=call_premium,
            ),
            OptionLeg(
                strike=put_strike, option_type="PE", side=side,
                quantity=lots, lot_size=lot_size, premium=put_premium,
            ),
        ]

    @staticmethod
    def vertical_spread(
        long_strike: float,
        short_strike: float,
        option_type: str = "CE",
        long_premium: float = 0.0,
        short_premium: float = 0.0,
        lots: int = 1,
        lot_size: int = 25,
    ) -> list[OptionLeg]:
        """Create a bull/bear vertical spread.

        For a **bull call spread**: ``long_strike < short_strike``.
        For a **bear call spread**: ``long_strike > short_strike``.
        For a **bull put spread**: ``short_strike < long_strike`` with ``option_type="PE"``.

        Parameters
        ----------
        long_strike:
            Strike of the bought leg.
        short_strike:
            Strike of the sold leg.
        option_type:
            ``"CE"`` or ``"PE"``.
        long_premium:
            Premium per unit paid for the long leg.
        short_premium:
            Premium per unit received for the short leg.
        lots:
            Number of lots per leg.
        lot_size:
            Units per lot.

        Returns
        -------
        list[OptionLeg]
        """
        return [
            OptionLeg(
                strike=long_strike, option_type=option_type, side="BUY",
                quantity=lots, lot_size=lot_size, premium=long_premium,
            ),
            OptionLeg(
                strike=short_strike, option_type=option_type, side="SELL",
                quantity=lots, lot_size=lot_size, premium=short_premium,
            ),
        ]

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _default_range(
        legs: list[OptionLeg | FutureLeg],
    ) -> tuple[float, float]:
        """Derive a sensible price range from the leg strikes/entries.

        The range spans from 20 % below the lowest strike to 20 % above
        the highest strike.
        """
        ref_prices: list[float] = []
        for leg in legs:
            if isinstance(leg, OptionLeg):
                ref_prices.append(leg.strike)
            elif isinstance(leg, FutureLeg):
                ref_prices.append(leg.entry_price)

        if not ref_prices:
            return (0.0, 100.0)

        lo = min(ref_prices)
        hi = max(ref_prices)
        margin = max((hi - lo) * 0.5, lo * 0.2)
        return (lo - margin, hi + margin)

    @staticmethod
    def _compute_max_profit(
        legs: list[OptionLeg | FutureLeg], payoffs: np.ndarray
    ) -> float | None:
        """Estimate max profit.

        Returns ``None`` (unlimited) if the payoff at the edges of the
        evaluation range is still increasing, indicating unbounded
        upside.
        """
        max_val = float(np.max(payoffs))

        # Check if payoff is still rising at the boundaries
        has_naked_long_call = any(
            isinstance(l, OptionLeg)
            and l.option_type.upper() == "CE"
            and l.side == "BUY"
            for l in legs
        )
        has_naked_long_future = any(
            isinstance(l, FutureLeg) and l.side == "BUY"
            for l in legs
        )

        # A net long call or future position has unlimited upside
        net_call_qty = sum(
            l.net_quantity
            for l in legs
            if isinstance(l, OptionLeg) and l.option_type.upper() == "CE"
        )
        net_future_qty = sum(
            l.net_quantity for l in legs if isinstance(l, FutureLeg)
        )

        if net_call_qty > 0 or net_future_qty > 0:
            return None  # unlimited profit

        # A net short put position also has profit bounded (premium received)
        # but a net long put has profit bounded by strike going to zero.
        # If the last few payoff values are still strictly increasing
        # at the upper boundary, treat as unlimited.
        if len(payoffs) >= 3:
            if payoffs[-1] > payoffs[-2] > payoffs[-3]:
                slope = payoffs[-1] - payoffs[-2]
                if abs(slope) > 0.01:
                    return None

        return max_val

    @staticmethod
    def _compute_max_loss(
        legs: list[OptionLeg | FutureLeg], payoffs: np.ndarray
    ) -> float | None:
        """Estimate max loss.

        Returns ``None`` (unlimited) if the payoff at the edges is still
        decreasing, indicating unbounded downside.
        """
        min_val = float(np.min(payoffs))

        # Net short call has unlimited loss
        net_call_qty = sum(
            l.net_quantity
            for l in legs
            if isinstance(l, OptionLeg) and l.option_type.upper() == "CE"
        )
        net_future_qty = sum(
            l.net_quantity for l in legs if isinstance(l, FutureLeg)
        )

        if net_call_qty < 0 or net_future_qty != 0:
            # Check if payoff is still decreasing at the boundary
            if len(payoffs) >= 3:
                # Check upper boundary for short calls
                if net_call_qty < 0 and payoffs[-1] < payoffs[-2] < payoffs[-3]:
                    slope = payoffs[-1] - payoffs[-2]
                    if abs(slope) > 0.01:
                        return None
                # Check lower boundary for long futures / short puts
                if net_future_qty > 0 and payoffs[0] < payoffs[1] < payoffs[2]:
                    slope = payoffs[1] - payoffs[0]
                    if abs(slope) > 0.01:
                        return None
                # Short future: loss increases as price rises
                if net_future_qty < 0 and payoffs[-1] < payoffs[-2] < payoffs[-3]:
                    slope = payoffs[-1] - payoffs[-2]
                    if abs(slope) > 0.01:
                        return None

        # Net short put with no hedge: loss bounded by strike * qty (underlying -> 0)
        net_put_qty = sum(
            l.net_quantity
            for l in legs
            if isinstance(l, OptionLeg) and l.option_type.upper() == "PE"
        )
        if net_put_qty < 0:
            # Check lower boundary
            if len(payoffs) >= 3 and payoffs[0] < payoffs[1] < payoffs[2]:
                slope = payoffs[1] - payoffs[0]
                if abs(slope) > 0.01:
                    return None

        return min_val
