"""
Submissions endpoints.

* POST   /api/v1/submissions              — score and persist a new submission
* PATCH  /api/v1/submissions/{id}/withdraw — user withdraws a pending submission
* PATCH  /api/v1/submissions/{id}/override — admin forces a terminal state

References
----------
* API contract: docs/architecture/03_api_contracts.md §1, §2, §3
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.envelope import SubmissionEnvelope
from app.schemas.results import ScoringResultResponse
from app.schemas.voting import VoteRequest
from app.services import scoring_service, submission_service, voting_service

router = APIRouter(tags=["submissions"])


@router.post(
    "/submissions",
    response_model=ScoringResultResponse,
    status_code=201,
    summary="Submit a measurement for scoring",
    description=(
        "Accepts a project-specific submission envelope. "
        "Runs Stage 1 (Pydantic validation) then Stage 2+4 (scoring pipeline). "
        "Returns the Confidence Score, breakdown, and governance requirements. "
        "Idempotent: re-submitting the same submission_id returns the stored result. "
        "Auto-supersedes any prior pending submission for the same "
        "(project, entity, measurement) triplet. Returns 409 if the prior "
        "submission is already in a terminal state."
    ),
)
async def create_submission(
    envelope: SubmissionEnvelope,
    db: AsyncSession = Depends(get_db),
) -> ScoringResultResponse:
    """Score a submission, persist it, and return the result (201 Created)."""
    return await submission_service.submit(envelope, db)


@router.patch(
    "/submissions/{submission_id}/withdraw",
    response_model=ScoringResultResponse,
    status_code=200,
    summary="Withdraw a pending submission",
    description=(
        "Allows a user to withdraw their own pending submission before it is "
        "reviewed. Appends a new ScoringResult row with status 'voided'. "
        "Only permitted when the current status is 'pending_review'. "
        "Returns 409 if the submission is already in a terminal state."
    ),
)
async def withdraw_submission(
    submission_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ScoringResultResponse:
    """Withdraw a pending submission — transitions status to 'voided'."""
    return await scoring_service.withdraw_submission(submission_id, db)


@router.patch(
    "/submissions/{submission_id}/override",
    response_model=ScoringResultResponse,
    status_code=200,
    summary="Admin override — force a submission to a terminal state",
    description=(
        "Admin-only endpoint. Casts an override vote that immediately forces "
        "the submission into 'approved' or 'rejected'. "
        "Bypasses normal eligibility and duplicate-vote checks. "
        "The override vote is recorded with is_override=True, creating an "
        "auditable chain from the new ScoringResult to the acting admin. "
        "Only permitted when the current status is 'pending_review'."
    ),
)
async def override_submission(
    submission_id: UUID,
    request: VoteRequest,
    db: AsyncSession = Depends(get_db),
) -> ScoringResultResponse:
    """
    Force a submission to a terminal state via admin override vote.

    The ``request.is_override`` flag must be ``True``; if not, this endpoint
    behaves as a regular vote cast (eligibility rules apply).
    """
    await voting_service.cast_vote(submission_id, request, db, is_override=True)
    return await scoring_service.get_submission_result(submission_id, db)
