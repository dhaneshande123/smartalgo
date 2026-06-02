"""
State persistence layer using SQLite.

Saves and loads platform state so it survives backend restarts.
Uses a single SQLite file at data/platform_state.db (auto-created).

Design principles:
- Zero external dependencies (sqlite3 is stdlib)
- Thread-safe (sqlite3 with check_same_thread=False + threading.Lock)
- Auto-save on every mutation (no explicit flush needed)
- JSON serialization for complex nested dicts
- Atomic writes via SQLite transactions
- Graceful degradation: if DB fails, platform still runs (logs warning)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from core.constants import IST

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------


class StateStore:
    """SQLite-backed persistence for deployed strategies, P&L snapshots,
    trade logs, paper sessions, and arbitrary settings."""

    def __init__(self, db_path: str = "data/platform_state.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

        try:
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

            self._conn = sqlite3.connect(
                db_path,
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._create_tables()
            logger.info(f"StateStore initialised — {db_path}")
        except Exception as exc:
            logger.warning(f"StateStore DB init failed ({exc}). Platform will run without persistence.")
            self._conn = None

    # ------------------------------------------------------------------
    # Table creation
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        assert self._conn is not None
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS deployed_strategies (
                    id          TEXT PRIMARY KEY,
                    data        TEXT,
                    updated_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS pnl_snapshots (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id     TEXT,
                    timestamp       TEXT,
                    pnl             REAL,
                    unrealized_pnl  REAL,
                    realized_pnl    REAL,
                    positions_json  TEXT
                );

                CREATE TABLE IF NOT EXISTS trade_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT,
                    action      TEXT,
                    side        TEXT,
                    symbol      TEXT,
                    qty         INTEGER,
                    price       REAL,
                    timestamp   TEXT,
                    reason      TEXT
                );

                CREATE TABLE IF NOT EXISTS paper_sessions (
                    id          TEXT PRIMARY KEY,
                    data        TEXT,
                    updated_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key         TEXT PRIMARY KEY,
                    value       TEXT,
                    updated_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT,
                    equity      REAL,
                    daily_pnl   REAL,
                    cash        REAL,
                    margin_used REAL,
                    open_strategies INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_equity_ts
                    ON equity_snapshots(timestamp);

                CREATE TABLE IF NOT EXISTS risk_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT,
                    event_type  TEXT,
                    severity    TEXT,
                    limit_name  TEXT,
                    current_value REAL,
                    limit_value REAL,
                    utilization_pct REAL,
                    message     TEXT,
                    metadata_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_risk_events_ts
                    ON risk_events(timestamp);

                CREATE TABLE IF NOT EXISTS candles_cache (
                    symbol      TEXT NOT NULL,
                    resolution  TEXT NOT NULL,
                    ts          INTEGER NOT NULL,
                    open        REAL,
                    high        REAL,
                    low         REAL,
                    close       REAL,
                    volume      INTEGER,
                    PRIMARY KEY (symbol, resolution, ts)
                );

                CREATE INDEX IF NOT EXISTS idx_candles_sym_res
                    ON candles_cache(symbol, resolution, ts);
                """
            )

    # ------------------------------------------------------------------
    # Strategy methods
    # ------------------------------------------------------------------

    def save_strategy(self, strategy_id: str, strategy_data: dict) -> None:
        """Upsert a deployed strategy (JSON-serialized)."""
        try:
            with self._lock:
                if self._conn is None:
                    return
                now = datetime.now(IST).isoformat()
                data_json = json.dumps(strategy_data, default=str)
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO deployed_strategies (id, data, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
                        """,
                        (strategy_id, data_json, now),
                    )
        except Exception as exc:
            logger.warning(f"StateStore.save_strategy failed: {exc}")

    def load_all_strategies(self) -> dict[str, dict]:
        """Return {strategy_id: strategy_data} for every saved strategy."""
        try:
            with self._lock:
                if self._conn is None:
                    return {}
                cursor = self._conn.execute("SELECT id, data FROM deployed_strategies")
                rows = cursor.fetchall()
            result: dict[str, dict] = {}
            for row in rows:
                try:
                    result[row["id"]] = json.loads(row["data"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"StateStore: corrupt data for strategy {row['id']}, skipping")
            return result
        except Exception as exc:
            logger.warning(f"StateStore.load_all_strategies failed: {exc}")
            return {}

    def delete_strategy(self, strategy_id: str) -> None:
        """Remove a single strategy from the DB."""
        try:
            with self._lock:
                if self._conn is None:
                    return
                with self._conn:
                    self._conn.execute(
                        "DELETE FROM deployed_strategies WHERE id = ?",
                        (strategy_id,),
                    )
        except Exception as exc:
            logger.warning(f"StateStore.delete_strategy failed: {exc}")

    def clear_strategies(self, status_filter: list[str] | None = None) -> None:
        """Remove strategies by status list, or all if *status_filter* is ``None``."""
        try:
            with self._lock:
                if self._conn is None:
                    return
                with self._conn:
                    if status_filter is None:
                        self._conn.execute("DELETE FROM deployed_strategies")
                    else:
                        # Need to inspect the JSON data for status
                        cursor = self._conn.execute("SELECT id, data FROM deployed_strategies")
                        rows = cursor.fetchall()
                        ids_to_delete: list[str] = []
                        for row in rows:
                            try:
                                strat = json.loads(row["data"])
                                if strat.get("status") in status_filter:
                                    ids_to_delete.append(row["id"])
                            except (json.JSONDecodeError, TypeError):
                                continue
                        if ids_to_delete:
                            placeholders = ",".join("?" for _ in ids_to_delete)
                            self._conn.execute(
                                f"DELETE FROM deployed_strategies WHERE id IN ({placeholders})",
                                ids_to_delete,
                            )
        except Exception as exc:
            logger.warning(f"StateStore.clear_strategies failed: {exc}")

    # ------------------------------------------------------------------
    # P&L snapshot methods
    # ------------------------------------------------------------------

    def save_pnl_snapshot(
        self,
        strategy_id: str,
        pnl: float,
        unrealized_pnl: float,
        realized_pnl: float,
        positions: list[dict],
    ) -> None:
        """Insert a new P&L snapshot row."""
        try:
            with self._lock:
                if self._conn is None:
                    return
                now = datetime.now(IST).isoformat()
                positions_json = json.dumps(positions, default=str)
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO pnl_snapshots
                            (strategy_id, timestamp, pnl, unrealized_pnl, realized_pnl, positions_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (strategy_id, now, pnl, unrealized_pnl, realized_pnl, positions_json),
                    )
        except Exception as exc:
            logger.warning(f"StateStore.save_pnl_snapshot failed: {exc}")

    def get_pnl_history(
        self, strategy_id: str | None = None, limit: int = 500
    ) -> list[dict]:
        """Return P&L snapshots, newest first. Optionally filter by strategy_id."""
        try:
            with self._lock:
                if self._conn is None:
                    return []
                if strategy_id:
                    cursor = self._conn.execute(
                        "SELECT * FROM pnl_snapshots WHERE strategy_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (strategy_id, limit),
                    )
                else:
                    cursor = self._conn.execute(
                        "SELECT * FROM pnl_snapshots ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    )
                rows = cursor.fetchall()
            result: list[dict] = []
            for row in rows:
                entry = dict(row)
                # Deserialize positions JSON
                try:
                    entry["positions"] = json.loads(entry.pop("positions_json", "[]"))
                except (json.JSONDecodeError, TypeError):
                    entry["positions"] = []
                result.append(entry)
            return result
        except Exception as exc:
            logger.warning(f"StateStore.get_pnl_history failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # Trade log methods
    # ------------------------------------------------------------------

    def log_trade(
        self,
        strategy_id: str,
        action: str,
        side: str,
        symbol: str,
        qty: int,
        price: float,
        reason: str = "",
    ) -> None:
        """Insert a trade log entry."""
        try:
            with self._lock:
                if self._conn is None:
                    return
                now = datetime.now(IST).isoformat()
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO trade_log
                            (strategy_id, action, side, symbol, qty, price, timestamp, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (strategy_id, action, side, symbol, qty, price, now, reason),
                    )
        except Exception as exc:
            logger.warning(f"StateStore.log_trade failed: {exc}")

    def get_trade_log(
        self, strategy_id: str | None = None, limit: int = 200
    ) -> list[dict]:
        """Return trade log entries, newest first."""
        try:
            with self._lock:
                if self._conn is None:
                    return []
                if strategy_id:
                    cursor = self._conn.execute(
                        "SELECT * FROM trade_log WHERE strategy_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (strategy_id, limit),
                    )
                else:
                    cursor = self._conn.execute(
                        "SELECT * FROM trade_log ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    )
                rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.warning(f"StateStore.get_trade_log failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # Settings methods
    # ------------------------------------------------------------------

    def save_setting(self, key: str, value: Any) -> None:
        """Upsert a JSON-serialized setting."""
        try:
            with self._lock:
                if self._conn is None:
                    return
                now = datetime.now(IST).isoformat()
                value_json = json.dumps(value, default=str)
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO settings (key, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                        """,
                        (key, value_json, now),
                    )
        except Exception as exc:
            logger.warning(f"StateStore.save_setting failed: {exc}")

    def load_setting(self, key: str, default: Any = None) -> Any:
        """Load and JSON-deserialize a setting, returning *default* if missing."""
        try:
            with self._lock:
                if self._conn is None:
                    return default
                cursor = self._conn.execute(
                    "SELECT value FROM settings WHERE key = ?", (key,)
                )
                row = cursor.fetchone()
            if row is None:
                return default
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default
        except Exception as exc:
            logger.warning(f"StateStore.load_setting failed: {exc}")
            return default

    # ------------------------------------------------------------------
    # Risk: limits & kill switch (stored in settings table)
    # ------------------------------------------------------------------

    RISK_LIMITS_KEY = "risk_limits"
    KILL_SWITCH_KEY = "kill_switch_active"
    KILL_SWITCH_META_KEY = "kill_switch_meta"

    def save_risk_limits(self, limits: dict) -> None:
        """Persist user-configured risk limits."""
        self.save_setting(self.RISK_LIMITS_KEY, limits)

    def load_risk_limits(self, default: dict | None = None) -> dict:
        """Load risk limits. Returns *default* (or {}) if none stored."""
        return self.load_setting(self.RISK_LIMITS_KEY, default or {}) or {}

    def set_kill_switch(self, active: bool, reason: str = "", triggered_by: str = "user") -> None:
        """Persist kill switch state + audit metadata."""
        self.save_setting(self.KILL_SWITCH_KEY, bool(active))
        self.save_setting(self.KILL_SWITCH_META_KEY, {
            "active": bool(active),
            "reason": reason,
            "triggered_by": triggered_by,
            "timestamp": datetime.now(IST).isoformat(),
        })

    def is_kill_switch_active(self) -> bool:
        return bool(self.load_setting(self.KILL_SWITCH_KEY, False))

    def get_kill_switch_meta(self) -> dict:
        return self.load_setting(self.KILL_SWITCH_META_KEY, {}) or {}

    # ------------------------------------------------------------------
    # Risk: equity snapshots (for drawdown tracking)
    # ------------------------------------------------------------------

    def save_equity_snapshot(
        self,
        equity: float,
        daily_pnl: float = 0.0,
        cash: float = 0.0,
        margin_used: float = 0.0,
        open_strategies: int = 0,
    ) -> None:
        """Append a snapshot of current portfolio equity."""
        try:
            with self._lock:
                if self._conn is None:
                    return
                now = datetime.now(IST).isoformat()
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO equity_snapshots
                            (timestamp, equity, daily_pnl, cash, margin_used, open_strategies)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (now, equity, daily_pnl, cash, margin_used, open_strategies),
                    )
        except Exception as exc:
            logger.warning(f"StateStore.save_equity_snapshot failed: {exc}")

    def get_equity_curve(self, limit: int = 1000, since_iso: str | None = None) -> list[dict]:
        """Return equity snapshots oldest-first (suitable for drawdown calc)."""
        try:
            with self._lock:
                if self._conn is None:
                    return []
                if since_iso:
                    cursor = self._conn.execute(
                        """SELECT * FROM equity_snapshots
                           WHERE timestamp >= ?
                           ORDER BY timestamp ASC LIMIT ?""",
                        (since_iso, limit),
                    )
                else:
                    # Get newest N then reverse so oldest is first
                    cursor = self._conn.execute(
                        """SELECT * FROM (
                             SELECT * FROM equity_snapshots
                             ORDER BY timestamp DESC LIMIT ?
                           ) ORDER BY timestamp ASC""",
                        (limit,),
                    )
                rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.warning(f"StateStore.get_equity_curve failed: {exc}")
            return []

    def get_peak_equity(self) -> float:
        """Return the all-time peak equity (for drawdown reference)."""
        try:
            with self._lock:
                if self._conn is None:
                    return 0.0
                cursor = self._conn.execute(
                    "SELECT MAX(equity) AS peak FROM equity_snapshots"
                )
                row = cursor.fetchone()
            return float(row["peak"] or 0.0) if row else 0.0
        except Exception as exc:
            logger.warning(f"StateStore.get_peak_equity failed: {exc}")
            return 0.0

    # ------------------------------------------------------------------
    # Risk: audit log
    # ------------------------------------------------------------------

    def log_risk_event(
        self,
        event_type: str,
        severity: str,
        limit_name: str = "",
        current_value: float = 0.0,
        limit_value: float = 0.0,
        utilization_pct: float = 0.0,
        message: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Append a risk event to the audit log.

        event_type examples: BREACH, WARN, KILL_SWITCH_ON, KILL_SWITCH_OFF,
                             LIMITS_UPDATED, AUTO_KILL, MANUAL_KILL
        severity:           INFO, WARN, BREACH, CRITICAL
        """
        try:
            with self._lock:
                if self._conn is None:
                    return
                now = datetime.now(IST).isoformat()
                meta_json = json.dumps(metadata or {}, default=str)
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO risk_events
                            (timestamp, event_type, severity, limit_name,
                             current_value, limit_value, utilization_pct,
                             message, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (now, event_type, severity, limit_name,
                         current_value, limit_value, utilization_pct,
                         message, meta_json),
                    )
        except Exception as exc:
            logger.warning(f"StateStore.log_risk_event failed: {exc}")

    def get_risk_events(
        self,
        limit: int = 200,
        event_type: str | None = None,
        since_iso: str | None = None,
    ) -> list[dict]:
        """Return risk audit events, newest first."""
        try:
            with self._lock:
                if self._conn is None:
                    return []
                conditions = []
                params: list = []
                if event_type:
                    conditions.append("event_type = ?")
                    params.append(event_type)
                if since_iso:
                    conditions.append("timestamp >= ?")
                    params.append(since_iso)
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                params.append(limit)
                cursor = self._conn.execute(
                    f"SELECT * FROM risk_events {where} ORDER BY timestamp DESC LIMIT ?",
                    params,
                )
                rows = cursor.fetchall()
            result: list[dict] = []
            for row in rows:
                entry = dict(row)
                try:
                    entry["metadata"] = json.loads(entry.pop("metadata_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    entry["metadata"] = {}
                result.append(entry)
            return result
        except Exception as exc:
            logger.warning(f"StateStore.get_risk_events failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # Candle cache (for backtesting + chart data)
    # ------------------------------------------------------------------

    def save_candles(
        self,
        symbol: str,
        resolution: str,
        candles: list[dict],
    ) -> int:
        """Bulk upsert candles into the cache.

        Each candle dict must have: ts (unix epoch int), open, high, low, close, volume.
        Returns number of rows inserted/updated.
        """
        if not candles:
            return 0
        try:
            with self._lock:
                if self._conn is None:
                    return 0
                with self._conn:
                    self._conn.executemany(
                        """
                        INSERT INTO candles_cache (symbol, resolution, ts, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(symbol, resolution, ts) DO UPDATE SET
                            open = excluded.open, high = excluded.high,
                            low = excluded.low, close = excluded.close,
                            volume = excluded.volume
                        """,
                        [
                            (
                                symbol.upper(),
                                resolution,
                                int(c["ts"]),
                                float(c.get("open", 0)),
                                float(c.get("high", 0)),
                                float(c.get("low", 0)),
                                float(c.get("close", 0)),
                                int(c.get("volume", 0)),
                            )
                            for c in candles
                        ],
                    )
            return len(candles)
        except Exception as exc:
            logger.warning(f"StateStore.save_candles failed: {exc}")
            return 0

    def get_candles(
        self,
        symbol: str,
        resolution: str,
        from_ts: int | None = None,
        to_ts: int | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Fetch cached candles, oldest first.

        If from_ts/to_ts are provided, filter by timestamp range.
        """
        try:
            with self._lock:
                if self._conn is None:
                    return []
                conditions = ["symbol = ?", "resolution = ?"]
                params: list = [symbol.upper(), resolution]
                if from_ts is not None:
                    conditions.append("ts >= ?")
                    params.append(int(from_ts))
                if to_ts is not None:
                    conditions.append("ts <= ?")
                    params.append(int(to_ts))
                params.append(limit)
                where = " AND ".join(conditions)
                cursor = self._conn.execute(
                    f"SELECT ts, open, high, low, close, volume FROM candles_cache "
                    f"WHERE {where} ORDER BY ts ASC LIMIT ?",
                    params,
                )
                rows = cursor.fetchall()
            return [
                {
                    "ts": row["ts"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                }
                for row in rows
            ]
        except Exception as exc:
            logger.warning(f"StateStore.get_candles failed: {exc}")
            return []

    def get_candle_date_range(self, symbol: str, resolution: str) -> dict:
        """Return the min/max timestamps cached for a symbol + resolution."""
        try:
            with self._lock:
                if self._conn is None:
                    return {"min_ts": None, "max_ts": None, "count": 0}
                cursor = self._conn.execute(
                    """SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts, COUNT(*) AS cnt
                       FROM candles_cache WHERE symbol = ? AND resolution = ?""",
                    (symbol.upper(), resolution),
                )
                row = cursor.fetchone()
            if row and row["cnt"] > 0:
                return {
                    "min_ts": row["min_ts"],
                    "max_ts": row["max_ts"],
                    "count": row["cnt"],
                }
            return {"min_ts": None, "max_ts": None, "count": 0}
        except Exception as exc:
            logger.warning(f"StateStore.get_candle_date_range failed: {exc}")
            return {"min_ts": None, "max_ts": None, "count": 0}

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            with self._lock:
                if self._conn is not None:
                    self._conn.close()
                    self._conn = None
                    logger.info("StateStore closed.")
        except Exception as exc:
            logger.warning(f"StateStore.close failed: {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: StateStore | None = None


def get_store() -> StateStore:
    """Return (or create) the module-level StateStore singleton."""
    global _store
    if _store is None:
        _store = StateStore()
    return _store
