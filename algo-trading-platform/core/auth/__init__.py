"""
Authentication sub-package for the algo trading platform.

Re-exports the main public API so consumers can write::

    from core.auth import JWTHandler, get_current_user, require_role, User
"""

from core.auth.jwt_handler import JWTHandler
from core.auth.middleware import api_key_auth, get_current_user, require_role
from core.auth.models import LoginRequest, TokenResponse, User, UserRole

__all__ = [
    "JWTHandler",
    "LoginRequest",
    "TokenResponse",
    "User",
    "UserRole",
    "api_key_auth",
    "get_current_user",
    "require_role",
]
