"""Security utilities: password hashing, JWT tokens, and authorization."""

from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt
from pydantic import BaseModel

from app.config import get_settings

settings = get_settings()

# Argon2 password hasher (recommended over bcrypt)
ph = PasswordHasher()


class TokenData(BaseModel):
    """JWT token payload data."""

    sub: str  # username
    role: str
    exp: datetime


class Token(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


def hash_password(password: str) -> str:
    """Hash a password using Argon2."""
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        ph.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """Check if password hash needs to be rehashed with new parameters."""
    return ph.check_needs_rehash(password_hash)


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> Token:
    """Create a JWT access token."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.token_expire_minutes)

    expire = datetime.now(UTC) + expires_delta
    to_encode = data.copy()
    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    return Token(access_token=encoded_jwt, expires_at=expire)


def decode_token(token: str) -> TokenData | None:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return TokenData(
            sub=payload.get("sub", ""),
            role=payload.get("role", "VIEWER"),
            exp=datetime.fromtimestamp(payload.get("exp", 0), tz=UTC),
        )
    except JWTError:
        return None


def validate_role(required_role: str, user_role: str) -> bool:
    """Check if user role meets the required permission level."""
    role_hierarchy = {"VIEWER": 0, "OPERATOR": 1, "ADMIN": 2}
    return role_hierarchy.get(user_role, 0) >= role_hierarchy.get(required_role, 0)
