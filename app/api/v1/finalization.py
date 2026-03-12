"""
Finalization endpoint — PATCH /api/v1/submissions/{id}/final-status.

Delivers the ground-truth outcome to Winnow after expert or community review.
Triggers the Trust Advisor (Stage 4 output) and returns a FinalizationResponse.

Note: This endpoint returns 501 Not Implemented until the DB persistence layer
is added (stub behaviour is by design for Phase 1 — see scoring_service.py).

References
----------
* API contract: docs/architecture/03_api_contracts.md §3b
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.schemas.finalization import FinalizationRequest, FinalizationResponse
from app.services import scoring_service

router = APIRouter(tags=["submissions"])


@router.patch(
    "/submissions/{submission_id}/final-status",
    response_model=FinalizationResponse,
    status_code=200,
    summary="Finalize a submission with a ground-truth outcome",
    description=(
        "Closes the feedback loop by delivering the expert/community decision. "
        "Computes a trust adjustment recommendation (Stage 4 output). "
        "Returns 501 until the DB persistence layer is implemented."
    ),
)
async def finalize_submission(
    submission_id: UUID,
    request: FinalizationRequest,
) -> FinalizationResponse:
    """Finalize a submission and return the trust adjustment recommendation."""
    return await scoring_service.finalize_submission(submission_id, request)
