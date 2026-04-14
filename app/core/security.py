from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


def hash_password(plain_password: str) -> str:
    """Hash a plain-text password using bcrypt."""
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True if plain_password matches the bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def _create_token(payload: dict[str, Any], expires_delta: timedelta) -> str:
    """Build and sign a JWT with the given payload and expiry."""
    expire = datetime.now(timezone.utc) + expires_delta
    payload = payload.copy()
    payload["exp"] = expire
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    """Create a short-lived access token.

    Args:
        subject: The user ID stored in the 'sub' claim.
        extra:   Optional extra claims (e.g. role) merged into the payload.
    """
    payload: dict[str, Any] = {"sub": subject, "type": "access"}
    if extra:
        payload.update(extra)
    return _create_token(
        payload,
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(subject: str) -> str:
    """Create a long-lived refresh token.

    Args:
        subject: The user ID stored in the 'sub' claim.
    """
    payload: dict[str, Any] = {"sub": subject, "type": "refresh"}
    return _create_token(
        payload,
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT.

    Returns:
        The decoded payload dict.

    Raises:
        JWTError: If the token is invalid, expired, or tampered with.
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
