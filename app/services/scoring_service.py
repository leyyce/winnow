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

from app.core.exceptions import NotImplementedYetError
from app.registry.manager import registry
from app.schemas.envelope import SubmissionEnvelope
# from app.schemas.finalization import FinalizationRequest, FinalizationResponse
from app.schemas.supersede import SupersedeRequest, SupersedeResponse
from app.schemas.results import RuleBreakdown, ScoringResultResponse

logger = logging.getLogger(__name__)


async def process_submission(envelope: SubmissionEnvelope) -> ScoringResultResponse:
    """
    Orchestrate Stage 1 validation → Stage 2+4 scoring → governance requirements.

    Steps
    -----
    1. Resolve project config from registry (ProjectNotFoundError → 422 at API layer).
    2. Validate raw payload against project schema (ValidationError → 422).
    3. [STUB] Persist submission — not implemented until DB layer is added.
    4. Run scoring pipeline → PipelineResult.
    5. Determine governance requirements → RequiredValidations.
    6. [STUB] Persist scoring result — not implemented until DB layer is added.
    7. Return ScoringResultResponse with status='pending_finalization'.

    Raises
    ------
    ProjectNotFoundError
        If ``envelope.metadata.project_id`` is not registered.
    pydantic.ValidationError
        If the raw payload fails Stage 1 schema validation.
    """
    project_id = envelope.metadata.project_id
    submission_id = envelope.metadata.submission_id

    # Step 1 — resolve project config (raises ProjectNotFoundError on unknown project)
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
    # Build weight lookup once (O(N)) to avoid O(N²) double-scan per rule.
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
        status="pending_review",
        confidence_score=pipeline_result.total_score,
        breakdown=breakdown,
        required_validations=required,
        thresholds=config.thresholds,
        created_at=datetime.now(timezone.utc),
    )

async def supersede_submission(
    submission_id: UUID,
    request: SupersedeRequest,
) -> SupersedeResponse:
    """
    Mark a submission as superseded by a newer corrected one.

    Steps
    -----
    1. [STUB] Load submission from DB — not implemented until Sprint 3.
    2. [STUB] Transition status to 'superseded'.
    3. Return SupersedeResponse.

    Raises
    ------
    NotImplementedYetError
        Always, until the DB persistence layer is implemented.
    """
    # Step 1 — [STUB] load submission from DB
    # TODO: load submission from DB, validate it exists and is not already finalized
    raise NotImplementedYetError(
        f"supersede_submission({submission_id!s})"
    )
