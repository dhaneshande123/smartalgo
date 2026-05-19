"""
Database / persistence layer for the algo trading platform.

Exports connection management, ORM models, repositories, and migration helpers.
"""

from core.database.connection import DatabaseManager, get_session
from core.database.models import (
    AlertRecord,
    AuditLogRecord,
    BacktestResultRecord,
    Base,
    EquityCurvePoint,
    OrderRecord,
    PositionRecord,
    StrategyStateRecord,
    TradeRecord,
)
from core.database.repositories import (
    AlertRepository,
    BacktestRepository,
    OrderRepository,
    PositionRepository,
    TradeRepository,
)
from core.database.migrations import create_all_tables, drop_all_tables

__all__ = [
    # Connection
    "DatabaseManager",
    "get_session",
    # ORM models
    "Base",
    "OrderRecord",
    "TradeRecord",
    "PositionRecord",
    "StrategyStateRecord",
    "BacktestResultRecord",
    "AlertRecord",
    "AuditLogRecord",
    "EquityCurvePoint",
    # Repositories
    "OrderRepository",
    "TradeRepository",
    "PositionRepository",
    "BacktestRepository",
    "AlertRepository",
    # Migrations
    "create_all_tables",
    "drop_all_tables",
]
