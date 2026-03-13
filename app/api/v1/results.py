"""
Results endpoint — GET /api/v1/results/{submission_id}.

Returns the stored ``ScoringResultResponse`` for the given submission UUID.
Previously a 501 stub — now backed by the DB persistence layer (Sprint 3).

References
----------
* API contract: docs/architecture/03_api_contracts.md §7
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.results import ScoringResultResponse
from app.services import scoring_service

router = APIRouter(tags=["results"])


@router.get(
    "/results/{submission_id}",
    response_model=ScoringResultResponse,
    status_code=200,
    summary="Retrieve scoring result by submission ID",
    description=(
        "Returns the stored ``ScoringResultResponse`` for the given submission UUID. "
        "Returns 404 if the submission is not found."
    ),
)
async def get_result(
    submission_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ScoringResultResponse:
    """Return the persisted scoring result for the given submission."""
    return await scoring_service.get_submission_result(submission_id, db)
