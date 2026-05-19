"""
Portfolio-level Greeks aggregation from individual option positions.

Maintains per-position greeks, aggregates them at strategy and portfolio
level, and checks configurable exposure limits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.models import PortfolioGreeks

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal storage
# ---------------------------------------------------------------------------

_GREEK_KEYS = ("delta", "gamma", "theta", "vega", "rho")


@dataclass
class _PositionGreeks:
    """Internal container for a single position's greeks."""

    position_key: str
    strategy_id: str
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "rho": self.rho,
        }


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class GreeksAggregator:
    """Aggregates option greeks at strategy and portfolio level.

    Stores greeks for every open option position and provides fast
    aggregation queries:

    * **Position-level**: retrieve the greeks stored for a single key.
    * **Strategy-level**: sum greeks across all positions belonging to a
      given *strategy_id*.
    * **Portfolio-level**: sum greeks across *all* tracked positions and
      return a :class:`~core.models.PortfolioGreeks` instance.

    A simple limit-checking method (:meth:`check_limits`) compares the
    absolute portfolio-level delta, gamma and vega against user-supplied
    maximums and returns a list of breach descriptions (empty when all
    limits are satisfied).

    Usage::

        agg = GreeksAggregator()
        agg.update_position_greeks(
            "NIFTY24200CE", "strat_1",
            delta=0.5, gamma=0.001, theta=-15, vega=12,
        )
        portfolio = agg.get_portfolio_greeks()
    """

    def __init__(self) -> None:
        # position_key -> _PositionGreeks
        self._position_greeks: dict[str, _PositionGreeks] = {}
        # position_key -> strategy_id  (derived, kept in sync)
        self._position_to_strategy: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def update_position_greeks(
        self,
        position_key: str,
        strategy_id: str,
        *,
        delta: float = 0.0,
        gamma: float = 0.0,
        theta: float = 0.0,
        vega: float = 0.0,
        rho: float = 0.0,
    ) -> None:
        """Create or update greeks for a single position.

        Args:
            position_key: Unique identifier for the position (e.g.
                ``"NIFTY24200CE"``).
            strategy_id: The strategy that owns this position.
            delta: Position delta.
            gamma: Position gamma.
            theta: Position theta (typically negative for long options).
            vega: Position vega.
            rho: Position rho.
        """
        pg = self._position_greeks.get(position_key)
        if pg is None:
            pg = _PositionGreeks(position_key=position_key, strategy_id=strategy_id)
            self._position_greeks[position_key] = pg
            logger.debug(
                "Greeks tracking started for position %s (strategy=%s)",
                position_key,
                strategy_id,
            )
        else:
            # Strategy re-assignment is allowed (position may move)
            if pg.strategy_id != strategy_id:
                logger.info(
                    "Position %s re-assigned from strategy %s to %s",
                    position_key,
                    pg.strategy_id,
                    strategy_id,
                )
            pg.strategy_id = strategy_id

        pg.delta = delta
        pg.gamma = gamma
        pg.theta = theta
        pg.vega = vega
        pg.rho = rho

        self._position_to_strategy[position_key] = strategy_id

    def remove_position(self, position_key: str) -> None:
        """Remove a position from the aggregator (e.g. after it is closed)."""
        removed = self._position_greeks.pop(position_key, None)
        self._position_to_strategy.pop(position_key, None)
        if removed is not None:
            logger.debug("Greeks removed for position %s", position_key)
        else:
            logger.debug(
                "remove_position called for unknown key %s (no-op)",
                position_key,
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position_greeks(self, position_key: str) -> dict[str, float] | None:
        """Return the greeks dict for a single position, or ``None``."""
        pg = self._position_greeks.get(position_key)
        if pg is None:
            return None
        return pg.as_dict()

    def get_strategy_greeks(self, strategy_id: str) -> dict[str, float]:
        """Aggregate and return greeks for all positions in *strategy_id*.

        Returns a dict with keys ``delta``, ``gamma``, ``theta``, ``vega``,
        ``rho``.  If no positions belong to the strategy all values are 0.
        """
        totals: dict[str, float] = {k: 0.0 for k in _GREEK_KEYS}
        for pg in self._position_greeks.values():
            if pg.strategy_id == strategy_id:
                for k in _GREEK_KEYS:
                    totals[k] += getattr(pg, k)
        return totals

    def get_portfolio_greeks(self) -> PortfolioGreeks:
        """Aggregate greeks across **all** tracked positions.

        Returns:
            A :class:`~core.models.PortfolioGreeks` instance with current
            totals and a UTC timestamp.
        """
        totals: dict[str, float] = {k: 0.0 for k in _GREEK_KEYS}
        for pg in self._position_greeks.values():
            for k in _GREEK_KEYS:
                totals[k] += getattr(pg, k)

        return PortfolioGreeks(
            net_delta=totals["delta"],
            net_gamma=totals["gamma"],
            net_theta=totals["theta"],
            net_vega=totals["vega"],
            net_rho=totals["rho"],
            timestamp=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Limit checking
    # ------------------------------------------------------------------

    def check_limits(
        self,
        max_delta: float,
        max_gamma: float,
        max_vega: float,
    ) -> list[str]:
        """Check portfolio-level greeks against absolute limits.

        Args:
            max_delta: Maximum allowed absolute net delta.
            max_gamma: Maximum allowed absolute net gamma.
            max_vega: Maximum allowed absolute net vega.

        Returns:
            A list of human-readable breach descriptions.  An empty list
            means all greeks are within limits.
        """
        portfolio = self.get_portfolio_greeks()
        breaches: list[str] = []

        if max_delta > 0 and abs(portfolio.net_delta) > max_delta:
            breaches.append(
                f"Net delta {portfolio.net_delta:.4f} exceeds limit "
                f"{max_delta:.4f}"
            )
        if max_gamma > 0 and abs(portfolio.net_gamma) > max_gamma:
            breaches.append(
                f"Net gamma {portfolio.net_gamma:.6f} exceeds limit "
                f"{max_gamma:.6f}"
            )
        if max_vega > 0 and abs(portfolio.net_vega) > max_vega:
            breaches.append(
                f"Net vega {portfolio.net_vega:.4f} exceeds limit "
                f"{max_vega:.4f}"
            )

        if breaches:
            logger.warning("Greeks limit breaches: %s", breaches)

        return breaches

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Remove all tracked positions and greeks."""
        count = len(self._position_greeks)
        self._position_greeks.clear()
        self._position_to_strategy.clear()
        logger.info("GreeksAggregator reset: cleared %d positions", count)

    @property
    def position_count(self) -> int:
        """Number of positions currently tracked."""
        return len(self._position_greeks)

    @property
    def strategy_ids(self) -> set[str]:
        """Set of strategy IDs that have at least one tracked position."""
        return {pg.strategy_id for pg in self._position_greeks.values()}
