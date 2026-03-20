"""
Unit tests for app/services/scoring_service.py.

Tests exercise ``process_submission()`` directly — no HTTP layer involved.
All tests receive a ``db_session`` fixture (async SQLite, rolled back after
each test) and pass it to the service so DB persistence is exercised without
needing a real PostgreSQL instance.

Coverage
--------
- Happy path response shape and field values
- RequiredValidations new role-weights schema fields
- Confidence score bounds
- Multiple distinct submissions produce distinct IDs
- Unknown project raises ProjectNotFoundError
- Invalid payload raises ValidationError (Stage 1 failure)
- Extreme payload values produce scores in [0, 100]
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ProjectNotFoundError
from app.schemas.envelope import SubmissionEnvelope, SubmissionMetadata
from app.schemas.results import ScoringResultResponse
from app.services.scoring_service import process_submission
from app.tests.conftest import _ctx, _payload


# ── Helpers ───────────────────────────────────────────────────────────────────

def _envelope(
    project_id: str = "tree-app",
    payload: dict | None = None,
    trust_level: int = 50,
) -> SubmissionEnvelope:
    """Build a minimal valid SubmissionEnvelope for the given project."""
    if payload is None:
        payload = _payload().model_dump(mode="json")
    return SubmissionEnvelope(
        metadata=SubmissionMetadata(
            project_id=project_id,
            submission_id=uuid4(),
            entity_type="tree_measurement",
            entity_id=uuid4(),
            measurement_id=uuid4(),
            submitted_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        user_context=_ctx(trust_level=trust_level),
        payload=payload,
    )


# ── Happy path ────────────────────────────────────────────────────────────────

async def test_process_submission_returns_scoring_result_response(
    db_session: AsyncSession,
) -> None:
    """Valid envelope → ScoringResultResponse with correct shape."""
    result = await process_submission(_envelope(), db_session)
    assert isinstance(result, ScoringResultResponse)
    assert result.project_id == "tree-app"
    assert result.status == "pending_review"


async def test_process_submission_submission_id_echoed(
    db_session: AsyncSession,
) -> None:
    """submission_id in response must match the one sent in the envelope."""
    envelope = _envelope()
    result = await process_submission(envelope, db_session)
    assert result.submission_id == envelope.metadata.submission_id


async def test_process_submission_confidence_score_in_range(
    db_session: AsyncSession,
) -> None:
    """confidence_score must be a float in [0, 100]."""
    result = await process_submission(_envelope(), db_session)
    assert isinstance(result.confidence_score, float)
    assert 0.0 <= result.confidence_score <= 100.0


async def test_process_submission_breakdown_non_empty(
    db_session: AsyncSession,
) -> None:
    """Breakdown must contain at least one rule contribution."""
    result = await process_submission(_envelope(), db_session)
    assert len(result.breakdown) > 0
    for entry in result.breakdown:
        assert 0.0 <= entry.score <= 1.0
        assert 0.0 <= entry.weight <= 1.0
        assert entry.weighted_score >= 0.0


async def test_process_submission_required_validations_new_schema(
    db_session: AsyncSession,
) -> None:
    """required_validations must expose Sprint 5 role_configs/default_config/blocked_roles."""
    result = await process_submission(_envelope(), db_session)
    # required_validations is now a list of all applicable tiers (cumulative)
    assert isinstance(result.required_validations, list)
    assert len(result.required_validations) >= 1
    rv = result.required_validations[0]
    assert rv.threshold_score >= 1
    assert isinstance(rv.role_configs, dict)
    assert isinstance(rv.default_config.weight, int)
    assert rv.default_config.min_trust >= 0
    assert isinstance(rv.blocked_roles, list)
    for role, cfg in rv.role_configs.items():
        assert isinstance(role, str)
        assert cfg.weight >= 0
        assert cfg.min_trust >= 0
    assert isinstance(rv.review_tier, str) and len(rv.review_tier) > 0


async def test_process_submission_thresholds_ordered(
    db_session: AsyncSession,
) -> None:
    """Returned thresholds must satisfy auto_approve_min >= manual_review_min."""
    result = await process_submission(_envelope(), db_session)
    t = result.thresholds
    assert t.auto_approve_min >= t.manual_review_min


async def test_process_submission_created_at_is_datetime(
    db_session: AsyncSession,
) -> None:
    """created_at must be a timezone-aware datetime."""
    result = await process_submission(_envelope(), db_session)
    assert isinstance(result.created_at, datetime)
    assert result.created_at.tzinfo is not None


async def test_process_submission_distinct_envelopes_produce_distinct_ids(
    db_session: AsyncSession,
) -> None:
    """Two separate envelopes with different UUIDs must echo their own submission_id."""
    e1 = _envelope()
    e2 = _envelope()
    r1 = await process_submission(e1, db_session)
    r2 = await process_submission(e2, db_session)
    assert r1.submission_id != r2.submission_id
    assert r1.submission_id == e1.metadata.submission_id
    assert r2.submission_id == e2.metadata.submission_id


async def test_process_submission_idempotent_same_id_returns_stored(
    db_session: AsyncSession,
) -> None:
    """Re-submitting the same submission_id must return the stored result unchanged."""
    envelope = _envelope()
    r1 = await process_submission(envelope, db_session)
    r2 = await process_submission(envelope, db_session)
    assert r1.submission_id == r2.submission_id
    assert r1.confidence_score == r2.confidence_score
    assert r1.status == r2.status


# ── Governance tier routing via confidence score ──────────────────────────────

async def test_low_trust_submission_lands_in_stricter_tier(
    db_session: AsyncSession,
) -> None:
    """A submission from a low-trust user typically scores lower."""
    result_low = await process_submission(_envelope(trust_level=1), db_session)
    result_high = await process_submission(_envelope(trust_level=100), db_session)
    assert 0.0 <= result_low.confidence_score <= 100.0
    assert 0.0 <= result_high.confidence_score <= 100.0
    assert result_high.confidence_score >= result_low.confidence_score


# ── Extreme payload values ────────────────────────────────────────────────────

async def test_process_submission_max_height_gives_score_in_range(
    db_session: AsyncSession,
) -> None:
    """Payload with very large height clamps to score in [0, 100]."""
    payload = _payload(height=9999.0).model_dump(mode="json")
    result = await process_submission(_envelope(payload=payload), db_session)
    assert 0.0 <= result.confidence_score <= 100.0


async def test_process_submission_inclination_at_90_gives_score_in_range(
    db_session: AsyncSession,
) -> None:
    """Boundary inclination (90°) is accepted and scores in [0, 100]."""
    payload = _payload(inclination=90).model_dump(mode="json")
    result = await process_submission(_envelope(payload=payload), db_session)
    assert 0.0 <= result.confidence_score <= 100.0


# ── Unknown project ───────────────────────────────────────────────────────────

async def test_process_submission_unknown_project_raises_project_not_found_error(
    db_session: AsyncSession,
) -> None:
    """Unknown project_id must raise ProjectNotFoundError before any scoring runs."""
    envelope = _envelope(project_id="no-such-project")
    with pytest.raises(ProjectNotFoundError, match="no-such-project"):
        await process_submission(envelope, db_session)


# ── Stage 1 validation failure ────────────────────────────────────────────────

async def test_process_submission_invalid_payload_raises_validation_error(
    db_session: AsyncSession,
) -> None:
    """Payload that fails Stage 1 schema validation must raise ValidationError."""
    bad_payload = {"tree_id": "not-a-uuid", "completely": "wrong"}
    envelope = _envelope(payload=bad_payload)
    with pytest.raises(ValidationError):
        await process_submission(envelope, db_session)


async def test_process_submission_empty_payload_raises_validation_error(
    db_session: AsyncSession,
) -> None:
    """Completely empty payload must fail Stage 1 with ValidationError."""
    envelope = _envelope(payload={})
    with pytest.raises(ValidationError):
        await process_submission(envelope, db_session)


async def test_process_submission_missing_species_id_raises_validation_error(
    db_session: AsyncSession,
) -> None:
    """Payload missing required species_id must fail Stage 1 validation."""
    payload = _payload().model_dump(mode="json")
    del payload["species_id"]
    envelope = _envelope(payload=payload)
    with pytest.raises(ValidationError):
        await process_submission(envelope, db_session)
