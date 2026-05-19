"""Implied Volatility Surface construction and analysis.

Builds IV surfaces (volatility smiles across strikes and expiries) from
market option-chain data.  Supports interpolation, skew/butterfly metrics,
anomaly detection, and matrix export for 3-D visualisation.

Designed for Indian index options (NIFTY / BANKNIFTY) where European-style
pricing applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class IVPoint:
    """A single point on the IV surface."""

    strike: float
    expiry: date
    time_to_expiry: float  # in years
    iv: float
    option_type: str  # "CE" or "PE"
    moneyness: float  # strike / spot ratio
    delta: float = 0.0


@dataclass
class IVSmile:
    """IV smile for a single expiry.

    Attributes:
        expiry: Expiry date of the smile.
        time_to_expiry: Annualised time to expiry.
        points: Individual IV observations sorted by strike.
        atm_iv: At-the-money implied volatility (moneyness closest to 1.0).
        skew_25d: 25-delta risk-reversal  (25d-put IV minus 25d-call IV).
            A positive value means puts are more expensive than calls.
        butterfly_25d: 25-delta butterfly spread
            ``(25d-put IV + 25d-call IV) / 2  -  ATM IV``.
            Measures the "curvature" of the smile.
    """

    expiry: date
    time_to_expiry: float
    points: list[IVPoint] = field(default_factory=list)
    atm_iv: float = 0.0
    skew_25d: float = 0.0
    butterfly_25d: float = 0.0


@dataclass
class IVSurface:
    """Complete IV surface across strikes and expiries.

    Smiles are ordered by ascending ``time_to_expiry``.
    """

    underlying: str
    spot_price: float
    timestamp: Any = None
    smiles: list[IVSmile] = field(default_factory=list)

    # -----------------------------------------------------------------
    # Interpolation helpers
    # -----------------------------------------------------------------

    def get_iv(self, strike: float, time_to_expiry: float) -> float | None:
        """Interpolate IV for any strike / expiry point on the surface.

        Uses bilinear interpolation between the four nearest grid points
        (two adjacent expiries, two adjacent strikes on each smile).

        Returns ``None`` if the surface has insufficient data or if the
        requested point falls outside the grid.
        """
        if len(self.smiles) == 0:
            return None

        # Sort smiles by time-to-expiry for ordered searching
        sorted_smiles = sorted(self.smiles, key=lambda s: s.time_to_expiry)
        ttes = [s.time_to_expiry for s in sorted_smiles]

        # --- Locate bracketing expiries ----------------------------------
        if time_to_expiry <= ttes[0]:
            # Clamp to nearest smile
            return self._interp_smile(sorted_smiles[0], strike)
        if time_to_expiry >= ttes[-1]:
            return self._interp_smile(sorted_smiles[-1], strike)

        # Binary-search style bracketing
        idx_upper = 0
        for i, t in enumerate(ttes):
            if t >= time_to_expiry:
                idx_upper = i
                break

        idx_lower = idx_upper - 1
        smile_lo = sorted_smiles[idx_lower]
        smile_hi = sorted_smiles[idx_upper]

        iv_lo = self._interp_smile(smile_lo, strike)
        iv_hi = self._interp_smile(smile_hi, strike)

        if iv_lo is None or iv_hi is None:
            return iv_lo if iv_hi is None else iv_hi

        # Linear interpolation along the time axis
        t_lo = ttes[idx_lower]
        t_hi = ttes[idx_upper]
        if t_hi == t_lo:
            return iv_lo
        weight = (time_to_expiry - t_lo) / (t_hi - t_lo)
        return iv_lo + weight * (iv_hi - iv_lo)

    def get_term_structure(
        self, moneyness: float = 1.0
    ) -> list[tuple[float, float]]:
        """ATM (or given moneyness) IV term structure across expiries.

        For each smile the strike closest to ``spot * moneyness`` is used.

        Returns:
            Sorted list of ``(time_to_expiry, iv)`` tuples.
        """
        result: list[tuple[float, float]] = []
        target_strike = self.spot_price * moneyness

        for smile in sorted(self.smiles, key=lambda s: s.time_to_expiry):
            iv = self._interp_smile(smile, target_strike)
            if iv is not None:
                result.append((smile.time_to_expiry, iv))
        return result

    def get_skew(self, expiry: date) -> float | None:
        """Get the 25-delta skew for a specific expiry.

        Returns ``None`` if no smile exists for that expiry.
        """
        for smile in self.smiles:
            if smile.expiry == expiry:
                return smile.skew_25d
        return None

    def to_matrix(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Convert the surface to NumPy arrays for 3-D plotting.

        Returns:
            ``(strikes, expiries, iv_matrix)`` where

            * ``strikes`` — 1-D array of unique strikes across all smiles.
            * ``expiries`` — 1-D array of ``time_to_expiry`` values.
            * ``iv_matrix`` — 2-D array of shape ``(len(expiries), len(strikes))``.
              Missing values are filled via linear interpolation along the
              strike axis; remaining gaps are filled with ``NaN``.
        """
        if not self.smiles:
            return np.array([]), np.array([]), np.empty((0, 0))

        # Collect all unique strikes
        all_strikes: set[float] = set()
        for smile in self.smiles:
            for pt in smile.points:
                all_strikes.add(pt.strike)
        strikes = np.array(sorted(all_strikes))

        sorted_smiles = sorted(self.smiles, key=lambda s: s.time_to_expiry)
        expiries = np.array([s.time_to_expiry for s in sorted_smiles])

        iv_matrix = np.full((len(sorted_smiles), len(strikes)), np.nan)

        for i, smile in enumerate(sorted_smiles):
            # Build a strike -> iv mapping for this smile
            smile_strikes = np.array([p.strike for p in smile.points])
            smile_ivs = np.array([p.iv for p in smile.points])

            if len(smile_strikes) == 0:
                continue

            # Sort by strike
            order = np.argsort(smile_strikes)
            smile_strikes = smile_strikes[order]
            smile_ivs = smile_ivs[order]

            # Interpolate onto the common strike grid
            iv_matrix[i, :] = np.interp(
                strikes, smile_strikes, smile_ivs,
                left=np.nan, right=np.nan,
            )

        return strikes, expiries, iv_matrix

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _interp_smile(smile: IVSmile, strike: float) -> float | None:
        """Linearly interpolate IV within a single smile at the given strike."""
        pts = sorted(smile.points, key=lambda p: p.strike)
        if not pts:
            return None
        if len(pts) == 1:
            return pts[0].iv

        strikes = [p.strike for p in pts]
        ivs = [p.iv for p in pts]

        # Clamp to boundaries
        if strike <= strikes[0]:
            return ivs[0]
        if strike >= strikes[-1]:
            return ivs[-1]

        # Find bracketing strikes
        for j in range(len(strikes) - 1):
            if strikes[j] <= strike <= strikes[j + 1]:
                w = (strike - strikes[j]) / (strikes[j + 1] - strikes[j])
                return ivs[j] + w * (ivs[j + 1] - ivs[j])
        return None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class IVSurfaceBuilder:
    """Builds and maintains IV surfaces from market data.

    Features
    --------
    * Construct an IV smile from an option-chain snapshot for one expiry.
    * Assemble a full surface from multiple smiles.
    * Detect IV skew anomalies (unusual put/call skew vs. historical norm).
    * IV term-structure analysis.
    * Store up to one year of daily surfaces per underlying for time-series
      analysis.
    """

    def __init__(self) -> None:
        self._surfaces: dict[str, IVSurface] = {}  # underlying -> latest
        self._history: dict[str, list[IVSurface]] = {}  # underlying -> historical
        self._max_history: int = 252  # ~1 year of trading days

    # -----------------------------------------------------------------
    # Smile construction
    # -----------------------------------------------------------------

    def build_smile(
        self,
        underlying: str,
        spot: float,
        expiry: date,
        time_to_expiry: float,
        strikes: list[float],
        ivs: list[float],
        option_types: list[str],
        deltas: list[float] | None = None,
    ) -> IVSmile:
        """Build an IV smile for one expiry from market data.

        Parameters
        ----------
        underlying:
            Underlying symbol (e.g. ``"NIFTY"``).
        spot:
            Current spot price of the underlying.
        expiry:
            Expiry date for this smile.
        time_to_expiry:
            Annualised time to expiry.
        strikes:
            List of strike prices.
        ivs:
            Corresponding implied volatilities (annualised, e.g. 0.15 for 15 %).
        option_types:
            ``"CE"`` or ``"PE"`` for each strike.
        deltas:
            Optional list of option deltas (signed).  If not supplied, a
            rough delta approximation is used based on moneyness.

        Returns
        -------
        IVSmile
            Smile with ATM IV, 25-delta skew, and butterfly computed.
        """
        if not (len(strikes) == len(ivs) == len(option_types)):
            raise ValueError(
                "strikes, ivs, and option_types must have equal length"
            )

        points: list[IVPoint] = []
        for i, (k, iv, otype) in enumerate(zip(strikes, ivs, option_types)):
            moneyness = k / spot if spot > 0 else 0.0
            delta = deltas[i] if deltas is not None else self._approx_delta(
                moneyness, otype, time_to_expiry
            )
            points.append(
                IVPoint(
                    strike=k,
                    expiry=expiry,
                    time_to_expiry=time_to_expiry,
                    iv=iv,
                    option_type=otype,
                    moneyness=moneyness,
                    delta=delta,
                )
            )

        # Sort by strike for consistent ordering
        points.sort(key=lambda p: p.strike)

        # --- ATM IV: closest moneyness to 1.0 ---
        atm_iv = min(points, key=lambda p: abs(p.moneyness - 1.0)).iv

        # --- 25-delta skew and butterfly ---
        skew_25d = 0.0
        butterfly_25d = 0.0

        put_25d_iv = self._find_delta_iv(points, target_delta=-0.25, option_type="PE")
        call_25d_iv = self._find_delta_iv(points, target_delta=0.25, option_type="CE")

        if put_25d_iv is not None and call_25d_iv is not None:
            skew_25d = put_25d_iv - call_25d_iv
            butterfly_25d = 0.5 * (put_25d_iv + call_25d_iv) - atm_iv

        return IVSmile(
            expiry=expiry,
            time_to_expiry=time_to_expiry,
            points=points,
            atm_iv=atm_iv,
            skew_25d=skew_25d,
            butterfly_25d=butterfly_25d,
        )

    # -----------------------------------------------------------------
    # Surface construction
    # -----------------------------------------------------------------

    def build_surface(
        self,
        underlying: str,
        spot: float,
        smiles: list[IVSmile],
    ) -> IVSurface:
        """Build a complete IV surface from multiple smiles and store it.

        The surface is stored as the "latest" for the underlying and
        appended to the historical buffer (FIFO, capped at
        ``_max_history`` entries).

        Parameters
        ----------
        underlying:
            Underlying symbol.
        spot:
            Current spot price.
        smiles:
            List of :class:`IVSmile` objects, one per expiry.

        Returns
        -------
        IVSurface
        """
        from datetime import datetime

        surface = IVSurface(
            underlying=underlying,
            spot_price=spot,
            timestamp=datetime.now(),
            smiles=sorted(smiles, key=lambda s: s.time_to_expiry),
        )

        # Store latest
        self._surfaces[underlying] = surface

        # Append to history
        if underlying not in self._history:
            self._history[underlying] = []
        history = self._history[underlying]
        history.append(surface)
        if len(history) > self._max_history:
            history.pop(0)

        return surface

    # -----------------------------------------------------------------
    # Anomaly detection
    # -----------------------------------------------------------------

    def detect_skew_anomaly(
        self, underlying: str, threshold: float = 2.0
    ) -> list[dict]:
        """Detect unusual IV skew patterns relative to recent history.

        An anomaly is flagged when the current 25-delta skew for any expiry
        deviates more than *threshold* standard deviations from the
        historical mean skew at a comparable tenor bucket.

        Parameters
        ----------
        underlying:
            Underlying symbol to analyse.
        threshold:
            Number of standard deviations beyond which a skew value is
            considered anomalous.

        Returns
        -------
        list[dict]
            Each dict contains:

            * ``expiry`` — the anomalous expiry date.
            * ``time_to_expiry`` — annualised tenor.
            * ``current_skew`` — current 25-delta skew.
            * ``historical_mean`` — mean skew from history.
            * ``historical_std`` — std dev of historical skews.
            * ``z_score`` — how many std devs the current skew deviates.
        """
        anomalies: list[dict] = []

        current = self._surfaces.get(underlying)
        history = self._history.get(underlying, [])

        if current is None or len(history) < 5:
            # Not enough history to compute meaningful statistics
            return anomalies

        # Collect historical skews grouped into tenor buckets.
        # Bucket boundaries: weekly (<=14d), monthly (14-45d),
        # quarterly (45-120d), long (>120d).
        def _tenor_bucket(tte: float) -> str:
            days = tte * 365
            if days <= 14:
                return "weekly"
            if days <= 45:
                return "monthly"
            if days <= 120:
                return "quarterly"
            return "long"

        # Gather historical skews per bucket
        bucket_skews: dict[str, list[float]] = {
            "weekly": [], "monthly": [], "quarterly": [], "long": [],
        }
        for surf in history[:-1]:  # Exclude the latest (which is current)
            for smile in surf.smiles:
                bucket = _tenor_bucket(smile.time_to_expiry)
                bucket_skews[bucket].append(smile.skew_25d)

        # Check current smiles
        for smile in current.smiles:
            bucket = _tenor_bucket(smile.time_to_expiry)
            hist = bucket_skews[bucket]
            if len(hist) < 3:
                continue
            arr = np.array(hist)
            mean = float(np.mean(arr))
            std = float(np.std(arr, ddof=1))
            if std < 1e-10:
                continue
            z = (smile.skew_25d - mean) / std
            if abs(z) > threshold:
                anomalies.append(
                    {
                        "expiry": smile.expiry,
                        "time_to_expiry": smile.time_to_expiry,
                        "current_skew": smile.skew_25d,
                        "historical_mean": round(mean, 6),
                        "historical_std": round(std, 6),
                        "z_score": round(z, 4),
                    }
                )

        return anomalies

    # -----------------------------------------------------------------
    # Accessors
    # -----------------------------------------------------------------

    def get_surface(self, underlying: str) -> IVSurface | None:
        """Return the latest IV surface for *underlying*, or ``None``."""
        return self._surfaces.get(underlying)

    @property
    def tracked_underlyings(self) -> list[str]:
        """List of underlyings with a stored surface."""
        return list(self._surfaces.keys())

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _approx_delta(
        moneyness: float, option_type: str, tte: float
    ) -> float:
        """Quick-and-dirty delta approximation from moneyness.

        Uses the heuristic that ATM delta ~ 0.5 for calls / -0.5 for puts,
        scaling by an exponential decay in moneyness distance.  This is only
        used when explicit deltas are not supplied by the caller.
        """
        # Approximate log-moneyness sensitivity
        sigma_approx = 0.15  # assumed vol for rough delta
        sqrt_t = max(np.sqrt(tte), 1e-6)
        from scipy.stats import norm as _norm

        d1 = np.log(1.0 / moneyness) / (sigma_approx * sqrt_t) + 0.5 * sigma_approx * sqrt_t
        if option_type.upper() == "CE":
            return float(_norm.cdf(d1))
        else:
            return float(_norm.cdf(d1) - 1.0)

    @staticmethod
    def _find_delta_iv(
        points: list[IVPoint],
        target_delta: float,
        option_type: str,
    ) -> float | None:
        """Find the IV of the point closest to *target_delta* among points
        of the given option type.

        Returns ``None`` if no points of that type exist.
        """
        candidates = [p for p in points if p.option_type.upper() == option_type.upper()]
        if not candidates:
            return None
        best = min(candidates, key=lambda p: abs(p.delta - target_delta))
        return best.iv
