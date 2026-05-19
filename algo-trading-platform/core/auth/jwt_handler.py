"""
JWT authentication handler for the algo trading platform.

Provides token creation (access + refresh) and verification using
HS256 signing.  The signing secret is read from the DASHBOARD_SECRET
environment variable (falling back to the config system).

Dependencies: PyJWT (``pip install PyJWT``).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt  # PyJWT

from core.auth.models import UserRole

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_ACCESS_EXPIRE_MINUTES = 30
_DEFAULT_REFRESH_EXPIRE_DAYS = 7
_ALGORITHM = "HS256"

# Token type markers embedded in the payload so access and refresh tokens
# cannot be used interchangeably.
_TOKEN_TYPE_ACCESS = "access"
_TOKEN_TYPE_REFRESH = "refresh"


# ---------------------------------------------------------------------------
# JWTHandler
# ---------------------------------------------------------------------------


class JWTHandler:
    """Create and verify JWT tokens for the platform.

    Parameters
    ----------
    secret:
        HMAC signing key.  If *None*, the ``DASHBOARD_SECRET`` environment
        variable is used.  Raises ``ValueError`` when no secret is available.
    access_expire_minutes:
        Lifetime of an access token in minutes (default 30).
    refresh_expire_days:
        Lifetime of a refresh token in days (default 7).
    """

    def __init__(
        self,
        secret: str | None = None,
        access_expire_minutes: int = _DEFAULT_ACCESS_EXPIRE_MINUTES,
        refresh_expire_days: int = _DEFAULT_REFRESH_EXPIRE_DAYS,
    ) -> None:
        self._secret = secret or os.environ.get("DASHBOARD_SECRET", "")
        if not self._secret:
            raise ValueError(
                "JWT secret is not configured.  Set the DASHBOARD_SECRET "
                "environment variable or pass `secret` explicitly."
            )
        self._access_expire = timedelta(minutes=access_expire_minutes)
        self._refresh_expire = timedelta(days=refresh_expire_days)

    # -- token creation -----------------------------------------------------

    def create_access_token(
        self,
        user_id: str,
        role: UserRole | str,
        expires_delta: timedelta | None = None,
    ) -> str:
        """Return an encoded JWT access token.

        The payload contains:

        * ``sub`` -- the user ID
        * ``role`` -- the user's role
        * ``type`` -- ``"access"``
        * ``exp`` -- expiration timestamp
        * ``iat`` -- issued-at timestamp
        """
        now = datetime.now(timezone.utc)
        expire = now + (expires_delta or self._access_expire)

        payload: dict[str, Any] = {
            "sub": user_id,
            "role": str(role.value if isinstance(role, UserRole) else role),
            "type": _TOKEN_TYPE_ACCESS,
            "exp": expire,
            "iat": now,
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def create_refresh_token(
        self,
        user_id: str,
        expires_delta: timedelta | None = None,
    ) -> str:
        """Return an encoded JWT refresh token.

        The payload contains ``sub``, ``type="refresh"``, ``exp``, and ``iat``.
        """
        now = datetime.now(timezone.utc)
        expire = now + (expires_delta or self._refresh_expire)

        payload: dict[str, Any] = {
            "sub": user_id,
            "type": _TOKEN_TYPE_REFRESH,
            "exp": expire,
            "iat": now,
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    # -- token verification -------------------------------------------------

    def decode_token(self, token: str) -> dict[str, Any]:
        """Decode and verify a JWT token.

        Returns the decoded payload dict on success.

        Raises
        ------
        jwt.ExpiredSignatureError
            If the token has expired.
        jwt.InvalidTokenError
            If the token is malformed or fails signature verification.
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            raise
        except jwt.InvalidTokenError as exc:
            logger.warning("Invalid token: %s", exc)
            raise

    def decode_access_token(self, token: str) -> dict[str, Any]:
        """Decode a token and verify it is an *access* token."""
        payload = self.decode_token(token)
        if payload.get("type") != _TOKEN_TYPE_ACCESS:
            raise jwt.InvalidTokenError("Token is not an access token")
        return payload

    def decode_refresh_token(self, token: str) -> dict[str, Any]:
        """Decode a token and verify it is a *refresh* token."""
        payload = self.decode_token(token)
        if payload.get("type") != _TOKEN_TYPE_REFRESH:
            raise jwt.InvalidTokenError("Token is not a refresh token")
        return payload
