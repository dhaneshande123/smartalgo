"""
Order Audit Trail -- compliance-grade logging for order lifecycle events.

Maintains an in-memory, ring-buffer-style collection of :class:`AuditEntry`
records.  All writes are serialised with an ``asyncio.Lock`` so the trail is
safe to use from concurrent coroutines.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit entry
# ---------------------------------------------------------------------------


@dataclass(frozen=False, slots=True)
class AuditEntry:
    """A single audit record for an order lifecycle event.

    Attributes:
        timestamp:       When the event occurred (UTC).
        order_id:        Internal order identifier.
        event_type:      One of ``SUBMIT``, ``MODIFY``, ``CANCEL``,
                         ``STATE_CHANGE``, ``FILL``, ``ERROR``.
        old_state:       The order state *before* the event.
        new_state:       The order state *after* the event.
        details:         Arbitrary key/value payload describing the event.
        strategy_id:     Owning strategy, if applicable.
        broker_order_id: Broker-assigned order identifier, if known.
    """

    timestamp: datetime
    order_id: str
    event_type: str
    old_state: str
    new_state: str
    details: dict[str, Any] = field(default_factory=dict)
    strategy_id: str = ""
    broker_order_id: str = ""


# ---------------------------------------------------------------------------
# Supported event types (non-enforced constants for documentation)
# ---------------------------------------------------------------------------

AUDIT_SUBMIT = "SUBMIT"
AUDIT_MODIFY = "MODIFY"
AUDIT_CANCEL = "CANCEL"
AUDIT_STATE_CHANGE = "STATE_CHANGE"
AUDIT_FILL = "FILL"
AUDIT_ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------


class AuditTrail:
    """In-memory audit trail for order lifecycle events.

    The trail is implemented as a ring buffer: once *max_entries* is reached
    the oldest entries are discarded.  All mutating operations are protected
    by an ``asyncio.Lock`` so the trail can be safely accessed from
    concurrent tasks.

    Usage::

        audit = AuditTrail(max_entries=50_000)
        await audit.record(AuditEntry(
            timestamp=datetime.now(timezone.utc),
            order_id="abc123",
            event_type="SUBMIT",
            old_state="",
            new_state="PENDING",
            details={"symbol": "NIFTY", "qty": 50},
        ))
        history = await audit.get_order_history("abc123")
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._entries: list[AuditEntry] = []
        self._max_entries = max_entries
        self._lock = asyncio.Lock()

    # -- recording -----------------------------------------------------------

    async def record(self, entry: AuditEntry) -> None:
        """Append an audit entry, evicting the oldest if at capacity.

        Args:
            entry: The :class:`AuditEntry` to store.
        """
        async with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                overflow = len(self._entries) - self._max_entries
                self._entries = self._entries[overflow:]

        logger.debug(
            "Audit [%s] order=%s %s -> %s",
            entry.event_type,
            entry.order_id,
            entry.old_state or "(none)",
            entry.new_state,
        )

    # -- querying ------------------------------------------------------------

    async def get_entries(
        self,
        order_id: str | None = None,
        strategy_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Return audit entries matching the given filters.

        All filter parameters are optional and are combined with logical AND.
        Results are returned in chronological order, most recent last, capped
        at *limit*.

        Args:
            order_id:    Filter by internal order ID.
            strategy_id: Filter by strategy ID.
            event_type:  Filter by event type string.
            since:       Only entries on or after this timestamp.
            limit:       Maximum number of entries to return (default 100).

        Returns:
            A list of matching :class:`AuditEntry` objects.
        """
        async with self._lock:
            results: list[AuditEntry] = []
            # Iterate backwards for efficiency when a limit is set.
            for entry in reversed(self._entries):
                if order_id is not None and entry.order_id != order_id:
                    continue
                if strategy_id is not None and entry.strategy_id != strategy_id:
                    continue
                if event_type is not None and entry.event_type != event_type:
                    continue
                if since is not None and entry.timestamp < since:
                    # Entries are appended chronologically so once we pass
                    # the threshold we can stop early.
                    break
                results.append(entry)
                if len(results) >= limit:
                    break
            # Reverse so results are chronological (oldest first).
            results.reverse()
            return results

    async def get_order_history(self, order_id: str) -> list[AuditEntry]:
        """Return the full audit trail for a single order (no limit).

        Args:
            order_id: The internal order ID to look up.

        Returns:
            Chronologically ordered list of all events for this order.
        """
        async with self._lock:
            return [e for e in self._entries if e.order_id == order_id]

    # -- properties / maintenance --------------------------------------------

    @property
    def entry_count(self) -> int:
        """Total number of entries currently held."""
        return len(self._entries)

    async def clear(self) -> None:
        """Remove all audit entries."""
        async with self._lock:
            count = len(self._entries)
            self._entries.clear()
        logger.info("Audit trail cleared (%d entries removed)", count)
