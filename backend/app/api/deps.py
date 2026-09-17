"""FastAPI dependencies for authentication and database access."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token, validate_role
from app.db.session import get_db
from app.models.user import User

# auto_error is off so the missing-credentials case is answered here, with the
# same status and challenge as an invalid one. Left on, the framework answers a
# missing Authorization header with 403, which says "you may not" about a caller
# that has not said who it is -- and a client cannot tell from it that logging in
# would help.
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    Dependency to get the current authenticated user.

    Validates JWT token and returns User model.
    Raises 401 if credentials are absent, invalid, or the user is unknown.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = decode_token(credentials.credentials)

    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user from database
    result = await db.execute(
        select(User).where(User.username == token_data.sub, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


async def get_current_active_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Dependency to ensure user is active."""
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return user


def require_role(required_role: str):
    """
    Factory for role-based access control dependency.

    Usage:
        @router.get("/admin-only")
        async def admin_endpoint(user: User = Depends(require_role("ADMIN"))):
            ...
    """

    async def role_checker(
        user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        if not validate_role(required_role, user.role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{required_role}' or higher required",
            )
        return user

    return role_checker


# Type aliases for common dependencies
CurrentUser = Annotated[User, Depends(get_current_active_user)]
AdminUser = Annotated[User, Depends(require_role("ADMIN"))]
OperatorUser = Annotated[User, Depends(require_role("OPERATOR"))]
DbSession = Annotated[AsyncSession, Depends(get_db)]
