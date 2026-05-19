"""
Simple migration helpers for bootstrapping and tearing down the schema.

For production use consider pairing these with Alembic for versioned
migrations.  These helpers are mainly useful for development, testing,
and initial deployment.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from core.database.models import Base

logger = logging.getLogger(__name__)

# Tables that should be converted to TimescaleDB hypertables after creation.
# Each entry is (table_name, time_column).
HYPERTABLE_CANDIDATES: list[tuple[str, str]] = [
    ("trades", "timestamp"),
    ("alerts", "timestamp"),
    ("audit_log", "timestamp"),
    ("equity_curve", "timestamp"),
]


async def create_all_tables(engine: AsyncEngine) -> None:
    """Create every table defined on ``Base.metadata``.

    This is an **additive** operation -- existing tables are left untouched
    (SQLAlchemy's ``create_all`` uses ``CREATE TABLE IF NOT EXISTS``).

    After table creation the function logs the recommended TimescaleDB
    ``create_hypertable`` statements so an operator can run them manually
    (they require the TimescaleDB extension to be installed).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("All tables created (or already exist)")

    # Inform the operator about optional hypertable conversion
    for table, column in HYPERTABLE_CANDIDATES:
        logger.info(
            "TimescaleDB hint: SELECT create_hypertable('%s', '%s', "
            "if_not_exists => TRUE);",
            table,
            column,
        )


async def drop_all_tables(
    engine: AsyncEngine,
    *,
    confirm: bool = False,
) -> None:
    """Drop **all** tables defined on ``Base.metadata``.

    This is a destructive operation and requires ``confirm=True`` as a
    safety guard.

    Args:
        engine: The async engine to use.
        confirm: Must be ``True`` to actually perform the drop.

    Raises:
        RuntimeError: If ``confirm`` is not ``True``.
    """
    if not confirm:
        raise RuntimeError(
            "drop_all_tables() requires confirm=True to proceed. "
            "This will irreversibly delete all data."
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    logger.warning("All tables dropped")
