"""
Results endpoint — GET /api/v1/results/{id}.

Stub returning 501 Not Implemented until the DB persistence layer is added.
Once DB is available this endpoint will return the stored ScoringResultResponse
for a given submission UUID.

References
----------
* API contract: docs/architecture/03_api_contracts.md §7
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["results"])


@router.get(
    "/results/{submission_id}",
    status_code=200,
    summary="Retrieve scoring result by submission ID",
    description=(
        "Returns the stored ScoringResultResponse for the given submission UUID. "
        "Returns 501 Not Implemented until the DB persistence layer is added."
    ),
)
async def get_result(submission_id: UUID) -> None:
    """Stub — returns 501 until the DB layer is implemented."""
    raise HTTPException(
        status_code=501,
        detail=(
            f"GET /api/v1/results/{submission_id} is not yet implemented. "
            "The database persistence layer has not been added."
        ),
    )
