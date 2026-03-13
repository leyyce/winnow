"""
Supersede endpoint — PATCH /api/v1/submissions/{id}/supersede.

Used exclusively to mark a submission as superseded by a newer corrected one.
When a user edits domain data in the client (e.g., corrects a tree measurement),
the client sends a brand-new submission (new UUID) and then calls this endpoint
to retire the old one, preserving a complete audit trail without mutation.

This endpoint is intentionally narrow:
* It ONLY accepts ``status="superseded"`` — the Pydantic schema enforces this
  with a Literal type, returning 422 if any other status value is supplied.
* It does NOT accept ``approved`` or ``rejected`` — those transitions are
  triggered automatically by the Governance Engine's vote-threshold logic
  (POST /submissions/{id}/votes).

Returns 501 Not Implemented until the DB persistence layer is added (Sprint 3).

References
----------
* API contract:          docs/architecture/03_api_contracts.md §3b
* Immutable submissions: docs/architecture/02_architecture_patterns.md §4
* Rule 10:               .junie/AGENTS.md — Immutable Submissions & Append-Only State
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.supersede import SupersedeRequest, SupersedeResponse
from app.services import scoring_service

router = APIRouter(tags=["submissions"])


@router.patch(
    "/submissions/{submission_id}/supersede",
    response_model=SupersedeResponse,
    status_code=200,
    summary="Mark a submission as superseded by a newer corrected one",
    description=(
        "Retires an old submission by transitioning it to 'superseded' state. "
        "Only accepts status='superseded' — any other value returns 422. "
        "Approved/rejected transitions are managed automatically by the "
        "Governance Engine vote-threshold logic. "
        "Requires the DB persistence layer (Sprint 3)."
    ),
)
async def supersede_submission(
    submission_id: UUID,
    request: SupersedeRequest,
    db: AsyncSession = Depends(get_db),
) -> SupersedeResponse:
    """Retire a submission and record the UUID of the replacement."""
    return await scoring_service.supersede_submission(submission_id, request, db)
