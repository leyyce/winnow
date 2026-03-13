"""
Shared pytest configuration and fixtures for the Winnow test suite.

DB Strategy (Sprint 3)
----------------------
Each test gets a fresh in-memory SQLite database via a function-scoped
``db_engine`` fixture.  Tables are created once per engine and dropped on
teardown.  The ``db_session`` fixture yields an ``AsyncSession`` that is
**rolled back** after every test, giving cheap write isolation without the
complexity of savepoints or a shared connection pool.

The ``async_client`` fixture overrides the FastAPI ``get_db`` dependency to
inject the test ``db_session``, so every HTTP request in a test runs against
the same in-memory SQLite database as direct service calls in that test.

Event-loop
----------
``asyncio_mode = "auto"`` is set in ``pyproject.toml``; all async fixtures
and tests run automatically without ``@pytest.mark.asyncio``.

References
----------
* Database design: docs/architecture/05_database_design.md §6 step 12
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bootstrap import bootstrap
from app.db.session import get_db
from app.models.base import Base
from app.schemas.envelope import UserContext
from app.schemas.projects.trees import (
    SpeciesStats,
    TreeMeasurementPayload,
    TreePayload,
    TreePhotoPayload,
)
from app.scoring.common.trust_advisor import UserSubmissionStats

# ── Session-scoped bootstrap ──────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _populate_registry() -> None:
    """Populate the project registry once per test session."""
    bootstrap()


# ── Per-test async SQLite engine + session ────────────────────────────────────

@pytest.fixture
async def db_engine():
    """
    Function-scoped async SQLite in-memory engine.

    A fresh engine (and therefore a fresh in-memory database) is created for
    every test, so tests are completely isolated with zero shared state.
    Tables are created on setup and the engine is disposed on teardown.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        # SQLite doesn't need pool_size / max_overflow
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncSession:
    """
    Function-scoped async session backed by the test SQLite engine.

    Rolled back after each test so that any DB writes are discarded, giving
    cheap isolation without recreating the schema on every test.
    """
    factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with factory() as session:
        yield session
        await session.rollback()


# ── Async HTTP client fixture ─────────────────────────────────────────────────

@pytest.fixture
async def async_client(db_session: AsyncSession) -> AsyncClient:
    """
    Return an ``httpx.AsyncClient`` wired to the FastAPI app.

    The ``get_db`` dependency is overridden to inject the test ``db_session``
    so every API request in a test runs against the same in-memory SQLite
    database as any direct service calls made in that test.
    """
    from app.main import app

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


# ── Shared timestamp ──────────────────────────────────────────────────────────

_FIXED_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ── Shared helper functions ───────────────────────────────────────────────────

def _ctx(trust_level: int = 50) -> UserContext:
    """Return a minimal ``UserContext`` with a configurable trust level."""
    return UserContext(
        user_id=uuid4(),
        username="tester",
        role="citizen",
        trust_level=trust_level,
        total_submissions=5,
        account_created_at=_FIXED_TS,
    )


def _default_stats() -> SpeciesStats:
    """Return a ``SpeciesStats`` instance with sensible default values."""
    return SpeciesStats(
        mean_height=20.0, std_height=5.0,
        mean_inclination=5.0, std_inclination=2.0,
        mean_trunk_diameter=30.0, std_trunk_diameter=10.0,
    )


def _payload(
    height: float = 20.0,
    inclination: int = 5,
    trunk_diameter: int = 30,
    note: str | None = None,
    step_length_measured: bool = True,
    photos: list[TreePhotoPayload] | None = None,
    species_stats: SpeciesStats | None = None,
) -> TreePayload:
    """Return a valid ``TreePayload`` with configurable measurement fields."""
    if photos is None:
        photos = [TreePhotoPayload(path="a.jpg"), TreePhotoPayload(path="b.jpg")]
    return TreePayload(
        tree_id=uuid4(),
        species_id=uuid4(),
        measurement=TreeMeasurementPayload(
            height=height,
            inclination=inclination,
            trunk_diameter=trunk_diameter,
            note=note,
        ),
        photos=photos,
        step_length_measured=step_length_measured,
        species_stats=species_stats or _default_stats(),
    )


def _user_stats(consecutive: int = 0) -> UserSubmissionStats:
    """Return a ``UserSubmissionStats`` instance with a configurable streak."""
    return UserSubmissionStats(
        total_finalized=10,
        total_approved=8,
        total_rejected=2,
        consecutive_approvals=consecutive,
    )
