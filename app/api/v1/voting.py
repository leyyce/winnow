"""
Voting endpoint — POST /api/v1/submissions/{id}/votes.

Accepts individual reviewer votes and delegates to the voting service for
eligibility checks, duplicate prevention, threshold evaluation, and
auto-finalization.

References
----------
* API contract: docs/architecture/03_api_contracts.md §9
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.voting import VoteRequest, VoteResponse
from app.services import voting_service

router = APIRouter(tags=["voting"])


@router.post(
    "/submissions/{submission_id}/votes",
    response_model=VoteResponse,
    status_code=201,
    summary="Cast a vote on a submission",
    description=(
        "Records a reviewer's vote (approve/reject) on a pending submission. "
        "Enforces eligibility, prevents duplicate votes, and automatically "
        "finalizes the submission when the governance threshold is met."
    ),
)
async def cast_vote(
    submission_id: UUID,
    request: VoteRequest,
    db: AsyncSession = Depends(get_db),
) -> VoteResponse:
    """Record a vote and return the current voting state."""
    return await voting_service.cast_vote(submission_id, request, db)
