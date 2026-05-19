"""Real-time option chain aggregator built from incoming tick data.

Aggregates ticks for individual option contracts into a structured option
chain view per underlying symbol and expiry.  Supports external greek
updates (from the greeks engine), ATM strike detection, PCR computation,
max-pain calculation, and OI change tracking.

Typical usage::

    builder = OptionChainBuilder()

    # Register known option instruments at startup:
    builder.register_instrument(
        instrument_id="NFO:NIFTY24MAR22000CE",
        underlying="NIFTY",
        expiry=date(2024, 3, 28),
        strike=Decimal("22000"),
        option_type="CE",
    )

    # On each underlying tick:
    builder.update_underlying_price("NIFTY", Decimal("22050.30"))

    # On each option tick:
    builder.update_from_tick(tick)

    # Query:
    chain = builder.get_chain("NIFTY", date(2024, 3, 28))
    atm = builder.get_atm_strike("NIFTY", date(2024, 3, 28))
    pcr = builder.get_pcr("NIFTY", date(2024, 3, 28))
    max_pain = builder.compute_max_pain("NIFTY", date(2024, 3, 28))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from core.models import OptionChain, OptionContract, OptionType, Tick


# ---------------------------------------------------------------------------
# Internal state dataclasses
# ---------------------------------------------------------------------------


@dataclass
class _ContractState:
    """Mutable state for a single option contract within a chain.

    Attributes:
        strike: The strike price of the contract.
        option_type: ``"CE"`` for call, ``"PE"`` for put.
        ltp: Last traded price.
        bid: Best bid price.
        ask: Best ask (offer) price.
        volume: Cumulative traded volume for the day.
        oi: Current open interest.
        oi_change: Change in open interest from the previous session.
        iv: Implied volatility (set externally by the greeks engine).
        delta: Option delta.
        gamma: Option gamma.
        theta: Option theta.
        vega: Option vega.
        rho: Option rho.
        last_update: Timestamp of the most recent update.
    """

    strike: Decimal
    option_type: str  # "CE" or "PE"
    ltp: Decimal = Decimal("0")
    bid: Decimal = Decimal("0")
    ask: Decimal = Decimal("0")
    volume: int = 0
    oi: int = 0
    oi_change: int = 0
    iv: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    last_update: datetime | None = None


@dataclass
class _OptionChainState:
    """Mutable aggregate state for all contracts sharing the same
    underlying and expiry.

    Attributes:
        underlying: The underlying symbol (e.g. ``"NIFTY"``).
        expiry: Expiry date.
        contracts: Mapping of instrument_id to its ``_ContractState``.
        underlying_price: Latest spot / underlying price.
        last_update: Timestamp of the most recent tick processed for
            any contract in this chain.
    """

    underlying: str
    expiry: date
    contracts: dict[str, _ContractState] = field(default_factory=dict)
    underlying_price: Decimal = Decimal("0")
    last_update: datetime | None = None


# ---------------------------------------------------------------------------
# OptionChainBuilder
# ---------------------------------------------------------------------------


class OptionChainBuilder:
    """Builds and maintains real-time option chains from tick data.

    Aggregates ticks for individual option contracts into a structured
    option chain view per underlying symbol and expiry.  Updates greeks
    when provided externally (from the greeks engine).

    Features:

    - Real-time option chain per underlying/expiry combination
    - ATM strike auto-detection from underlying price
    - PCR (Put-Call Ratio) computation from OI
    - Max pain calculation
    - OI change tracking
    - Snapshot capture for storage

    Thread-safety note:
        This class is *not* thread-safe.  External synchronisation is
        required if ticks arrive from multiple threads.
    """

    def __init__(self) -> None:
        # Key: (underlying, expiry) -> _OptionChainState
        self._chains: dict[tuple[str, date], _OptionChainState] = {}
        self._underlying_prices: dict[str, Decimal] = {}  # symbol -> last price
        self._instrument_to_chain: dict[str, tuple[str, date]] = {}  # instrument_id -> chain key
        self._total_ticks: int = 0

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_instrument(
        self,
        instrument_id: str,
        underlying: str,
        expiry: date,
        strike: Decimal,
        option_type: str,
    ) -> None:
        """Register an option instrument for chain tracking.

        Must be called before ticks for *instrument_id* are processed.
        Calling this method multiple times with the same *instrument_id*
        is safe and will update the existing registration.

        Args:
            instrument_id: Unique identifier for the option contract
                (e.g. ``"NFO:NIFTY24MAR22000CE"``).
            underlying: Underlying symbol (e.g. ``"NIFTY"``).
            expiry: Contract expiry date.
            strike: Strike price.
            option_type: ``"CE"`` for call or ``"PE"`` for put.

        Raises:
            ValueError: If *option_type* is not ``"CE"`` or ``"PE"``.
        """
        if option_type not in ("CE", "PE"):
            raise ValueError(f"option_type must be 'CE' or 'PE', got {option_type!r}")

        chain_key = (underlying, expiry)

        # Ensure chain state exists.
        if chain_key not in self._chains:
            self._chains[chain_key] = _OptionChainState(
                underlying=underlying,
                expiry=expiry,
            )

        chain = self._chains[chain_key]

        # Ensure contract state exists.
        if instrument_id not in chain.contracts:
            chain.contracts[instrument_id] = _ContractState(
                strike=strike,
                option_type=option_type,
            )

        # Update the reverse lookup.
        self._instrument_to_chain[instrument_id] = chain_key

    # ------------------------------------------------------------------
    # Price updates
    # ------------------------------------------------------------------

    def update_underlying_price(self, underlying: str, price: Decimal) -> None:
        """Update the spot / underlying price.

        This price is used for ATM strike detection and is included in
        option chain snapshots.

        Args:
            underlying: Underlying symbol (e.g. ``"NIFTY"``).
            price: Latest spot price.
        """
        self._underlying_prices[underlying] = price

        # Also propagate to every chain that references this underlying.
        for (u, _exp), chain in self._chains.items():
            if u == underlying:
                chain.underlying_price = price

    def update_from_tick(self, tick: Tick) -> None:
        """Update option chain state from an incoming option tick.

        If the tick's *instrument_id* has not been registered via
        :meth:`register_instrument`, the tick is silently ignored.

        Args:
            tick: A ``core.models.Tick`` instance for an option contract.
        """
        chain_key = self._instrument_to_chain.get(tick.instrument_id)
        if chain_key is None:
            return  # unregistered instrument — ignore

        self._total_ticks += 1
        chain = self._chains[chain_key]
        contract = chain.contracts[tick.instrument_id]

        contract.ltp = tick.ltp
        contract.bid = tick.bid
        contract.ask = tick.ask
        contract.volume = tick.volume
        contract.oi = tick.oi
        contract.oi_change = tick.oi_change
        contract.last_update = tick.timestamp

        chain.last_update = tick.timestamp

    # ------------------------------------------------------------------
    # Greeks (set externally)
    # ------------------------------------------------------------------

    def update_greeks(
        self,
        instrument_id: str,
        iv: float,
        delta: float,
        gamma: float,
        theta: float,
        vega: float,
        rho: float = 0.0,
    ) -> None:
        """Update greeks for a specific option contract.

        Typically called by the greeks engine after it computes implied
        volatility and sensitivities.

        Args:
            instrument_id: Unique option contract identifier.
            iv: Implied volatility.
            delta: Option delta.
            gamma: Option gamma.
            theta: Option theta.
            vega: Option vega.
            rho: Option rho (default ``0.0``).
        """
        chain_key = self._instrument_to_chain.get(instrument_id)
        if chain_key is None:
            return

        contract = self._chains[chain_key].contracts.get(instrument_id)
        if contract is None:
            return

        contract.iv = iv
        contract.delta = delta
        contract.gamma = gamma
        contract.theta = theta
        contract.vega = vega
        contract.rho = rho

    # ------------------------------------------------------------------
    # Chain queries
    # ------------------------------------------------------------------

    def get_chain(self, underlying: str, expiry: date) -> OptionChain | None:
        """Get the full option chain for a given underlying and expiry.

        Returns a ``core.models.OptionChain`` instance populated from the
        current internal state.

        Args:
            underlying: Underlying symbol (e.g. ``"NIFTY"``).
            expiry: Contract expiry date.

        Returns:
            An ``OptionChain`` model, or ``None`` if no chain is tracked
            for the given underlying/expiry pair.
        """
        chain_key = (underlying, expiry)
        chain = self._chains.get(chain_key)
        if chain is None:
            return None

        contracts: list[OptionContract] = []
        for _iid, cs in chain.contracts.items():
            contracts.append(
                OptionContract(
                    instrument=_stub_option_instrument(
                        underlying=underlying,
                        expiry=expiry,
                        strike=cs.strike,
                        option_type=cs.option_type,
                    ),
                    strike=cs.strike,
                    option_type=OptionType(cs.option_type),
                    expiry=expiry,
                    ltp=cs.ltp,
                    bid=cs.bid,
                    ask=cs.ask,
                    volume=cs.volume,
                    oi=cs.oi,
                    oi_change=cs.oi_change,
                    iv=cs.iv,
                    delta=cs.delta,
                    gamma=cs.gamma,
                    theta=cs.theta,
                    vega=cs.vega,
                    rho=cs.rho,
                )
            )

        # Sort contracts by strike then option type for consistent ordering.
        contracts.sort(key=lambda c: (c.strike, c.option_type.value))

        underlying_price = chain.underlying_price
        atm = self.get_atm_strike(underlying, expiry) or Decimal("0")
        pcr = self.get_pcr(underlying, expiry) or 0.0

        return OptionChain(
            underlying_symbol=underlying,
            underlying_price=underlying_price,
            expiry=expiry,
            timestamp=chain.last_update or datetime.now(timezone.utc),
            contracts=contracts,
            atm_strike=atm,
            pcr=pcr,
        )

    def get_atm_strike(self, underlying: str, expiry: date) -> Decimal | None:
        """Return the at-the-money strike for the given chain.

        The ATM strike is the strike price closest to the current
        underlying price.

        Args:
            underlying: Underlying symbol.
            expiry: Contract expiry date.

        Returns:
            The ATM strike as a ``Decimal``, or ``None`` if the chain
            does not exist or the underlying price is unknown.
        """
        chain = self._chains.get((underlying, expiry))
        if chain is None:
            return None

        spot = chain.underlying_price
        if spot == Decimal("0"):
            return None

        # Collect unique strikes.
        strikes = {cs.strike for cs in chain.contracts.values()}
        if not strikes:
            return None

        return min(strikes, key=lambda s: abs(s - spot))

    def get_pcr(self, underlying: str, expiry: date) -> float | None:
        """Return the OI-based put-call ratio for the given chain.

        PCR = total put OI / total call OI.

        Args:
            underlying: Underlying symbol.
            expiry: Contract expiry date.

        Returns:
            The PCR as a ``float``, or ``None`` if the chain does not
            exist or there is no call OI.
        """
        chain = self._chains.get((underlying, expiry))
        if chain is None:
            return None

        total_call_oi = 0
        total_put_oi = 0
        for cs in chain.contracts.values():
            if cs.option_type == "CE":
                total_call_oi += cs.oi
            else:
                total_put_oi += cs.oi

        if total_call_oi == 0:
            return None

        return total_put_oi / total_call_oi

    def compute_max_pain(self, underlying: str, expiry: date) -> Decimal | None:
        """Compute the max pain strike for the given chain.

        Max pain is the strike price at which option writers (sellers)
        suffer the minimum aggregate loss if the underlying expires at
        that price.

        Algorithm:
            1. For each candidate strike *S* present in the chain:
            2. For each **call** contract: if S > call_strike, writers
               lose ``(S - call_strike) * call_OI``.
            3. For each **put** contract: if S < put_strike, writers
               lose ``(put_strike - S) * put_OI``.
            4. Sum total losses across all contracts for each S.
            5. Max pain = S with the minimum total loss.

        Args:
            underlying: Underlying symbol.
            expiry: Contract expiry date.

        Returns:
            The max pain strike as a ``Decimal``, or ``None`` if the chain
            does not exist or contains no contracts.
        """
        chain = self._chains.get((underlying, expiry))
        if chain is None:
            return None

        if not chain.contracts:
            return None

        # Collect unique strikes.
        strikes = sorted({cs.strike for cs in chain.contracts.values()})
        if not strikes:
            return None

        # Pre-collect calls and puts with their (strike, oi) for efficiency.
        calls: list[tuple[Decimal, int]] = []
        puts: list[tuple[Decimal, int]] = []
        for cs in chain.contracts.values():
            if cs.option_type == "CE":
                calls.append((cs.strike, cs.oi))
            else:
                puts.append((cs.strike, cs.oi))

        min_loss = None
        max_pain_strike = strikes[0]

        for s in strikes:
            total_loss = Decimal("0")

            # Call writer losses: if S > call_strike, loss = (S - call_strike) * OI
            for call_strike, call_oi in calls:
                if s > call_strike:
                    total_loss += (s - call_strike) * call_oi

            # Put writer losses: if S < put_strike, loss = (put_strike - S) * OI
            for put_strike, put_oi in puts:
                if s < put_strike:
                    total_loss += (put_strike - s) * put_oi

            if min_loss is None or total_loss < min_loss:
                min_loss = total_loss
                max_pain_strike = s

        return max_pain_strike

    def get_oi_change_summary(self, underlying: str, expiry: date) -> dict:
        """Return an OI change summary for the given chain.

        The summary includes total call OI change, total put OI change,
        and the top strikes by absolute OI change (call and put
        separately).

        Args:
            underlying: Underlying symbol.
            expiry: Contract expiry date.

        Returns:
            A dictionary with keys:

            - ``total_call_oi_change`` (``int``)
            - ``total_put_oi_change`` (``int``)
            - ``top_call_oi_buildup`` (``list[dict]``): top 5 call
              strikes by absolute OI change.
            - ``top_put_oi_buildup`` (``list[dict]``): top 5 put
              strikes by absolute OI change.

            Returns an empty dict if the chain is not found.
        """
        chain = self._chains.get((underlying, expiry))
        if chain is None:
            return {}

        total_call_oi_change = 0
        total_put_oi_change = 0
        call_changes: list[dict] = []
        put_changes: list[dict] = []

        for cs in chain.contracts.values():
            if cs.option_type == "CE":
                total_call_oi_change += cs.oi_change
                call_changes.append({
                    "strike": cs.strike,
                    "oi_change": cs.oi_change,
                    "oi": cs.oi,
                })
            else:
                total_put_oi_change += cs.oi_change
                put_changes.append({
                    "strike": cs.strike,
                    "oi_change": cs.oi_change,
                    "oi": cs.oi,
                })

        # Sort by absolute OI change descending, take top 5.
        call_changes.sort(key=lambda x: abs(x["oi_change"]), reverse=True)
        put_changes.sort(key=lambda x: abs(x["oi_change"]), reverse=True)

        return {
            "total_call_oi_change": total_call_oi_change,
            "total_put_oi_change": total_put_oi_change,
            "top_call_oi_buildup": call_changes[:5],
            "top_put_oi_buildup": put_changes[:5],
        }

    def take_snapshot(self, underlying: str, expiry: date) -> dict | None:
        """Capture a point-in-time snapshot of the chain for storage.

        The snapshot is a plain dictionary suitable for serialisation
        (e.g. to JSON or a database).

        Args:
            underlying: Underlying symbol.
            expiry: Contract expiry date.

        Returns:
            A dictionary containing the full chain state, or ``None`` if
            the chain is not found.
        """
        chain = self._chains.get((underlying, expiry))
        if chain is None:
            return None

        contracts_snapshot: list[dict] = []
        for iid, cs in chain.contracts.items():
            contracts_snapshot.append({
                "instrument_id": iid,
                "strike": str(cs.strike),
                "option_type": cs.option_type,
                "ltp": str(cs.ltp),
                "bid": str(cs.bid),
                "ask": str(cs.ask),
                "volume": cs.volume,
                "oi": cs.oi,
                "oi_change": cs.oi_change,
                "iv": cs.iv,
                "delta": cs.delta,
                "gamma": cs.gamma,
                "theta": cs.theta,
                "vega": cs.vega,
                "rho": cs.rho,
                "last_update": cs.last_update.isoformat() if cs.last_update else None,
            })

        atm = self.get_atm_strike(underlying, expiry)
        pcr = self.get_pcr(underlying, expiry)
        max_pain = self.compute_max_pain(underlying, expiry)

        return {
            "underlying": underlying,
            "expiry": expiry.isoformat(),
            "underlying_price": str(chain.underlying_price),
            "atm_strike": str(atm) if atm is not None else None,
            "pcr": pcr,
            "max_pain": str(max_pain) if max_pain is not None else None,
            "last_update": chain.last_update.isoformat() if chain.last_update else None,
            "contracts": contracts_snapshot,
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tracked_chains(self) -> list[tuple[str, date]]:
        """List of ``(underlying, expiry)`` tuples currently tracked.

        Returns:
            A list of chain keys.
        """
        return list(self._chains.keys())

    @property
    def metrics(self) -> dict:
        """Operational metrics for monitoring and diagnostics.

        Returns:
            A dictionary containing:

            - ``chains_tracked``: number of unique (underlying, expiry)
              chains.
            - ``total_contracts``: total number of registered option
              contracts across all chains.
            - ``total_ticks_processed``: cumulative tick count.
            - ``per_chain_contracts``: mapping of chain key (as string)
              to its contract count.
        """
        per_chain: dict[str, int] = {}
        total_contracts = 0
        for (u, exp), chain in self._chains.items():
            count = len(chain.contracts)
            per_chain[f"{u}:{exp.isoformat()}"] = count
            total_contracts += count

        return {
            "chains_tracked": len(self._chains),
            "total_contracts": total_contracts,
            "total_ticks_processed": self._total_ticks,
            "per_chain_contracts": per_chain,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_option_instrument(
    underlying: str,
    expiry: date,
    strike: Decimal,
    option_type: str,
) -> "core.models.Instrument":  # noqa: F821 — forward ref for type hint
    """Create a minimal ``Instrument`` for embedding in ``OptionContract``.

    This is a convenience helper so that ``get_chain`` can return fully
    formed ``OptionContract`` objects without requiring the caller to
    supply the full ``Instrument`` metadata.
    """
    from core.models import Exchange, InstrumentType, Instrument, Segment

    inst_type = (
        InstrumentType.CALL_OPTION if option_type == "CE" else InstrumentType.PUT_OPTION
    )
    ot = OptionType(option_type)

    return Instrument(
        symbol=f"{underlying}{expiry.strftime('%y%b').upper()}{strike}{option_type}",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=inst_type,
        expiry=expiry,
        strike=strike,
        option_type=ot,
        underlying=underlying,
    )
