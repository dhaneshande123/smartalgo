"""
Repository classes providing async CRUD operations over the ORM models.

All public methods accept an ``AsyncSession`` and never manage transactions
themselves -- the caller (or ``DatabaseManager.session()``) owns the
commit / rollback boundary.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import (
    AlertRecord,
    BacktestResultRecord,
    OrderRecord,
    PositionRecord,
    TradeRecord,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _instrument_columns(inst: dict[str, Any]) -> dict[str, Any]:
    """Extract flattened instrument columns from an ``Instrument`` dict."""
    return {
        "symbol": inst["symbol"],
        "exchange": inst["exchange"],
        "segment": inst["segment"],
        "instrument_type": inst["instrument_type"],
        "expiry": inst.get("expiry"),
        "strike": inst.get("strike"),
        "option_type": inst.get("option_type"),
        "underlying": inst.get("underlying"),
    }


# ---------------------------------------------------------------------------
# OrderRepository
# ---------------------------------------------------------------------------


class OrderRepository:
    """Persistence operations for orders."""

    @staticmethod
    async def save_order(session: AsyncSession, order: dict[str, Any]) -> OrderRecord:
        """Insert or merge an order record.

        Args:
            session: Active async session.
            order: Dict representation of ``core.models.Order``
                   (typically ``order.model_dump()``).

        Returns:
            The persisted ``OrderRecord``.
        """
        inst = order.get("instrument", {})
        record = OrderRecord(
            order_id=order["order_id"],
            strategy_id=order.get("strategy_id"),
            **_instrument_columns(inst),
            order_type=order["order_type"],
            side=order["side"],
            product_type=order["product_type"],
            quantity=order["quantity"],
            price=order.get("price"),
            trigger_price=order.get("trigger_price"),
            status=order.get("status", "PENDING"),
            filled_quantity=order.get("filled_quantity", 0),
            average_price=order.get("average_price", Decimal("0")),
            broker_order_id=order.get("broker_order_id"),
            tag=order.get("tag"),
            rejection_reason=order.get("rejection_reason"),
            parent_order_id=order.get("parent_order_id"),
            placed_at=order.get("placed_at"),
            updated_at=order.get("updated_at"),
        )
        merged = await session.merge(record)
        return merged

    @staticmethod
    async def get_order(session: AsyncSession, order_id: str) -> OrderRecord | None:
        """Fetch a single order by its ID."""
        return await session.get(OrderRecord, order_id)

    @staticmethod
    async def get_orders(
        session: AsyncSession,
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
        status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> Sequence[OrderRecord]:
        """Query orders with optional filters.

        All filter parameters are AND-ed together.
        """
        stmt = select(OrderRecord)
        conditions = []
        if strategy_id is not None:
            conditions.append(OrderRecord.strategy_id == strategy_id)
        if symbol is not None:
            conditions.append(OrderRecord.symbol == symbol)
        if status is not None:
            conditions.append(OrderRecord.status == status)
        if start_time is not None:
            conditions.append(OrderRecord.created_at >= start_time)
        if end_time is not None:
            conditions.append(OrderRecord.created_at <= end_time)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(OrderRecord.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update_status(
        session: AsyncSession,
        order_id: str,
        status: str,
        *,
        filled_quantity: int | None = None,
        average_price: Decimal | None = None,
        broker_order_id: str | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        """Update the status (and optional fill details) of an order."""
        values: dict[str, Any] = {"status": status, "updated_at": datetime.utcnow()}
        if filled_quantity is not None:
            values["filled_quantity"] = filled_quantity
        if average_price is not None:
            values["average_price"] = average_price
        if broker_order_id is not None:
            values["broker_order_id"] = broker_order_id
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason

        stmt = (
            update(OrderRecord)
            .where(OrderRecord.order_id == order_id)
            .values(**values)
        )
        await session.execute(stmt)


# ---------------------------------------------------------------------------
# TradeRepository
# ---------------------------------------------------------------------------


class TradeRepository:
    """Persistence operations for trades."""

    @staticmethod
    async def save_trade(session: AsyncSession, trade: dict[str, Any]) -> TradeRecord:
        """Insert or merge a trade record."""
        inst = trade.get("instrument", {})
        record = TradeRecord(
            trade_id=trade["trade_id"],
            order_id=trade["order_id"],
            strategy_id=trade.get("strategy_id"),
            **_instrument_columns(inst),
            side=trade["side"],
            quantity=trade["quantity"],
            price=trade["price"],
            timestamp=trade["timestamp"],
            broker_trade_id=trade.get("broker_trade_id"),
            exchange_trade_id=trade.get("exchange_trade_id"),
        )
        merged = await session.merge(record)
        return merged

    @staticmethod
    async def get_trades(
        session: AsyncSession,
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
        side: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> Sequence[TradeRecord]:
        """Query trades with optional filters."""
        stmt = select(TradeRecord)
        conditions = []
        if strategy_id is not None:
            conditions.append(TradeRecord.strategy_id == strategy_id)
        if symbol is not None:
            conditions.append(TradeRecord.symbol == symbol)
        if side is not None:
            conditions.append(TradeRecord.side == side)
        if start_time is not None:
            conditions.append(TradeRecord.timestamp >= start_time)
        if end_time is not None:
            conditions.append(TradeRecord.timestamp <= end_time)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(TradeRecord.timestamp.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_trades_by_strategy(
        session: AsyncSession,
        strategy_id: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
    ) -> Sequence[TradeRecord]:
        """Convenience wrapper: all trades for a given strategy."""
        return await TradeRepository.get_trades(
            session,
            strategy_id=strategy_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )


# ---------------------------------------------------------------------------
# PositionRepository
# ---------------------------------------------------------------------------


class PositionRepository:
    """Persistence operations for position snapshots."""

    @staticmethod
    async def save_snapshot(
        session: AsyncSession, position: dict[str, Any]
    ) -> PositionRecord:
        """Insert a new position snapshot row."""
        inst = position.get("instrument", {})
        record = PositionRecord(
            strategy_id=position.get("strategy_id"),
            **_instrument_columns(inst),
            quantity=position.get("quantity", 0),
            average_price=position.get("average_price", Decimal("0")),
            ltp=position.get("ltp", Decimal("0")),
            pnl_unrealized=position.get("pnl_unrealized", Decimal("0")),
            pnl_realized=position.get("pnl_realized", Decimal("0")),
            value=position.get("value", Decimal("0")),
            margin_used=position.get("margin_used", Decimal("0")),
            product_type=position.get("product_type", "NRML"),
        )
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    async def get_latest_positions(
        session: AsyncSession,
        *,
        strategy_id: str | None = None,
        symbol: str | None = None,
        limit: int = 200,
    ) -> Sequence[PositionRecord]:
        """Return the most recent position snapshots.

        When ``strategy_id`` or ``symbol`` are given the results are filtered
        accordingly.  Results are ordered newest-first.
        """
        stmt = select(PositionRecord)
        conditions = []
        if strategy_id is not None:
            conditions.append(PositionRecord.strategy_id == strategy_id)
        if symbol is not None:
            conditions.append(PositionRecord.symbol == symbol)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(PositionRecord.snapshot_at.desc()).limit(limit)
        result = await session.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# BacktestRepository
# ---------------------------------------------------------------------------


class BacktestRepository:
    """Persistence operations for backtest results."""

    @staticmethod
    async def save_result(
        session: AsyncSession, result: dict[str, Any]
    ) -> BacktestResultRecord:
        """Insert a new backtest result."""
        record = BacktestResultRecord(
            strategy_id=result["strategy_id"],
            strategy_name=result["strategy_name"],
            start_date=result["start_date"],
            end_date=result["end_date"],
            total_pnl=result.get("total_pnl", Decimal("0")),
            total_trades=result.get("total_trades", 0),
            win_rate=result.get("win_rate", 0.0),
            max_drawdown=result.get("max_drawdown", 0.0),
            sharpe_ratio=result.get("sharpe_ratio", 0.0),
            sortino_ratio=result.get("sortino_ratio", 0.0),
            profit_factor=result.get("profit_factor", 0.0),
            params=result.get("params"),
            metrics=result.get("metrics"),
        )
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    async def get_result(
        session: AsyncSession, result_id: int
    ) -> BacktestResultRecord | None:
        """Fetch a single backtest result by its auto-incremented ID."""
        return await session.get(BacktestResultRecord, result_id)

    @staticmethod
    async def list_results(
        session: AsyncSession,
        *,
        strategy_id: str | None = None,
        start_date: Any | None = None,
        end_date: Any | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[BacktestResultRecord]:
        """List backtest results with optional filters."""
        stmt = select(BacktestResultRecord)
        conditions = []
        if strategy_id is not None:
            conditions.append(BacktestResultRecord.strategy_id == strategy_id)
        if start_date is not None:
            conditions.append(BacktestResultRecord.created_at >= start_date)
        if end_date is not None:
            conditions.append(BacktestResultRecord.created_at <= end_date)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(BacktestResultRecord.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# AlertRepository
# ---------------------------------------------------------------------------


class AlertRepository:
    """Persistence operations for platform alerts."""

    @staticmethod
    async def save_alert(session: AsyncSession, alert: dict[str, Any]) -> AlertRecord:
        """Insert or merge an alert record."""
        record = AlertRecord(
            alert_id=alert["alert_id"],
            level=alert["level"],
            source=alert["source"],
            message=alert["message"],
            timestamp=alert["timestamp"],
            acknowledged=alert.get("acknowledged", False),
            data=alert.get("data"),
        )
        merged = await session.merge(record)
        return merged

    @staticmethod
    async def get_alerts(
        session: AsyncSession,
        *,
        level: str | None = None,
        source: str | None = None,
        acknowledged: bool | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[AlertRecord]:
        """Query alerts with optional filters."""
        stmt = select(AlertRecord)
        conditions = []
        if level is not None:
            conditions.append(AlertRecord.level == level)
        if source is not None:
            conditions.append(AlertRecord.source == source)
        if acknowledged is not None:
            conditions.append(AlertRecord.acknowledged == acknowledged)
        if start_time is not None:
            conditions.append(AlertRecord.timestamp >= start_time)
        if end_time is not None:
            conditions.append(AlertRecord.timestamp <= end_time)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(AlertRecord.timestamp.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def acknowledge(session: AsyncSession, alert_id: str) -> None:
        """Mark an alert as acknowledged."""
        stmt = (
            update(AlertRecord)
            .where(AlertRecord.alert_id == alert_id)
            .values(acknowledged=True)
        )
        await session.execute(stmt)
