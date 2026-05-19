"""
Strategy Parameter Store — manages strategy parameters with versioning,
partial updates, and change-notification callbacks.
"""

from __future__ import annotations

import copy
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ParameterStore:
    """Manages strategy parameters with versioning and hot-reload.

    Every ``set`` or ``update`` call snapshots the full parameter dict into a
    time-stamped history list so operators can audit what changed and when.

    Listeners registered via ``on_change`` are invoked synchronously whenever
    the parameters for a given strategy are modified.  If a listener raises,
    the exception is logged but does **not** prevent other listeners from
    running.

    Usage::

        store = ParameterStore()
        store.set("strat_1", {"strike_offset": 200, "lots": 2})
        params = store.get("strat_1")
        store.update("strat_1", {"lots": 3})  # partial update
    """

    def __init__(self) -> None:
        self._params: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
        self._listeners: dict[str, list[Callable[[str, dict[str, Any]], None]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set(self, strategy_name: str, params: dict[str, Any]) -> None:
        """Replace the entire parameter set for *strategy_name*.

        A deep copy is stored so that external mutations do not silently alter
        the store.  A history snapshot is created and listeners are notified.
        """
        snapshot = copy.deepcopy(params)
        self._params[strategy_name] = snapshot
        self._record_history(strategy_name, snapshot)
        self._notify(strategy_name, snapshot)
        logger.info("Parameters SET for %s: %s", strategy_name, list(snapshot.keys()))

    def get(self, strategy_name: str) -> dict[str, Any]:
        """Return a deep copy of the current parameters for *strategy_name*.

        Returns an empty dict if no parameters have been stored yet.
        """
        stored = self._params.get(strategy_name)
        if stored is None:
            return {}
        return copy.deepcopy(stored)

    def update(self, strategy_name: str, updates: dict[str, Any]) -> None:
        """Merge *updates* into the existing parameter set for *strategy_name*.

        Keys present in *updates* overwrite existing values; keys not present
        are left unchanged.  If the strategy has no stored parameters, the
        update dict becomes the full parameter set.
        """
        current = self._params.get(strategy_name, {})
        current.update(copy.deepcopy(updates))
        self._params[strategy_name] = current
        snapshot = copy.deepcopy(current)
        self._record_history(strategy_name, snapshot)
        self._notify(strategy_name, snapshot)
        logger.info("Parameters UPDATED for %s: keys=%s", strategy_name, list(updates.keys()))

    def get_history(
        self, strategy_name: str, limit: int = 10
    ) -> list[tuple[datetime, dict[str, Any]]]:
        """Return the most recent *limit* parameter snapshots (newest last)."""
        history = self._history.get(strategy_name, [])
        return [
            (ts, copy.deepcopy(params)) for ts, params in history[-limit:]
        ]

    def on_change(
        self, strategy_name: str, callback: Callable[[str, dict[str, Any]], None]
    ) -> None:
        """Register a *callback* to be invoked when parameters change.

        The callback signature is ``callback(strategy_name, new_params)``.
        """
        self._listeners[strategy_name].append(callback)
        logger.debug("Registered parameter listener for %s", strategy_name)

    def remove_listeners(self, strategy_name: str) -> None:
        """Remove all change listeners for *strategy_name*."""
        self._listeners.pop(strategy_name, None)

    def remove(self, strategy_name: str) -> None:
        """Remove all parameters, history, and listeners for *strategy_name*."""
        self._params.pop(strategy_name, None)
        self._history.pop(strategy_name, None)
        self._listeners.pop(strategy_name, None)
        logger.info("Parameters REMOVED for %s", strategy_name)

    def has(self, strategy_name: str) -> bool:
        """Return ``True`` if parameters exist for *strategy_name*."""
        return strategy_name in self._params

    def list_strategies(self) -> list[str]:
        """Return strategy names that have stored parameters."""
        return list(self._params.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_history(self, strategy_name: str, snapshot: dict[str, Any]) -> None:
        self._history[strategy_name].append(
            (datetime.now(timezone.utc), copy.deepcopy(snapshot))
        )

    def _notify(self, strategy_name: str, params: dict[str, Any]) -> None:
        for cb in self._listeners.get(strategy_name, []):
            try:
                cb(strategy_name, copy.deepcopy(params))
            except Exception:
                logger.exception(
                    "Parameter change listener raised for %s", strategy_name
                )
