"""
Scoring service — orchestrates submission lifecycle against the database.

Wires together the registry, scoring pipeline, governance policy, and DB
persistence.  Every public function accepts an ``AsyncSession`` so callers
(API endpoints, tests) control the transaction boundary.

DB operations use SQLAlchemy 2.0 async patterns:
* ``db.get()`` for PK lookups (idempotency check)
* ``select()`` for filtered queries
* ``db.add()`` + ``await db.flush()`` to send INSERTs within the current
  transaction without committing (commit is deferred to the ``get_db``
  FastAPI dependency)

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
* Database:     docs/architecture/05_database_design.md §6 steps 2–3, 8
* Rule 9:       Services never import from fastapi / never raise HTTPException
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyFinalizedError, NotImplementedYetError, SubmissionNotFoundError
from app.models.scoring_result import ScoringResult
from app.models.submission import Submission, SubmissionStatus
from app.registry.manager import registry
from app.schemas.envelope import SubmissionEnvelope
from app.schemas.results import RuleBreakdown, ScoringResultResponse, ThresholdConfig
from app.schemas.supersede import SupersedeRequest, SupersedeResponse

logger = logging.getLogger(__name__)


async def process_submission(
    envelope: SubmissionEnvelope,
    db: AsyncSession,
) -> ScoringResultResponse:
    """
    Orchestrate Stage 1 validation → Stage 2+4 scoring → DB persistence.

    Idempotent: if ``submission_id`` already exists in the DB the stored
    result is returned without re-scoring (ADR-DB-004 / Rule 10).

    Steps
    -----
    1. Resolve project config (ProjectNotFoundError → 422 at API layer).
    2. Idempotency check — return existing result if submission_id already stored.
    3. Validate raw payload against project schema (ValidationError → 422).
    4. Run scoring pipeline → PipelineResult.
    5. Determine governance requirements → RequiredValidations.
    6. Persist Submission + ScoringResult in one atomic flush.
    7. Return ScoringResultResponse.

    Raises
    ------
    ProjectNotFoundError
        If ``envelope.metadata.project_id`` is not registered.
    pydantic.ValidationError
        If the raw payload fails Stage 1 schema validation.
    """
    project_id = envelope.metadata.project_id
    submission_id = envelope.metadata.submission_id

    # Step 1 — resolve project config
    config = registry.get_config(project_id)

    # Step 2 — idempotency: return stored result if already processed
    existing = await db.get(Submission, submission_id)
    if existing is not None:
        sr_stmt = select(ScoringResult).where(ScoringResult.submission_id == submission_id)
        sr_row = (await db.execute(sr_stmt)).scalar_one()
        logger.info(
            "Idempotent re-submission — returning stored result",
            extra={"submission_id": str(submission_id), "project_id": project_id},
        )
        return _build_response_from_orm(existing, sr_row)

    # Step 3 — Stage 1: validate raw payload
    validated_payload = config.payload_schema.model_validate(envelope.payload)

    # Step 4 — Stage 2 + 4-input: run scoring pipeline
    pipeline_result = config.pipeline.run(validated_payload, envelope.user_context)

    # Step 5 — governance: determine review requirements
    required = config.governance_policy.determine_requirements(
        pipeline_result.total_score,
        envelope.user_context,
    )

    # Build per-rule breakdown (weight_map avoids O(N²) scan)
    weight_map = {rule.name: rule.weight for rule in config.pipeline.rules}
    breakdown = [
        RuleBreakdown(
            rule=r.rule_name,
            weight=(w := weight_map.get(r.rule_name, 0.0)),
            score=r.score,
            weighted_score=r.score * w * 100.0,
            details=r.details,
        )
        for r in pipeline_result.breakdown
    ]

    # Step 6 — persist Submission + ScoringResult atomically
    submission = Submission(
        submission_id=submission_id,
        project_id=project_id,
        submission_type=envelope.metadata.submission_type,
        user_id=envelope.user_context.user_id,
        user_context=envelope.user_context.model_dump(mode="json"),
        raw_payload=validated_payload.model_dump(mode="json"),
        status=SubmissionStatus.PENDING_REVIEW,
        confidence_score=pipeline_result.total_score,
    )
    sr_row = ScoringResult(
        submission_id=submission_id,
        breakdown=[r.model_dump(mode="json") for r in breakdown],
        required_validations=required.model_dump(mode="json"),
        thresholds=config.thresholds.model_dump(mode="json"),
    )
    db.add(submission)
    db.add(sr_row)
    await db.flush()  # send INSERTs; commit deferred to get_db dependency

    logger.info(
        "Submission scored and persisted",
        extra={
            "submission_id": str(submission_id),
            "project_id": project_id,
            "confidence_score": pipeline_result.total_score,
        },
    )

    # Step 7 — return response
    return ScoringResultResponse(
        submission_id=submission_id,
        project_id=project_id,
        status=SubmissionStatus.PENDING_REVIEW,
        confidence_score=pipeline_result.total_score,
        breakdown=breakdown,
        required_validations=required,
        thresholds=config.thresholds,
        created_at=datetime.now(timezone.utc),
    )


async def get_submission_result(
    submission_id: UUID,
    db: AsyncSession,
) -> ScoringResultResponse:
    """
    Return the stored ScoringResultResponse for the given submission UUID.

    Raises
    ------
    SubmissionNotFoundError
        If ``submission_id`` is not found in the DB.
    """
    submission = await db.get(Submission, submission_id)
    if submission is None:
        raise SubmissionNotFoundError(submission_id)

    sr_stmt = select(ScoringResult).where(ScoringResult.submission_id == submission_id)
    sr_row = (await db.execute(sr_stmt)).scalar_one()
    return _build_response_from_orm(submission, sr_row)


async def supersede_submission(
    submission_id: UUID,
    request: SupersedeRequest,
    db: AsyncSession,
) -> SupersedeResponse:
    """
    Mark a submission as superseded by a newer corrected one.

    Steps
    -----
    1. Load submission with SELECT … FOR UPDATE (prevents concurrent modification).
    2. Validate it is not already in a terminal state.
    3. Transition status → 'superseded'; record superseded_by FK.
    4. Flush (commit deferred to get_db dependency).
    5. Return SupersedeResponse.

    Raises
    ------
    SubmissionNotFoundError
        If ``submission_id`` is not found.
    AlreadyFinalizedError
        If the submission is already in a terminal state
        (approved / rejected / superseded).
    """
    stmt = (
        select(Submission)
        .where(Submission.submission_id == submission_id)
        .with_for_update()
    )
    submission = (await db.execute(stmt)).scalar_one_or_none()

    if submission is None:
        raise SubmissionNotFoundError(submission_id)

    terminal_states = {
        SubmissionStatus.APPROVED,
        SubmissionStatus.REJECTED,
        SubmissionStatus.SUPERSEDED,
    }
    if submission.status in terminal_states:
        raise AlreadyFinalizedError(submission_id, submission.status)

    now = datetime.now(timezone.utc)
    submission.status = SubmissionStatus.SUPERSEDED
    submission.superseded_by = request.superseded_by
    submission.updated_at = now
    await db.flush()

    logger.info(
        "Submission superseded",
        extra={
            "submission_id": str(submission_id),
            "superseded_by": str(request.superseded_by),
        },
    )

    return SupersedeResponse(
        submission_id=submission_id,
        status="superseded",
        superseded_by=request.superseded_by,
        updated_at=now,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_response_from_orm(
    submission: Submission,
    sr_row: ScoringResult,
) -> ScoringResultResponse:
    """
    Reconstruct a ``ScoringResultResponse`` from persisted ORM rows.
    Used for idempotency returns and the GET /results/{id} endpoint.

    SQLite stores datetimes as naive ISO strings.  PostgreSQL returns
    timezone-aware datetimes via ``TIMESTAMPTZ``.  We normalise to UTC
    here so the response always satisfies ``AwareDatetime`` validation.
    """
    from app.schemas.results import RequiredValidations

    breakdown = [RuleBreakdown.model_validate(b) for b in sr_row.breakdown]
    required = RequiredValidations.model_validate(sr_row.required_validations)
    thresholds = ThresholdConfig.model_validate(sr_row.thresholds)

    # Normalise: SQLite returns naive datetimes; PostgreSQL returns aware ones.
    created_at = sr_row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return ScoringResultResponse(
        submission_id=submission.submission_id,
        project_id=submission.project_id,
        status=submission.status,
        confidence_score=submission.confidence_score,
        breakdown=breakdown,
        required_validations=required,
        thresholds=thresholds,
        created_at=created_at,
    )
