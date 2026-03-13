"""
Governance service — DB-backed review task queue.

Replaces the Sprint 2.6 ``pending_submissions: list = []`` stub with a real
DB query.  Winnow is the Governance Authority: it queries all
``pending_review`` submissions for a project, evaluates reviewer eligibility
for each via the governance policy, then returns a paginated list of
``TaskItem`` objects the reviewer is authorised to process.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
* API contract: docs/architecture/03_api_contracts.md §6
* Database:     docs/architecture/05_database_design.md §6 step 9 (task list)
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scoring_result import ScoringResult
from app.models.submission import Submission, SubmissionStatus
from app.registry.manager import registry
from app.schemas.results import RequiredValidations
from app.schemas.tasks import TaskItem, TaskListResponse

logger = logging.getLogger(__name__)


async def get_available_tasks(
    project_id: str,
    user_trust: int,
    user_role: str,
    db: AsyncSession,
    page: int = 1,
    per_page: int = 20,
) -> TaskListResponse:
    """
    Return the paginated list of pending submissions the reviewer may process.

    Steps
    -----
    1. Resolve project config (ProjectNotFoundError → 422 at API layer).
    2. Query all ``pending_review`` submissions for ``project_id`` joined with
       their ``ScoringResult`` rows.
    3. Filter via ``governance_policy.is_eligible_reviewer()`` for each row.
    4. Paginate and return ``TaskListResponse``.

    Parameters
    ----------
    project_id:
        Registered project identifier.
    user_trust:
        Reviewer's current trust level.
    user_role:
        Reviewer's role string (e.g. ``"citizen"``, ``"expert"``).
    db:
        Async SQLAlchemy session injected by the ``get_db`` FastAPI dependency.
    page:
        1-based page number.
    per_page:
        Maximum tasks per page.

    Raises
    ------
    ProjectNotFoundError
        If ``project_id`` is not registered in the registry.
    """
    # Step 1 — resolve project config
    config = registry.get_config(project_id)

    # Step 2 — query all pending submissions with their scoring results
    stmt = (
        select(Submission, ScoringResult)
        .join(ScoringResult, ScoringResult.submission_id == Submission.submission_id)
        .where(
            Submission.project_id == project_id,
            Submission.status == SubmissionStatus.PENDING_REVIEW,
        )
        .order_by(Submission.created_at.asc())  # oldest first for fair queueing
    )
    rows = (await db.execute(stmt)).all()

    # Step 3 — filter by reviewer eligibility
    eligible: list[TaskItem] = []
    for submission, sr_row in rows:
        required = RequiredValidations.model_validate(sr_row.required_validations)
        if config.governance_policy.is_eligible_reviewer(
            submission_score=sr_row.confidence_score,
            submission_requirements=required,
            reviewer_trust=user_trust,
            reviewer_role=user_role,
        ):
            eligible.append(
                TaskItem(
                    submission_id=submission.submission_id,
                    project_id=submission.project_id,
                    submission_type=submission.submission_type,
                    confidence_score=sr_row.confidence_score,
                    review_tier=required.review_tier,
                    required_validations=required,
                    submitted_at=submission.created_at,
                )
            )

    # Step 4 — paginate
    total = len(eligible)
    start = (page - 1) * per_page
    page_items = eligible[start : start + per_page]

    logger.info(
        "Task list requested",
        extra={
            "project_id": project_id,
            "user_trust": user_trust,
            "user_role": user_role,
            "eligible_total": total,
            "page": page,
        },
    )

    return TaskListResponse(
        tasks=page_items,
        total=total,
        page=page,
        per_page=per_page,
    )
