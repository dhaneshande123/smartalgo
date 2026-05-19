"""
Authentication data models for the algo trading platform.

Defines user roles, user representation, token responses, and login requests
used across the auth system.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


class UserRole(str, Enum):
    """Platform user roles with descending privilege levels."""

    ADMIN = "ADMIN"
    TRADER = "TRADER"
    VIEWER = "VIEWER"


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------


class User(BaseModel):
    """Authenticated platform user."""

    id: str = Field(..., description="Unique user identifier")
    username: str = Field(..., min_length=1)
    role: UserRole = Field(default=UserRole.VIEWER)
    is_active: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Auth request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Credentials submitted by a user to obtain tokens."""

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    """JWT token pair returned after successful authentication."""

    access_token: str
    refresh_token: str
    token_type: str = Field(default="bearer")
