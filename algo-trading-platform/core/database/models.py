"""
SQLAlchemy 2.0 ORM models for the algo trading platform.

Each table mirrors a Pydantic domain model from ``core.models`` while adding
persistence concerns (primary keys, indexes, timestamps).

Tables annotated with ``-- TimescaleDB hypertable`` comments should be
converted with ``SELECT create_hypertable(...)`` after initial creation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


# ---------------------------------------------------------------------------
# OrderRecord
# ---------------------------------------------------------------------------


class OrderRecord(Base):
    """Persisted order — maps to ``core.models.Order``."""

    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Instrument fields (denormalised for query speed)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    segment: Mapped[str] = mapped_column(String(16), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(24), nullable=False)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    underlying: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Order fields
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    product_type: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    trigger_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tag: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Timestamps
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_orders_strategy_status", "strategy_id", "status"),
        Index("ix_orders_symbol_created", "symbol", "created_at"),
        Index("ix_orders_placed_at", "placed_at"),
    )


# ---------------------------------------------------------------------------
# TradeRecord  -- TimescaleDB hypertable on ``timestamp``
# ---------------------------------------------------------------------------


class TradeRecord(Base):
    """Persisted trade fill — maps to ``core.models.Trade``."""

    __tablename__ = "trades"

    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Instrument (denormalised)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    segment: Mapped[str] = mapped_column(String(16), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(24), nullable=False)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    underlying: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Trade fields
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    broker_trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exchange_trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_trades_strategy_ts", "strategy_id", "timestamp"),
        Index("ix_trades_symbol_ts", "symbol", "timestamp"),
        # SELECT create_hypertable('trades', 'timestamp');
    )


# ---------------------------------------------------------------------------
# PositionRecord (snapshots)
# ---------------------------------------------------------------------------


class PositionRecord(Base):
    """Point-in-time position snapshot — maps to ``core.models.Position``."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Instrument (denormalised)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    segment: Mapped[str] = mapped_column(String(16), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(24), nullable=False)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    underlying: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Position fields
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    ltp: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    pnl_unrealized: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    pnl_realized: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    margin_used: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    product_type: Mapped[str] = mapped_column(String(8), nullable=False, default="NRML")

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    __table_args__ = (
        Index("ix_positions_strategy_snapshot", "strategy_id", "snapshot_at"),
        Index("ix_positions_symbol_snapshot", "symbol", "snapshot_at"),
    )


# ---------------------------------------------------------------------------
# StrategyStateRecord
# ---------------------------------------------------------------------------


class StrategyStateRecord(Base):
    """Persisted strategy runtime state — maps to ``core.models.StrategyState``."""

    __tablename__ = "strategy_states"

    strategy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pnl_today: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    positions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


# ---------------------------------------------------------------------------
# BacktestResultRecord
# ---------------------------------------------------------------------------


class BacktestResultRecord(Base):
    """Stored backtest run result."""

    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Summary metrics
    total_pnl: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sortino_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Flexible JSON blob for additional metrics / parameters
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_backtest_strategy_date", "strategy_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# AlertRecord  -- TimescaleDB hypertable on ``timestamp``
# ---------------------------------------------------------------------------


class AlertRecord(Base):
    """Persisted platform alert — maps to ``core.models.Alert``."""

    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_alerts_level_ts", "level", "timestamp"),
        Index("ix_alerts_source_ts", "source", "timestamp"),
        # SELECT create_hypertable('alerts', 'timestamp');
    )


# ---------------------------------------------------------------------------
# AuditLogRecord  -- TimescaleDB hypertable on ``timestamp``
# ---------------------------------------------------------------------------


class AuditLogRecord(Base):
    """Immutable audit trail entry."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_audit_actor_ts", "actor", "timestamp"),
        Index("ix_audit_action_ts", "action", "timestamp"),
        # SELECT create_hypertable('audit_log', 'timestamp');
    )


# ---------------------------------------------------------------------------
# EquityCurvePoint  -- TimescaleDB hypertable on ``timestamp``
# ---------------------------------------------------------------------------


class EquityCurvePoint(Base):
    """Single data-point on a strategy's equity curve."""

    __tablename__ = "equity_curve"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    equity: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    __table_args__ = (
        Index("ix_equity_strategy_ts", "strategy_id", "timestamp"),
        # SELECT create_hypertable('equity_curve', 'timestamp');
    )
