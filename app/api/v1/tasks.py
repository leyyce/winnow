"""
Tasks endpoint — GET /api/v1/tasks/available.

Returns the paginated list of pending submissions the requesting reviewer is
eligible to process, as determined by Winnow's governance policy.

References
----------
* API contract: docs/architecture/03_api_contracts.md §6
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.schemas.tasks import TaskListResponse
from app.services import governance_service

router = APIRouter(tags=["tasks"])


@router.get(
    "/tasks/available",
    response_model=TaskListResponse,
    status_code=200,
    summary="List review tasks available to the requesting reviewer",
    description=(
        "Winnow is the Governance Authority: it filters pending submissions "
        "based on the reviewer's trust level and role, returning only those "
        "the reviewer is eligible to process."
    ),
)
async def get_available_tasks(
    project_id: str = Query(
        min_length=1,
        description="Registered project identifier, e.g. 'tree-app'.",
    ),
    user_trust: int = Query(
        ge=0,
        description="Reviewer's current trust level.",
    ),
    user_role: str = Query(
        default="citizen",
        min_length=1,
        description="Reviewer's role in the client application.",
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="Page number (1-based).",
    ),
    per_page: int = Query(
        default=20,
        ge=1,
        le=settings.TASK_PAGE_SIZE_MAX,
        description="Maximum number of tasks per page.",
    ),
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    """Return available review tasks for the given reviewer."""
    return await governance_service.get_available_tasks(
        project_id=project_id,
        user_trust=user_trust,
        user_role=user_role,
        db=db,
        page=page,
        per_page=per_page,
    )
