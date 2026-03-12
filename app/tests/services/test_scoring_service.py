"""
Unit tests for app/services/scoring_service.py.

Tests exercise ``process_submission()`` directly — no HTTP layer involved.
All tests rely on the session-scoped ``_populate_registry`` fixture from
conftest.py to ensure the tree-app project is registered before any call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.envelope import SubmissionEnvelope, SubmissionMetadata
from app.schemas.results import ScoringResultResponse
from app.services.scoring_service import process_submission
from app.tests.conftest import _ctx, _payload


# ── Helpers ───────────────────────────────────────────────────────────────────

def _envelope(
    project_id: str = "tree-app",
    payload: dict | None = None,
) -> SubmissionEnvelope:
    """Build a minimal valid SubmissionEnvelope for the given project."""
    if payload is None:
        payload = _payload().model_dump(mode="json")
    return SubmissionEnvelope(
        metadata=SubmissionMetadata(
            project_id=project_id,
            submission_id=uuid4(),
            submission_type="tree_measurement",
            submitted_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        user_context=_ctx(trust_level=50),
        payload=payload,
    )


# ── Happy path ────────────────────────────────────────────────────────────────

async def test_process_submission_returns_scoring_result_response() -> None:
    """Valid envelope → ScoringResultResponse with correct shape."""
    result = await process_submission(_envelope())

    assert isinstance(result, ScoringResultResponse)
    assert result.project_id == "tree-app"
    assert result.status == "pending_finalization"


async def test_process_submission_submission_id_echoed() -> None:
    """submission_id in response must match the one sent in the envelope."""
    envelope = _envelope()
    result = await process_submission(envelope)

    assert result.submission_id == envelope.metadata.submission_id


async def test_process_submission_confidence_score_in_range() -> None:
    """confidence_score must be a float in [0, 100]."""
    result = await process_submission(_envelope())

    assert isinstance(result.confidence_score, float)
    assert 0.0 <= result.confidence_score <= 100.0


async def test_process_submission_breakdown_non_empty() -> None:
    """Breakdown must contain at least one rule contribution."""
    result = await process_submission(_envelope())

    assert len(result.breakdown) > 0
    for entry in result.breakdown:
        assert 0.0 <= entry.score <= 1.0
        assert 0.0 <= entry.weight <= 1.0
        assert entry.weighted_score >= 0.0


async def test_process_submission_required_validations_shape() -> None:
    """required_validations must have the correct shape."""
    result = await process_submission(_envelope())

    rv = result.required_validations
    assert rv.min_validators >= 1
    assert rv.required_min_trust >= 0
    assert isinstance(rv.review_tier, str) and len(rv.review_tier) > 0


async def test_process_submission_thresholds_ordered() -> None:
    """Returned thresholds must satisfy approve >= review >= reject."""
    result = await process_submission(_envelope())

    t = result.thresholds
    assert t.approve >= t.review >= t.reject


async def test_process_submission_created_at_is_datetime() -> None:
    """created_at must be a timezone-aware datetime."""
    result = await process_submission(_envelope())

    assert isinstance(result.created_at, datetime)
    assert result.created_at.tzinfo is not None


# ── Unknown project ───────────────────────────────────────────────────────────

async def test_process_submission_unknown_project_raises_key_error() -> None:
    """Unknown project_id must raise KeyError before any scoring runs."""
    envelope = _envelope(project_id="no-such-project")

    with pytest.raises(KeyError, match="no-such-project"):
        await process_submission(envelope)


# ── Stage 1 validation failure ────────────────────────────────────────────────

async def test_process_submission_invalid_payload_raises_validation_error() -> None:
    """Payload that fails Stage 1 schema validation must raise ValidationError."""
    bad_payload = {"tree_id": "not-a-uuid", "completely": "wrong"}
    envelope = _envelope(payload=bad_payload)

    with pytest.raises(ValidationError):
        await process_submission(envelope)


async def test_process_submission_empty_payload_raises_validation_error() -> None:
    """Completely empty payload must fail Stage 1 with ValidationError."""
    envelope = _envelope(payload={})

    with pytest.raises(ValidationError):
        await process_submission(envelope)
