"""
Scoring service — application-layer orchestrator for the submission lifecycle.

This module wires together the registry, scoring pipeline, and governance
policy into two async service functions that the API endpoints call directly.
No database persistence is performed yet; all DB calls are stubbed out.

Orchestration contracts are defined in docs/architecture/02_architecture_patterns.md §3.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError

from app.registry.manager import registry
from app.schemas.envelope import SubmissionEnvelope
from app.schemas.finalization import (
    FinalizationRequest,
    FinalizationResponse,
    TrustAdjustment,
)
from app.schemas.results import RuleBreakdown, ScoringResultResponse
from app.scoring.common.trust_advisor import UserSubmissionStats

logger = logging.getLogger(__name__)


async def process_submission(envelope: SubmissionEnvelope) -> ScoringResultResponse:
    """
    Orchestrate Stage 1 validation → Stage 2+4 scoring → governance requirements.

    Steps
    -----
    1. Resolve project config from registry (KeyError → 422 at API layer).
    2. Validate raw payload against project schema (ValidationError → 422).
    3. [STUB] Persist submission — not implemented until DB layer is added.
    4. Run scoring pipeline → PipelineResult.
    5. Determine governance requirements → RequiredValidations.
    6. [STUB] Persist scoring result — not implemented until DB layer is added.
    7. Return ScoringResultResponse with status='pending_finalization'.

    Raises
    ------
    KeyError
        If ``envelope.metadata.project_id`` is not registered.
    pydantic.ValidationError
        If the raw payload fails Stage 1 schema validation.
    """
    project_id = envelope.metadata.project_id
    submission_id = envelope.metadata.submission_id

    # Step 1 — resolve project config (raises KeyError on unknown project)
    config = registry.get_config(project_id)

    # Step 2 — Stage 1: validate raw payload against project-specific schema
    validated_payload = config.payload_schema.model_validate(envelope.payload)

    # Step 3 — [STUB] persist submission
    # TODO: persist submission to DB

    # Step 4 — Stage 2 + 4-input: run scoring pipeline
    pipeline_result = config.pipeline.run(validated_payload, envelope.user_context)

    # Step 5 — governance: determine review requirements
    required = config.governance_policy.determine_requirements(
        pipeline_result.total_score,
        envelope.user_context,
    )

    # Step 6 — [STUB] persist scoring result
    # TODO: persist scoring result to DB

    # Step 7 — assemble and return response
    breakdown = [
        RuleBreakdown(
            rule=r.rule_name,
            weight=next(
                (rule.weight for rule in config.pipeline.rules if rule.name == r.rule_name),
                0.0,
            ),
            score=r.score,
            weighted_score=r.score * next(
                (rule.weight for rule in config.pipeline.rules if rule.name == r.rule_name),
                0.0,
            ) * 100.0,
            details=r.details,
        )
        for r in pipeline_result.breakdown
    ]

    logger.info(
        "Submission scored",
        extra={
            "submission_id": str(submission_id),
            "project_id": project_id,
            "confidence_score": pipeline_result.total_score,
        },
    )

    return ScoringResultResponse(
        submission_id=submission_id,
        project_id=project_id,
        status="pending_finalization",
        confidence_score=pipeline_result.total_score,
        breakdown=breakdown,
        required_validations=required,
        thresholds=config.thresholds,
        created_at=datetime.now(timezone.utc),
    )


async def finalize_submission(
    submission_id: UUID,
    request: FinalizationRequest,
) -> FinalizationResponse:
    """
    Close the feedback loop: record ground-truth outcome and compute trust delta.

    Steps
    -----
    1. [STUB] Load submission — raises HTTPException(501) until DB layer is added.
    2. [STUB] Update submission status.
    3. Compute trust adjustment recommendation via TrustAdvisor.
    4. Return FinalizationResponse.

    Raises
    ------
    fastapi.HTTPException(501)
        Always, until the DB persistence layer is implemented.
    """
    from fastapi import HTTPException

    # Step 1 — [STUB] load submission from DB
    # TODO: load submission from DB and resolve config
    raise HTTPException(
        status_code=501,
        detail=(
            f"finalize_submission({submission_id!s}) is not yet implemented. "
            "The database persistence layer has not been added."
        ),
    )
