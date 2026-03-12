"""
Submissions endpoint — POST /api/v1/submissions.

Accepts a ``SubmissionEnvelope``, delegates to the scoring service, and
returns a ``ScoringResultResponse`` (201 Created) on success.

References
----------
* API contract: docs/architecture/03_api_contracts.md §1 & §2
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.envelope import SubmissionEnvelope
from app.schemas.results import ScoringResultResponse
from app.services import submission_service

router = APIRouter(tags=["submissions"])


@router.post(
    "/submissions",
    response_model=ScoringResultResponse,
    status_code=201,
    summary="Submit a measurement for scoring",
    description=(
        "Accepts a project-specific submission envelope. "
        "Runs Stage 1 (Pydantic validation) then Stage 2+4 (scoring pipeline). "
        "Returns the Confidence Score, breakdown, and governance requirements."
    ),
)
async def create_submission(
    envelope: SubmissionEnvelope,
) -> ScoringResultResponse:
    """Score a submission and return the result (201 Created)."""
    return await submission_service.submit(envelope)
