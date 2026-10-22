"""Async SQLAlchemy database session management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.models.base import Base

settings = get_settings()

# Create async engine with SQLite WAL mode for better concurrency
engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    connect_args={"check_same_thread": False},  # Required for SQLite
)

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed default admin user if not exists
    async with async_session_factory() as session:
        from sqlalchemy import select

        from app.core.security import hash_password

        # Import dynamically to avoid circular imports during startup
        from app.models.user import User, UserRole

        result = await session.execute(select(User).limit(1))
        if not result.scalar_one_or_none():
            admin = User(
                username="admin",
                email="admin@datak.local",
                password_hash=hash_password("admin"),
                role=UserRole.ADMIN.value,
                is_active=True
            )
            session.add(admin)
            await session.commit()


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get async database session as context manager."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI to get database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
