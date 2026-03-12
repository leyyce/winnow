"""
Shared pytest configuration and fixtures for the Winnow test suite.

bootstrap() is called once per test session so that every test module
has access to a fully populated registry without relying on import-time
side effects.

Shared helper functions
-----------------------
``_ctx``, ``_payload``, ``_default_stats``, and ``_user_stats`` are plain
module-level factory functions (not pytest fixtures) that are imported
directly by test modules.  Centralising them here eliminates duplication
and ensures a single source of truth for default test data shapes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.bootstrap import bootstrap
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
    """Populate the project registry before any test runs."""
    bootstrap()


# ── Async HTTP client fixture ─────────────────────────────────────────────────

@pytest.fixture
async def async_client() -> AsyncClient:
    """Return an httpx AsyncClient wired directly to the Winnow FastAPI app."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


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
