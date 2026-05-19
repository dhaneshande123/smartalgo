"""
FastAPI authentication middleware and dependency functions.

Provides:
* ``get_current_user`` -- dependency that extracts a ``User`` from the
  ``Authorization: Bearer <token>`` header.
* ``require_role(role)`` -- dependency factory for role-based access control.
* ``APIKeyAuth`` -- alternative authentication via ``X-API-Key`` header for
  programmatic / service-to-service access.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

import jwt  # PyJWT
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.auth.jwt_handler import JWTHandler
from core.auth.models import User, UserRole

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared JWTHandler singleton (lazy-initialised)
# ---------------------------------------------------------------------------

_jwt_handler: JWTHandler | None = None
_bearer_scheme = HTTPBearer(auto_error=False)


def _get_jwt_handler() -> JWTHandler:
    """Return (and lazily create) the module-level ``JWTHandler``."""
    global _jwt_handler
    if _jwt_handler is None:
        _jwt_handler = JWTHandler()
    return _jwt_handler


# ---------------------------------------------------------------------------
# Bearer-token dependency
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> User:
    """FastAPI dependency: extract and validate a JWT from the request.

    Returns a ``User`` instance on success.

    Raises
    ------
    HTTPException 401
        If the token is missing, expired, or invalid.
    HTTPException 403
        If the user account is flagged inactive.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    handler = _get_jwt_handler()

    try:
        payload = handler.decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = User(
        id=payload["sub"],
        username=payload.get("username", payload["sub"]),
        role=UserRole(payload.get("role", UserRole.VIEWER.value)),
        is_active=True,
    )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    return user


# ---------------------------------------------------------------------------
# Role-based access dependency factory
# ---------------------------------------------------------------------------

# Define a hierarchy so ADMIN > TRADER > VIEWER.
_ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.ADMIN: 30,
    UserRole.TRADER: 20,
    UserRole.VIEWER: 10,
}


def require_role(minimum_role: UserRole) -> Callable:
    """Return a FastAPI dependency that enforces a minimum role level.

    Usage::

        @router.get("/admin-only")
        async def admin_endpoint(user: User = Depends(require_role(UserRole.ADMIN))):
            ...
    """

    async def _role_checker(user: User = Depends(get_current_user)) -> User:
        user_level = _ROLE_HIERARCHY.get(user.role, 0)
        required_level = _ROLE_HIERARCHY.get(minimum_role, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum_role.value} role or higher",
            )
        return user

    return _role_checker


# ---------------------------------------------------------------------------
# API-key authentication (programmatic access)
# ---------------------------------------------------------------------------

_API_KEY_HEADER = "X-API-Key"


async def api_key_auth(
    x_api_key: str | None = Header(None, alias=_API_KEY_HEADER),
) -> User:
    """FastAPI dependency: authenticate via a static API key.

    The expected key is read from the ``PLATFORM_API_KEY`` environment
    variable.  If the variable is unset, API-key auth is disabled and all
    requests are rejected with 401.

    Returns a service-level ``User`` with ADMIN role on success.
    """
    expected_key = os.environ.get("PLATFORM_API_KEY", "")

    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API-key authentication is not configured",
        )

    if not x_api_key or x_api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

    return User(
        id="service-account",
        username="api-key-user",
        role=UserRole.ADMIN,
        is_active=True,
    )
