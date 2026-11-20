"""
Central Fyers REST request limiter.

Fyers Standard plan: 200 requests/minute and 5 requests/second per user,
shared by every REST call this process makes. Going over returns 429 and, if
repeated, gets the session throttled — which is what used to knock Fly-High
off its data. Every SDK call goes through one sliding-window limiter here,
with three priority tiers:

- critical — order placement/modification and Fly-High candle fetches. Can use
  the full budget up to ``HARD_PER_MIN`` (kept just under Fyers' 200) and waits
  up to ``CRITICAL_MAX_WAIT`` for a slot.
- high — market data (history, quotes, option chain). Capped at
  ``SOFT_PER_MIN`` so the headroom above it is always left for critical calls.
- low — account reads (positions, funds, holdings, order/trade book, depth).
  Refused locally once ``LOW_PER_MIN`` is in use; they are display-only and
  can always be retried on the next poll.

All tiers share ``PER_SEC`` (under Fyers' 5/sec). A refused call returns a
Fyers-shaped error dict (``code`` -9429) instead of raising, so existing
``resp.get("s") == "ok"`` checks treat it as a normal failure.

Once Fyers rejects the token (expired at 06:00 IST, or revoked), non-critical
calls are refused locally (``code`` -9401) apart from one probe every
``AUTH_PROBE_INTERVAL`` — instead of sending thousands of doomed requests.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

FYERS_PER_MIN = 200          # Fyers plan limit (for display)
HARD_PER_MIN = 190           # critical calls may go up to here
SOFT_PER_MIN = 150           # high-priority data calls stop here
LOW_PER_MIN = 100            # low-priority account reads stop here
PER_SEC = 4                  # Fyers allows 5/sec — keep one spare

CRITICAL_MAX_WAIT = 10.0
HIGH_MAX_WAIT = 6.0
LOW_MAX_WAIT = 1.0

CRITICAL, HIGH, LOW = "critical", "high", "low"

_CRITICAL_METHODS = {
    "place_order", "modify_order", "cancel_order", "exit_positions",
    "place_basket_orders", "modify_basket_orders", "cancel_basket_orders",
    "convert_position", "place_multileg_order",
}
_HIGH_METHODS = {"history", "quotes", "optionchain"}

# Local codes — distinct from Fyers' own -429/429/-360 rate-limit replies
THROTTLED_CODE = -9429
AUTH_BLOCKED_CODE = -9401
AUTH_PROBE_INTERVAL = 30.0

_RATE_LIMIT_CODES = {429, -429, -360, 1015}
_AUTH_ERROR_CODES = {401, 403, -8, -15, -16, -17}


def method_priority(name: str) -> str:
    if name in _CRITICAL_METHODS:
        return CRITICAL
    if name in _HIGH_METHODS:
        return HIGH
    return LOW


class FyersRateLimiter:
    """Thread-safe sliding-window limiter shared by all Fyers REST calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: deque[tuple[float, str]] = deque()  # (monotonic ts, priority)
        self._throttled: deque[float] = deque()
        self._fyers_429: deque[float] = deque()
        self.throttled_total = 0
        self.fyers_429_total = 0
        self.token_rejected_at: float | None = None  # wall-clock time of last auth rejection
        self.last_ok_at: float | None = None
        self.auth_blocked_total = 0
        self._last_auth_probe = 0.0
        self.token_fingerprint: str | None = None  # sha256 prefix of the token in use

    @staticmethod
    def _cap(priority: str) -> int:
        return {CRITICAL: HARD_PER_MIN, HIGH: SOFT_PER_MIN}.get(priority, LOW_PER_MIN)

    @staticmethod
    def _max_wait(priority: str) -> float:
        return {CRITICAL: CRITICAL_MAX_WAIT, HIGH: HIGH_MAX_WAIT}.get(priority, LOW_MAX_WAIT)

    def reset_auth(self) -> None:
        """Called when a new token is installed — lift the auth block."""
        with self._lock:
            self.token_rejected_at = None
            self._last_auth_probe = 0.0

    def auth_allows(self, priority: str) -> bool:
        """False while the token is known-bad, except critical calls and one
        probe per ``AUTH_PROBE_INTERVAL`` (so recovery is still detected)."""
        with self._lock:
            if self.token_rejected_at is None or priority == CRITICAL:
                return True
            now = time.monotonic()
            if now - self._last_auth_probe >= AUTH_PROBE_INTERVAL:
                self._last_auth_probe = now
                return True
            self.auth_blocked_total += 1
            return False

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0][0] >= 60.0:
            self._calls.popleft()
        for dq in (self._throttled, self._fyers_429):
            while dq and now - dq[0] >= 60.0:
                dq.popleft()

    def acquire(self, priority: str) -> bool:
        """Block until a slot is free for ``priority`` or its max wait elapses."""
        max_wait = self._max_wait(priority)
        # Never sleep on the asyncio event-loop thread — that would freeze the
        # whole server. Such callers get an immediate yes/no.
        try:
            asyncio.get_running_loop()
            max_wait = 0.0
        except RuntimeError:
            pass

        cap = self._cap(priority)
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)
                in_min = len(self._calls)
                in_sec = sum(1 for ts, _ in reversed(self._calls) if now - ts < 1.0) if self._calls else 0
                if in_min < cap and in_sec < PER_SEC:
                    self._calls.append((now, priority))
                    return True
                # Time until the blocking window frees a slot
                if in_min >= cap:
                    wait = 60.0 - (now - self._calls[in_min - cap][0])
                else:
                    wait = 1.0 - (now - self._calls[-PER_SEC][0])
                wait = max(0.02, wait)
                if now + wait > deadline:
                    self._throttled.append(now)
                    self.throttled_total += 1
                    return False
            time.sleep(min(wait, 0.25))

    def record_response(self, resp: Any) -> None:
        if not isinstance(resp, dict):
            return
        code = resp.get("code")
        msg = str(resp.get("message", "")).lower()
        now = time.monotonic()
        with self._lock:
            if code in _RATE_LIMIT_CODES or "request limit" in msg or "requests/" in msg:
                self._fyers_429.append(now)
                self.fyers_429_total += 1
            elif code in _AUTH_ERROR_CODES or "authenticate" in msg or (
                    resp.get("s") != "ok" and "token" in msg and ("expired" in msg or "invalid" in msg)):
                if self.token_rejected_at is None:
                    logger.warning(f"Fyers rejected the access token (code={code}: {resp.get('message')}) — "
                                   f"pausing data calls until a new token is set")
                self.token_rejected_at = time.time()
                self._last_auth_probe = now  # next probe in AUTH_PROBE_INTERVAL
            elif resp.get("s") == "ok":
                self.last_ok_at = time.time()
                self.token_rejected_at = None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            by_prio = {CRITICAL: 0, HIGH: 0, LOW: 0}
            for _, p in self._calls:
                by_prio[p] = by_prio.get(p, 0) + 1
            return {
                "requests_last_min": len(self._calls),
                "by_priority": by_prio,
                "fyers_limit_per_min": FYERS_PER_MIN,
                "soft_cap_per_min": SOFT_PER_MIN,
                "hard_cap_per_min": HARD_PER_MIN,
                "per_sec_cap": PER_SEC,
                "throttled_last_min": len(self._throttled),
                "throttled_total": self.throttled_total,
                "fyers_429_last_min": len(self._fyers_429),
                "fyers_429_total": self.fyers_429_total,
                "token_rejected_at": self.token_rejected_at,
                "auth_blocked_total": self.auth_blocked_total,
                "last_ok_at": self.last_ok_at,
            }


_limiter = FyersRateLimiter()


def get_limiter() -> FyersRateLimiter:
    return _limiter


class RateLimitedFyers:
    """Proxy around ``fyersModel.FyersModel`` that routes calls via the limiter.

    ``with_priority(p)`` returns a view whose calls all use priority ``p`` —
    used by Fly-High so its option-candle fetches outrank dashboard charts.
    """

    def __init__(self, model: Any, limiter: FyersRateLimiter | None = None, priority: str | None = None):
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_limiter", limiter or _limiter)
        object.__setattr__(self, "_priority", priority)

    def with_priority(self, priority: str) -> "RateLimitedFyers":
        return RateLimitedFyers(self._model, self._limiter, priority)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._model, name)
        if name.startswith("_") or not callable(attr):
            return attr
        priority = self._priority or method_priority(name)
        limiter = self._limiter

        def call(*args: Any, **kwargs: Any) -> Any:
            if not limiter.auth_allows(priority):
                return {
                    "s": "error",
                    "code": AUTH_BLOCKED_CODE,
                    "message": "Fyers token rejected — log in again to resume (call skipped locally)",
                }
            if not limiter.acquire(priority):
                logger.warning(f"Fyers {name} throttled locally ({priority}) — request budget in use")
                return {
                    "s": "error",
                    "code": THROTTLED_CODE,
                    "message": f"Throttled locally: Fyers request budget in use ({priority} priority)",
                }
            resp = attr(*args, **kwargs)
            limiter.record_response(resp)
            return resp

        call.__name__ = name
        return call

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._model, name, value)
