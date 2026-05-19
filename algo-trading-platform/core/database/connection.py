"""
Async database connection manager using SQLAlchemy 2.0 + asyncpg.

Reads configuration from environment variables:
  - DATABASE_URL          (full async DSN, takes precedence)
  - TIMESCALEDB_HOST      (default: localhost)
  - TIMESCALEDB_PORT      (default: 5432)
  - TIMESCALEDB_DATABASE  (default: trading)
  - TIMESCALEDB_USER      (default: postgres)
  - TIMESCALEDB_PASSWORD  (default: "")

Connection-pool knobs:
  - DB_POOL_SIZE          (default: 20)
  - DB_MAX_OVERFLOW       (default: 10)
  - DB_POOL_RECYCLE       (default: 3600 seconds)
  - DB_POOL_TIMEOUT       (default: 30 seconds)
  - DB_ECHO               (default: false)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


def _build_dsn() -> str:
    """Build an async PostgreSQL DSN from environment variables."""
    explicit_url = os.environ.get("DATABASE_URL")
    if explicit_url:
        # Ensure the scheme is async-compatible
        if explicit_url.startswith("postgresql://"):
            explicit_url = explicit_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        elif explicit_url.startswith("postgres://"):
            explicit_url = explicit_url.replace(
                "postgres://", "postgresql+asyncpg://", 1
            )
        return explicit_url

    host = os.environ.get("TIMESCALEDB_HOST", "localhost")
    port = os.environ.get("TIMESCALEDB_PORT", "5432")
    database = os.environ.get("TIMESCALEDB_DATABASE", "trading")
    user = os.environ.get("TIMESCALEDB_USER", "postgres")
    password = os.environ.get("TIMESCALEDB_PASSWORD", "")

    return (
        f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"
    )


def _pool_size() -> int:
    return int(os.environ.get("DB_POOL_SIZE", "20"))


def _max_overflow() -> int:
    return int(os.environ.get("DB_MAX_OVERFLOW", "10"))


def _pool_recycle() -> int:
    return int(os.environ.get("DB_POOL_RECYCLE", "3600"))


def _pool_timeout() -> int:
    return int(os.environ.get("DB_POOL_TIMEOUT", "30"))


def _echo() -> bool:
    return os.environ.get("DB_ECHO", "false").lower() in ("1", "true", "yes")


class DatabaseManager:
    """Manages an async SQLAlchemy engine and session factory.

    Usage::

        db = DatabaseManager()
        await db.init()

        async with db.session() as session:
            ...

        await db.shutdown()
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or _build_dsn()
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    # -- lifecycle ------------------------------------------------------------

    async def init(self) -> None:
        """Create the engine and session factory.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._engine is not None:
            return

        self._engine = create_async_engine(
            self._dsn,
            pool_size=_pool_size(),
            max_overflow=_max_overflow(),
            pool_recycle=_pool_recycle(),
            pool_timeout=_pool_timeout(),
            echo=_echo(),
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info(
            "Database engine created (pool_size=%d, max_overflow=%d)",
            _pool_size(),
            _max_overflow(),
        )

    async def shutdown(self) -> None:
        """Dispose of the engine and release all pooled connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Database engine disposed")

    # -- properties -----------------------------------------------------------

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError(
                "DatabaseManager is not initialised. Call await db.init() first."
            )
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError(
                "DatabaseManager is not initialised. Call await db.init() first."
            )
        return self._session_factory

    # -- session helpers ------------------------------------------------------

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide a transactional async session scope.

        Commits on clean exit, rolls back on exception.
        """
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_manager: DatabaseManager | None = None


def get_database_manager() -> DatabaseManager:
    """Return (and lazily create) the module-level ``DatabaseManager``."""
    global _default_manager
    if _default_manager is None:
        _default_manager = DatabaseManager()
    return _default_manager


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Shortcut: get a session from the default ``DatabaseManager``.

    Ensures the manager is initialised before yielding.
    """
    mgr = get_database_manager()
    await mgr.init()
    async with mgr.session() as session:
        yield session
