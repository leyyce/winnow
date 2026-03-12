"""
Results endpoint — GET /api/v1/results/{id}.

Stub: raises ``NotImplementedYetError`` (→ 501 RFC 7807 response) until the
DB persistence layer is added. Once the DB layer is in place this endpoint
will return the stored ``ScoringResultResponse`` for the given submission UUID.

References
----------
* API contract: docs/architecture/03_api_contracts.md §7
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.core.exceptions import NotImplementedYetError
from app.schemas.results import ScoringResultResponse

router = APIRouter(tags=["results"])


@router.get(
    "/results/{submission_id}",
    response_model=ScoringResultResponse,
    status_code=200,
    summary="Retrieve scoring result by submission ID",
    description=(
        "Returns the stored ``ScoringResultResponse`` for the given submission UUID. "
        "Requires the DB persistence layer (Phase 2)."
    ),
)
async def get_result(submission_id: UUID) -> ScoringResultResponse:
    """Stub — raises NotImplementedYetError until the DB layer is implemented."""
    raise NotImplementedYetError(f"GET /api/v1/results/{submission_id}")
