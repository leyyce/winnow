"""
Async SQLAlchemy engine, session factory, and FastAPI dependency.

The engine is created once at module import time using ``DATABASE_URL``
assembled dynamically from POSTGRES_* env vars in ``app/core/config.py``.
Tests override ``get_db`` via FastAPI's ``dependency_overrides`` to inject
an in-memory SQLite session — the production URL is never used during tests.

``get_db`` is an async context manager used as a FastAPI dependency:

    async def my_endpoint(db: AsyncSession = Depends(get_db)):
        ...

References
----------
* Database design: docs/architecture/05_database_design.md §5, §6 step 6
* Config:          app/core/config.py
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
# ``pool_pre_ping=True`` discards stale connections after a PostgreSQL
# restart without raising an error to the caller.
# ``echo`` is gated on DEBUG so SQL statements are logged in development
# but silent in production.
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    echo=settings.DEBUG,
    # asyncpg handles its own connection pooling; keep SQLAlchemy pool small
    pool_size=5,
    max_overflow=10,
)

# ── Session factory ───────────────────────────────────────────────────────────
# ``expire_on_commit=False`` prevents SQLAlchemy from expiring all attributes
# after a commit, which would trigger lazy-loads on already-committed objects
# in an async context — a common source of "MissingGreenlet" errors.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── FastAPI dependency ────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an ``AsyncSession`` scoped to a single HTTP request.

    The session is committed on clean exit and rolled back on any exception,
    then closed unconditionally in the ``finally`` block.  This ensures
    every request gets a fresh, clean session with no dangling transactions.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
