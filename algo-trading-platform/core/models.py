"""
Comprehensive Pydantic v2 data models for an institutional-grade algorithmic
options trading platform targeting the Indian stock market (NSE / BSE).

All models use strict type hints, ``model_config`` with ``from_attributes=True``,
and appropriate validators.  Prices that require decimal precision use
``Decimal``; greeks and analytics use ``float``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Exchange(str, Enum):
    """Supported Indian market exchanges."""

    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"
    CDS = "CDS"
    MCX = "MCX"


class Segment(str, Enum):
    """Market segments."""

    EQUITY = "EQUITY"
    FNO = "FNO"
    CURRENCY = "CURRENCY"
    COMMODITY = "COMMODITY"


class InstrumentType(str, Enum):
    """Types of tradeable instruments."""

    STOCK = "STOCK"
    INDEX = "INDEX"
    FUTURE = "FUTURE"
    CALL_OPTION = "CALL_OPTION"
    PUT_OPTION = "PUT_OPTION"


class OrderType(str, Enum):
    """Order varieties supported by Indian brokers."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL_M"
    GTT = "GTT"


class OrderSide(str, Enum):
    """Direction of an order."""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """Lifecycle states of an order."""

    PENDING = "PENDING"
    PLACED = "PLACED"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class ProductType(str, Enum):
    """Product / margin types used by Indian brokers."""

    MIS = "MIS"      # Intraday (auto-squared-off)
    NRML = "NRML"    # Overnight / carry-forward
    CNC = "CNC"      # Cash-and-carry delivery


class StrategyStatus(str, Enum):
    """Lifecycle states of a trading strategy."""

    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class TimeFrame(str, Enum):
    """Candle / bar time-frames."""

    TICK = "TICK"
    S1 = "S1"
    S5 = "S5"
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    D1 = "D1"
    W1 = "W1"
    MO1 = "MO1"


class OptionType(str, Enum):
    """Option contract type."""

    CE = "CE"
    PE = "PE"


class AlertLevel(str, Enum):
    """Severity levels for platform alerts."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class TradingMode(str, Enum):
    """Execution mode for a strategy."""

    LIVE = "LIVE"
    PAPER = "PAPER"
    BACKTEST = "BACKTEST"


# ---------------------------------------------------------------------------
# Core Data Models
# ---------------------------------------------------------------------------


class Instrument(BaseModel):
    """Represents a tradeable instrument on the Indian exchanges."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str = Field(..., description="Trading symbol, e.g. NIFTY, RELIANCE")
    exchange: Exchange
    segment: Segment
    instrument_type: InstrumentType
    lot_size: int = Field(default=1, ge=1, description="Contract / lot size")
    tick_size: Decimal = Field(
        default=Decimal("0.05"),
        ge=0,
        description="Minimum price movement",
    )
    expiry: date | None = Field(default=None, description="Expiry date for derivatives")
    strike: Decimal | None = Field(default=None, ge=0, description="Strike price for options")
    option_type: OptionType | None = Field(default=None, description="CE or PE for options")
    underlying: str | None = Field(
        default=None,
        description="Underlying symbol for derivatives, e.g. NIFTY for NIFTY options",
    )
    token: str | None = Field(
        default=None,
        description="Broker-specific instrument token / ID",
    )

    @model_validator(mode="after")
    def _validate_option_fields(self) -> "Instrument":
        """Options must have strike, option_type, and expiry."""
        if self.instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
            if self.strike is None or self.option_type is None or self.expiry is None:
                raise ValueError(
                    "Options require strike, option_type, and expiry to be set"
                )
        return self


class Tick(BaseModel):
    """A single market-data tick (best-bid / best-ask snapshot)."""

    model_config = ConfigDict(from_attributes=True)

    instrument_id: str = Field(..., description="Unique instrument identifier")
    symbol: str
    ltp: Decimal = Field(..., description="Last traded price")
    bid: Decimal = Field(default=Decimal("0"))
    ask: Decimal = Field(default=Decimal("0"))
    bid_qty: int = Field(default=0, ge=0)
    ask_qty: int = Field(default=0, ge=0)
    open: Decimal = Field(default=Decimal("0"))
    high: Decimal = Field(default=Decimal("0"))
    low: Decimal = Field(default=Decimal("0"))
    close: Decimal = Field(default=Decimal("0"))
    volume: int = Field(default=0, ge=0)
    oi: int = Field(default=0, ge=0, description="Open interest")
    oi_change: int = Field(default=0, description="Change in open interest")
    timestamp: datetime
    exchange: Exchange


class Candle(BaseModel):
    """OHLCV candle for a given time-frame."""

    model_config = ConfigDict(from_attributes=True)

    instrument_id: str
    symbol: str
    timeframe: TimeFrame
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = Field(default=0, ge=0)
    oi: int = Field(default=0, ge=0)
    timestamp: datetime
    num_ticks: int = Field(default=0, ge=0, description="Number of ticks aggregated")

    @model_validator(mode="after")
    def _validate_ohlc(self) -> "Candle":
        """High must be >= Low; Open and Close must be within [Low, High]."""
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open must be between low and high")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close must be between low and high")
        return self


class Order(BaseModel):
    """An order submitted to the broker / exchange."""

    model_config = ConfigDict(from_attributes=True)

    order_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Internal order identifier",
    )
    strategy_id: str | None = Field(default=None, description="Owning strategy ID")
    instrument: Instrument
    order_type: OrderType
    side: OrderSide
    product_type: ProductType
    quantity: int = Field(..., gt=0)
    price: Decimal | None = Field(default=None, ge=0, description="Limit price")
    trigger_price: Decimal | None = Field(
        default=None, ge=0, description="Stop-loss trigger price"
    )
    status: OrderStatus = Field(default=OrderStatus.PENDING)
    filled_quantity: int = Field(default=0, ge=0)
    average_price: Decimal = Field(default=Decimal("0"), ge=0)
    placed_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
    broker_order_id: str | None = Field(default=None)
    tag: str | None = Field(default=None, max_length=20, description="Alphanumeric tag for grouping")
    rejection_reason: str | None = Field(default=None)
    parent_order_id: str | None = Field(
        default=None,
        description="Parent order ID for bracket / cover orders",
    )

    @model_validator(mode="after")
    def _validate_prices(self) -> "Order":
        """Limit orders require a price; SL orders require a trigger price."""
        if self.order_type == OrderType.LIMIT and self.price is None:
            raise ValueError("LIMIT orders require a price")
        if self.order_type in (OrderType.SL, OrderType.SL_M) and self.trigger_price is None:
            raise ValueError("SL / SL_M orders require a trigger_price")
        return self


class Trade(BaseModel):
    """A filled trade reported by the exchange."""

    model_config = ConfigDict(from_attributes=True)

    trade_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Internal trade identifier",
    )
    order_id: str
    strategy_id: str | None = Field(default=None)
    instrument: Instrument
    side: OrderSide
    quantity: int = Field(..., gt=0)
    price: Decimal = Field(..., ge=0)
    timestamp: datetime
    broker_trade_id: str | None = Field(default=None)
    exchange_trade_id: str | None = Field(default=None)


class Position(BaseModel):
    """A live position held in a strategy or portfolio."""

    model_config = ConfigDict(from_attributes=True)

    instrument: Instrument
    strategy_id: str | None = Field(default=None)
    quantity: int = Field(
        default=0,
        description="Signed quantity: +ve = long, -ve = short",
    )
    average_price: Decimal = Field(default=Decimal("0"), ge=0)
    ltp: Decimal = Field(default=Decimal("0"), ge=0, description="Last traded price")
    pnl_unrealized: Decimal = Field(default=Decimal("0"))
    pnl_realized: Decimal = Field(default=Decimal("0"))
    value: Decimal = Field(default=Decimal("0"), description="Current notional value")
    margin_used: Decimal = Field(default=Decimal("0"), ge=0)
    product_type: ProductType = Field(default=ProductType.NRML)


class OptionContract(BaseModel):
    """A single option contract with greeks and market data."""

    model_config = ConfigDict(from_attributes=True)

    instrument: Instrument
    strike: Decimal = Field(..., ge=0)
    option_type: OptionType
    expiry: date
    ltp: Decimal = Field(default=Decimal("0"), ge=0)
    bid: Decimal = Field(default=Decimal("0"), ge=0)
    ask: Decimal = Field(default=Decimal("0"), ge=0)
    volume: int = Field(default=0, ge=0)
    oi: int = Field(default=0, ge=0)
    oi_change: int = Field(default=0)
    iv: float = Field(default=0.0, ge=0, description="Implied volatility")
    delta: float = Field(default=0.0)
    gamma: float = Field(default=0.0)
    theta: float = Field(default=0.0)
    vega: float = Field(default=0.0)
    rho: float = Field(default=0.0)


class OptionChain(BaseModel):
    """Full option chain for a given underlying and expiry."""

    model_config = ConfigDict(from_attributes=True)

    underlying_symbol: str
    underlying_price: Decimal = Field(..., ge=0)
    expiry: date
    timestamp: datetime
    contracts: list[OptionContract] = Field(default_factory=list)
    atm_strike: Decimal = Field(default=Decimal("0"), ge=0, description="At-the-money strike")
    pcr: float = Field(default=0.0, ge=0, description="Put-call ratio (OI-based)")


class Greeks(BaseModel):
    """Full set of option greeks including higher-order sensitivities."""

    model_config = ConfigDict(from_attributes=True)

    delta: float = Field(default=0.0)
    gamma: float = Field(default=0.0)
    theta: float = Field(default=0.0)
    vega: float = Field(default=0.0)
    rho: float = Field(default=0.0)
    charm: float = Field(default=0.0, description="Delta decay (dDelta/dTime)")
    vanna: float = Field(default=0.0, description="dDelta/dVol")
    volga: float = Field(default=0.0, description="dVega/dVol, a.k.a. vomma")
    speed: float = Field(default=0.0, description="dGamma/dSpot")
    zomma: float = Field(default=0.0, description="dGamma/dVol")
    color: float = Field(default=0.0, description="dGamma/dTime")


class PortfolioGreeks(BaseModel):
    """Aggregated greeks across the entire portfolio."""

    model_config = ConfigDict(from_attributes=True)

    net_delta: float = Field(default=0.0)
    net_gamma: float = Field(default=0.0)
    net_theta: float = Field(default=0.0)
    net_vega: float = Field(default=0.0)
    net_rho: float = Field(default=0.0)
    timestamp: datetime


class MarginInfo(BaseModel):
    """Snapshot of account margin utilisation."""

    model_config = ConfigDict(from_attributes=True)

    available_cash: Decimal = Field(default=Decimal("0"), ge=0)
    used_margin: Decimal = Field(default=Decimal("0"), ge=0)
    available_margin: Decimal = Field(default=Decimal("0"), ge=0)
    total_collateral: Decimal = Field(default=Decimal("0"), ge=0)
    exposure_margin: Decimal = Field(default=Decimal("0"), ge=0)
    span_margin: Decimal = Field(default=Decimal("0"), ge=0)
    utilization_pct: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Margin utilisation percentage"
    )


class TransactionCharges(BaseModel):
    """Breakdown of transaction costs for Indian markets (NSE/BSE)."""

    model_config = ConfigDict(from_attributes=True)

    brokerage: Decimal = Field(default=Decimal("0"), ge=0)
    stt: Decimal = Field(default=Decimal("0"), ge=0, description="Securities Transaction Tax")
    exchange_txn_fee: Decimal = Field(default=Decimal("0"), ge=0)
    gst: Decimal = Field(default=Decimal("0"), ge=0, description="GST on brokerage + txn fee")
    sebi_fee: Decimal = Field(default=Decimal("0"), ge=0)
    stamp_duty: Decimal = Field(default=Decimal("0"), ge=0)
    total: Decimal = Field(default=Decimal("0"), ge=0)

    @classmethod
    def calculate(
        cls,
        side: OrderSide,
        instrument_type: InstrumentType,
        quantity: int,
        price: Decimal,
        is_intraday: bool = False,
    ) -> "TransactionCharges":
        """Compute approximate NSE transaction charges.

        Rates are indicative and based on publicly available NSE charge
        schedules.  Actual charges may vary by broker.

        Args:
            side: BUY or SELL.
            instrument_type: Type of the instrument traded.
            quantity: Number of shares / lots.
            price: Trade price per unit.
            is_intraday: Whether the trade is intraday (MIS).

        Returns:
            A fully populated ``TransactionCharges`` instance.
        """
        turnover = Decimal(str(quantity)) * price

        # -- Brokerage (flat Rs 20 per executed order, capped) --
        brokerage = min(Decimal("20"), turnover * Decimal("0.0003"))

        # -- STT --
        if instrument_type == InstrumentType.STOCK:
            if is_intraday:
                # Intraday equity: 0.025% on sell side only
                stt = turnover * Decimal("0.00025") if side == OrderSide.SELL else Decimal("0")
            else:
                # Delivery equity: 0.1% on both legs
                stt = turnover * Decimal("0.001")
        elif instrument_type == InstrumentType.FUTURE:
            # Futures: 0.02% on sell side
            stt = turnover * Decimal("0.0002") if side == OrderSide.SELL else Decimal("0")
        elif instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
            # Options: 0.1% on sell side (on premium)
            stt = turnover * Decimal("0.001") if side == OrderSide.SELL else Decimal("0")
        else:
            stt = Decimal("0")

        # -- Exchange transaction charges --
        if instrument_type == InstrumentType.STOCK:
            exchange_txn_fee = turnover * Decimal("0.0000345")
        elif instrument_type == InstrumentType.FUTURE:
            exchange_txn_fee = turnover * Decimal("0.000002")
        elif instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
            exchange_txn_fee = turnover * Decimal("0.0005")
        else:
            exchange_txn_fee = Decimal("0")

        # -- GST: 18% on (brokerage + exchange txn fee) --
        gst = (brokerage + exchange_txn_fee) * Decimal("0.18")

        # -- SEBI fee: Rs 10 per crore --
        sebi_fee = turnover * Decimal("0.000001")

        # -- Stamp duty (buy side only) --
        if side == OrderSide.BUY:
            if instrument_type == InstrumentType.STOCK:
                stamp_duty = turnover * Decimal("0.00015") if not is_intraday else turnover * Decimal("0.00003")
            elif instrument_type == InstrumentType.FUTURE:
                stamp_duty = turnover * Decimal("0.00002")
            elif instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
                stamp_duty = turnover * Decimal("0.00003")
            else:
                stamp_duty = Decimal("0")
        else:
            stamp_duty = Decimal("0")

        total = brokerage + stt + exchange_txn_fee + gst + sebi_fee + stamp_duty

        return cls(
            brokerage=brokerage.quantize(Decimal("0.01")),
            stt=stt.quantize(Decimal("0.01")),
            exchange_txn_fee=exchange_txn_fee.quantize(Decimal("0.01")),
            gst=gst.quantize(Decimal("0.01")),
            sebi_fee=sebi_fee.quantize(Decimal("0.01")),
            stamp_duty=stamp_duty.quantize(Decimal("0.01")),
            total=total.quantize(Decimal("0.01")),
        )


class PnLSnapshot(BaseModel):
    """Point-in-time PnL snapshot for a strategy, including charges."""

    model_config = ConfigDict(from_attributes=True)

    strategy_id: str
    timestamp: datetime
    realized_pnl: Decimal = Field(default=Decimal("0"))
    unrealized_pnl: Decimal = Field(default=Decimal("0"))
    total_pnl: Decimal = Field(default=Decimal("0"))
    charges: TransactionCharges = Field(default_factory=TransactionCharges)
    net_pnl: Decimal = Field(default=Decimal("0"), description="total_pnl minus charges.total")


class RiskMetrics(BaseModel):
    """Portfolio-level risk analytics."""

    model_config = ConfigDict(from_attributes=True)

    portfolio_var: float = Field(default=0.0, description="Value at Risk")
    max_drawdown: float = Field(default=0.0, description="Maximum drawdown (fraction)")
    current_drawdown: float = Field(default=0.0, description="Current drawdown (fraction)")
    margin_utilization: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Margin utilisation ratio"
    )
    concentration_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Portfolio concentration (0 = diversified, 1 = concentrated)",
    )
    timestamp: datetime


class StrategyConfig(BaseModel):
    """Static configuration for deploying a trading strategy."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=128)
    class_path: str = Field(
        ...,
        description="Fully qualified Python class path, e.g. strategies.iron_condor.IronCondor",
    )
    enabled: bool = Field(default=True)
    mode: TradingMode = Field(default=TradingMode.PAPER)
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific parameters",
    )


class StrategyState(BaseModel):
    """Runtime state of a deployed strategy."""

    model_config = ConfigDict(from_attributes=True)

    strategy_id: str
    name: str
    status: StrategyStatus = Field(default=StrategyStatus.INITIALIZING)
    deployed_at: datetime | None = Field(default=None)
    last_heartbeat: datetime | None = Field(default=None)
    pnl_today: Decimal = Field(default=Decimal("0"))
    positions_count: int = Field(default=0, ge=0)
    orders_today: int = Field(default=0, ge=0)
    error_message: str | None = Field(default=None)


class Alert(BaseModel):
    """Platform alert / notification."""

    model_config = ConfigDict(from_attributes=True)

    alert_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    level: AlertLevel
    source: str = Field(..., description="Component that raised the alert")
    message: str
    timestamp: datetime
    acknowledged: bool = Field(default=False)
    data: dict[str, Any] | None = Field(
        default=None, description="Optional structured payload"
    )


class OrderResponse(BaseModel):
    """Response returned after submitting an order to the broker."""

    model_config = ConfigDict(from_attributes=True)

    success: bool
    order_id: str | None = Field(default=None, description="Internal order ID")
    broker_order_id: str | None = Field(default=None)
    message: str = Field(default="")
    status: OrderStatus | None = Field(default=None)


class PayoffPoint(BaseModel):
    """Single point on an option strategy payoff curve."""

    model_config = ConfigDict(from_attributes=True)

    underlying_price: Decimal
    payoff: Decimal
    delta: float | None = Field(default=None, description="Delta at this price level")


class PayoffDiagram(BaseModel):
    """Complete payoff diagram for a multi-leg option strategy."""

    model_config = ConfigDict(from_attributes=True)

    strategy_name: str
    legs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of leg descriptors (side, strike, option_type, qty, premium, etc.)",
    )
    points: list[PayoffPoint] = Field(default_factory=list)
    max_profit: Decimal | None = Field(
        default=None, description="None indicates unlimited profit"
    )
    max_loss: Decimal | None = Field(
        default=None, description="None indicates unlimited loss"
    )
    breakevens: list[float] = Field(
        default_factory=list,
        description="Underlying prices where payoff crosses zero",
    )


# ---------------------------------------------------------------------------
# Event Models
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """Base event for the platform event bus."""

    model_config = ConfigDict(from_attributes=True)

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_type: str
    timestamp: datetime
    source: str = Field(..., description="Component that emitted the event")
    payload: dict[str, Any] = Field(default_factory=dict)


class TickEvent(Event):
    """Event carrying a market-data tick."""

    event_type: str = Field(default="TICK")

    @field_validator("payload")
    @classmethod
    def _payload_must_have_tick(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "tick" not in v:
            raise ValueError("TickEvent payload must contain a 'tick' key")
        return v


class OrderEvent(Event):
    """Event carrying an order update."""

    event_type: str = Field(default="ORDER")

    @field_validator("payload")
    @classmethod
    def _payload_must_have_order(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "order" not in v:
            raise ValueError("OrderEvent payload must contain an 'order' key")
        return v


class TradeEvent(Event):
    """Event carrying a trade fill."""

    event_type: str = Field(default="TRADE")

    @field_validator("payload")
    @classmethod
    def _payload_must_have_trade(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "trade" not in v:
            raise ValueError("TradeEvent payload must contain a 'trade' key")
        return v


class RiskEvent(Event):
    """Event raised when a risk threshold is breached."""

    event_type: str = Field(default="RISK")

    @field_validator("payload")
    @classmethod
    def _payload_must_have_risk(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "risk" not in v:
            raise ValueError("RiskEvent payload must contain a 'risk' key")
        return v
